"""Firefox-backed transport for classroom.its.ac.id.

A persistent Firefox profile (``.firefox-profile/``) is the ONLY credential
store. Cookies are never read from disk, copied, or printed. All HTTP to
Moodle happens in-page (``page.evaluate`` + ``fetch``) so the browser's
cookie jar is used directly.

Public contract (pinned; service.py / assign.py / submitter.py code against
it):

- ``connect(headed, profile_dir) -> FirefoxSession``
- ``FirefoxSession``: ``page``, ``browser`` (the BrowserContext),
  ``sesskey()``, ``user_id()``, ``ajax()``, ``fetch_html()``, ``close()``.
"""

import json
from typing import Any

from .errors import BrowserNotConnected, LoginRequired, MoodleError

BASE_URL = "https://classroom.its.ac.id"

_PROFILE_BUSY_MARKERS = (
    "browser is already running",
    "profile",
    "lock",
    "ProcessError",
)

# In-page fetch for an AJAX service call. Runs inside the page so the
# browser's cookies are sent (credentials: "same-origin").
_AJAX_JS = """
async ({ url, body }) => {
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body,
    });
    const data = await res.json();
    return { status: res.status, body: data, error: null };
  } catch (e) {
    return { status: 0, body: null, error: String(e) };
  }
}
"""

# In-page fetch for a plain HTML page, reporting the FINAL url so login
# redirects can be detected.
_FETCH_HTML_JS = """
async ({ url }) => {
  try {
    const res = await fetch(url, { credentials: "same-origin" });
    const text = await res.text();
    return { url: res.url, status: res.status, text, error: null };
  } catch (e) {
    return { url: "", status: 0, text: null, error: String(e) };
  }
}
"""


class FirefoxSession:
    """A live Playwright Firefox context bound to the persistent profile.

    ``self.browser`` is the BrowserContext returned by
    ``launch_persistent_context`` (there is no separate Browser object for
    persistent contexts). ``self.page`` is the page used for all in-page
    evaluations.
    """

    def __init__(self, page: Any, browser: Any, playwright: Any) -> None:
        self.page = page
        self.browser = browser
        self._pw = playwright
        self._sesskey: str | None = None

    async def _goto_mypage(self) -> None:
        """Navigate to /my/ (domcontentloaded), tolerating failure."""
        try:
            await self.page.goto(BASE_URL + "/my/", wait_until="domcontentloaded")
        except Exception:
            # Offline or slow: stay on whatever page we have; callers detect
            # missing login state via sesskey()/user_id().
            pass

    async def sesskey(self) -> str:
        """Return the Moodle sesskey (``M.cfg.sesskey``), cached.

        If absent on the current page, navigates to /my/ and retries once
        before raising LoginRequired.
        """
        if self._sesskey:
            return self._sesskey
        sk = await self.page.evaluate("() => window.M?.cfg?.sesskey")
        if not sk:
            await self._goto_mypage()
            sk = await self.page.evaluate("() => window.M?.cfg?.sesskey")
        if not sk:
            raise LoginRequired("No sesskey on /my/: not logged in.")
        self._sesskey = sk
        return sk

    async def user_id(self) -> int:
        """Return the logged-in Moodle user id (``M.cfg.userId``).

        Raises LoginRequired when the id is 0/None (not logged in).
        """
        uid = await self.page.evaluate("() => window.M?.cfg?.userId")
        if not uid:
            raise LoginRequired(
                "M.cfg.userId is 0: not logged in.",
            )
        return int(uid)

    async def ajax(self, method: str, args: dict) -> Any:
        """Call a Moodle AJAX web-service function in-page.

        Posts a single-entry batch to ``/lib/ajax/service.php`` with the
        cached sesskey. On a batch-entry error that mentions the
        sesskey/session, clears the cache and retries ONCE with a fresh
        sesskey; any other error raises MoodleError. Returns
        ``body[0].data``.
        """
        data = await self._ajax_once(method, args)
        if isinstance(data, dict) and data.get("error"):
            message = str(data.get("exception") or data.get("message") or data)
            lowered = message.lower()
            if "sesskey" in lowered or "session" in lowered:
                self._sesskey = None
                data = await self._ajax_once(method, args)
                if isinstance(data, dict) and data.get("error"):
                    message = str(data.get("exception") or data.get("message") or data)
                    raise MoodleError(f"Moodle AJAX {method} failed: {message}")
            else:
                raise MoodleError(f"Moodle AJAX {method} failed: {message}")
        return data.get("data") if isinstance(data, dict) else data

    async def _ajax_once(self, method: str, args: dict) -> Any:
        """One AJAX attempt; returns ``body[0]`` or raises MoodleError."""
        sk = await self.sesskey()
        url = f"{BASE_URL}/lib/ajax/service.php?sesskey={sk}&info={method}"
        body = json.dumps([{"index": 0, "methodname": method, "args": args}])
        result = await self.page.evaluate(_AJAX_JS, {"url": url, "body": body})
        if result.get("error") or result.get("body") is None or result.get("status", 0) >= 400:
            raise MoodleError(
                f"Moodle AJAX {method} transport failure: {result.get('error') or 'null body'}"
            )
        # /lib/ajax/service.php returns the batch entries as a top-level
        # JSON array; some Moodle setups wrap them in {"body": [...]}.
        payload = result["body"]
        if isinstance(payload, list):
            entries = payload
        else:
            entries = payload.get("body") or []
        if not entries:
            raise MoodleError(f"Moodle AJAX {method} returned an empty batch.")
        entry = entries[0]
        if isinstance(entry, dict) and entry.get("error"):
            # Sesskey/session errors must still reach ajax()'s retry wrapper
            # as a returned dict; any other error surfaces here with its
            # errorcode instead of being masked as an empty batch.
            message = str(entry.get("exception") or entry.get("message") or entry)
            if not ("sesskey" in message.lower() or "session" in message.lower()):
                raise MoodleError(
                    f"Moodle AJAX {method}: "
                    f"{entry.get('errorcode') or 'error'}: {message}"
                )
        return entry

    async def fetch_html(self, path: str) -> str:
        """Fetch an HTML page in-page with same-origin credentials.

        ``path`` may be absolute ("/course/view.php?id=1") or a full URL.
        Raises LoginRequired when the final URL lands on /login/.
        """
        url = path if path.startswith("http") else BASE_URL + (path if path.startswith("/") else "/" + path)
        result = await self.page.evaluate(_FETCH_HTML_JS, {"url": url})
        if result.get("error"):
            raise MoodleError(f"fetch_html({path}) failed: {result['error']}")
        if "/login/" in result.get("url", ""):
            raise LoginRequired(f"fetch_html({path}) redirected to the login page.")
        return result["text"]

    async def close(self) -> None:
        """Tear down the persistent context and stop Playwright."""
        if self.browser is not None:
            try:
                await self.browser.close()
            except Exception:
                pass
            self.browser = None
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None
        self.page = None


def _is_profile_busy(exc: Exception) -> bool:
    """Heuristically detect 'profile locked / Firefox already running'."""
    text = f"{type(exc).__name__} {exc}".lower()
    return any(marker.lower() in text for marker in _PROFILE_BUSY_MARKERS)


async def connect(headed: bool = False, profile_dir: str = ".firefox-profile") -> FirefoxSession:
    """Launch Firefox with the persistent profile and open /my/.

    Raises BrowserNotConnected(errorcode="profile_busy") when the profile is
    locked by another Firefox instance, or
    BrowserNotConnected(errorcode="browser_not_connected") for any other
    launch failure.
    """
    # Imported lazily so this module compiles (and imports) without
    # playwright installed; the runtime dependency is resolved by the
    # caller's environment.
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    try:
        context = await pw.firefox.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=not headed,
        )
    except Exception as exc:
        await pw.stop()
        if _is_profile_busy(exc):
            raise BrowserNotConnected(
                f"Firefox profile '{profile_dir}' is already in use: {exc}",
                errorcode="profile_busy",
                hint="Close the other instance using this profile, then retry.",
            ) from exc
        raise BrowserNotConnected(
            f"Could not launch Firefox (profile '{profile_dir}'): {exc}",
            errorcode="browser_not_connected",
            hint="Check the profile path and permissions, then retry.",
        ) from exc

    page = context.pages[0] if context.pages else await context.new_page()
    session = FirefoxSession(page=page, browser=context, playwright=pw)
    await session._goto_mypage()
    return session
