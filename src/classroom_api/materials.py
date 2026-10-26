"""Course materials (resource / folder / url) via FirefoxSession.

Primary path: ``core_course_get_contents`` over the in-page AJAX transport.
That web service is DISABLED on this deployment (``servicenotavailable``,
proven live 2026-10-26 across all enrolled courses), so on ``MoodleError``
we fall back to scraping ``/course/view.php`` HTML — the fallback is the
path that answers in practice. Each returned dict carries ``"source"``:
``"webservice"`` or ``"html"`` — callers can tell which path answered. The
HTML fallback then enriches ``resource``/``folder`` entries by fetching each
module's own page and lifting out pluginfile URLs (RemUI resource pages
sometimes JS-redirect instead of linking). Enrichment fetches ONE page per
module, so it is capped at ``_ENRICH_PAGE_CAP`` (~25) module pages per call;
remaining entries keep ``contents: []``.

Credentials never leave the browser: cookies are never read, copied, or
printed, and no sesskey appears in any returned dict or error message. No
dates are handled here, so there is no WIB/timezone conversion either.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup

from .browser import BASE_URL, FirefoxSession
from .errors import ClassroomError, LoginRequired, MoodleError

_MATERIAL_MODNAMES = ("resource", "folder", "url")

# Hard bound on module pages fetched per list_materials() call (enrichment
# is page-fetch-per-module; a big course must not turn the listing into a
# crawl). Entries past the cap keep contents: [].
_ENRICH_PAGE_CAP = 25
_ENRICH_FETCH_TIMEOUT = 20.0

# JS-redirect patterns on RemUI resource pages: location.replace('...') and
# window.location = '...' pointing at a pluginfile.php URL.
_PLUGINFILE_JS_RE = re.compile(
    r"""location(?:\.replace\s*\(\s*|\.href\s*=\s*|\s*=\s*)["']([^"']*pluginfile\.php[^"']*)["']""",
    re.IGNORECASE,
)

# In-page fetch returning BYTES as base64 (page.evaluate cannot return raw
# bytes). Same credential discipline as browser.py's _FETCH_HTML_JS.
_FETCH_BYTES_JS = """
async ({ url }) => {
  try {
    const res = await fetch(url, { credentials: "include" });
    if (!res.ok) {
      return { status: res.status, b64: null, error: null };
    }
    const buf = await res.arrayBuffer();
    if (buf.byteLength === 0) {
      return { status: res.status, b64: null, error: "empty body" };
    }
    let binary = "";
    const bytes = new Uint8Array(buf);
    const chunk = 32768;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return { status: res.status, b64: btoa(binary), error: null };
  } catch (e) {
    return { status: 0, b64: null, error: String(e) };
  }
}
"""


def _normalize_ws_module(mod: dict, section_name: str) -> dict:
    """One WS section module -> the materials dict shape."""
    contents: list[dict] = []
    for c in mod.get("contents") or []:
        contents.append(
            {
                "filename": c.get("filename", ""),
                "filesize": c.get("filesize"),
                "fileurl": c.get("fileurl", ""),
            }
        )
    return {
        "modname": mod.get("modname", ""),
        "id": mod.get("id"),
        "name": mod.get("name", ""),
        "section": section_name,
        "url": mod.get("url") or "",
        "contents": contents,
        "source": "webservice",
    }


def _materials_from_ws(sections: Any) -> list[dict]:
    """core_course_get_contents sections -> materials list."""
    out: list[dict] = []
    for section in sections or []:
        if not isinstance(section, dict):
            continue
        section_name = section.get("name", "")
        for mod in section.get("modules") or []:
            if not isinstance(mod, dict) or mod.get("modname") not in _MATERIAL_MODNAMES:
                continue
            out.append(_normalize_ws_module(mod, section_name))
    return out


def _scrape_materials_html(html: str) -> list[dict]:
    """Extract resource/folder/url activity links from course view HTML."""
    soup = BeautifulSoup(html, "html.parser")
    seen: dict[tuple[str, int], dict] = {}
    for a in soup.find_all("a", href=True):
        path = urlparse(a["href"]).path
        if path not in (
            "/mod/resource/view.php",
            "/mod/folder/view.php",
            "/mod/url/view.php",
        ):
            continue
        modname = path.split("/")[2]
        try:
            mid = int((parse_qs(urlparse(a["href"]).query).get("id") or ["0"])[0])
        except ValueError:
            mid = 0
        name = a.get_text(" ", strip=True)
        if not name:
            continue
        section = ""
        enclosing = a.find_parent(class_=re.compile(r"\bsection\b"))
        if enclosing is not None:
            heading = enclosing.find(["h3", "h4", "h5"])
            if heading is not None:
                section = heading.get_text(" ", strip=True)
        key = (modname, mid)
        if key not in seen or not seen[key]["name"]:
            seen[key] = {
                "modname": modname,
                "id": mid,
                "name": name,
                "section": section,
                "url": a["href"],
                "contents": [],
                "source": "html",
            }
    return list(seen.values())


def _filename_from_url(fileurl: str) -> str:
    """Last path segment of a pluginfile URL, URL-decoded."""
    path = urlparse(fileurl).path
    return unquote(path.rsplit("/", 1)[-1]) if path else ""


def _pluginfile_urls_from_html(html: str) -> list[str]:
    """pluginfile.php URLs from a module page: anchors first, then
    window.location-style JS redirects (RemUI resource pages sometimes
    JS-redirect instead of linking). Order-preserving dedup."""
    urls: list[str] = []
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        if "pluginfile.php" in a["href"]:
            urls.append(a["href"])
    for match in _PLUGINFILE_JS_RE.finditer(html):
        urls.append(match.group(1))
    seen: set[str] = set()
    unique: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


async def _enrich_contents(s: FirefoxSession, materials: list[dict]) -> None:
    """Populate ``contents`` of scraped resource/folder entries in place.

    Fetches each module's own page (sequential, ~20s bound each) and lifts
    out pluginfile URLs. A module-page failure leaves ``contents: []``;
    ``LoginRequired`` propagates. Stops after ``_ENRICH_PAGE_CAP`` module
    pages — remaining entries keep ``contents: []``.
    """
    fetched = 0
    for mod in materials:
        if fetched >= _ENRICH_PAGE_CAP:
            break
        if mod.get("modname") not in ("resource", "folder"):
            continue
        path = f"/mod/{mod['modname']}/view.php?id={mod.get('id', 0)}"
        try:
            html = await asyncio.wait_for(s.fetch_html(path), timeout=_ENRICH_FETCH_TIMEOUT)
        except LoginRequired:
            raise
        except (ClassroomError, asyncio.TimeoutError):
            continue
        fetched += 1
        mod["contents"] = [
            {
                "filename": _filename_from_url(fileurl) or "file",
                "filesize": None,
                "fileurl": fileurl,
            }
            for fileurl in _pluginfile_urls_from_html(html)
        ]


async def list_materials(s: FirefoxSession, course_id: int) -> list[dict]:
    """All resource/folder/url activities of a course.

    Tries the ``core_course_get_contents`` web service first (disabled live
    on this deployment, 2026-10-26); on ``MoodleError`` falls back to
    scraping the course view page.
    ``LoginRequired`` propagates — it must reach the CLI as exit 2, not
    trigger the fallback.
    """
    try:
        sections: Any = await s.ajax("core_course_get_contents", {"courseid": course_id})
        if isinstance(sections, list):
            return _materials_from_ws(sections)
    except MoodleError:
        pass
    html = await s.fetch_html(f"/course/view.php?id={course_id}")
    materials = _scrape_materials_html(html)
    await _enrich_contents(s, materials)
    return materials


async def download(
    s: FirefoxSession,
    fileurl: str,
    out_path: str,
    overwrite: bool = False,
) -> dict:
    """Download a Moodle file URL in-page (browser cookies) to ``out_path``.

    Refuses to overwrite an existing file unless ``overwrite`` is True.
    Returns ``{"path", "size", "url"}``.
    """
    if os.path.exists(out_path) and not overwrite:
        raise MoodleError(
            f"download: refusing to overwrite existing file '{out_path}'",
            hint="Pass overwrite=True to replace it.",
        )
    result = await s.page.evaluate(_FETCH_BYTES_JS, {"url": fileurl})
    if result.get("error"):
        raise MoodleError(f"download({fileurl}) failed: {result['error']}")
    if not result.get("b64"):
        raise MoodleError(
            f"download({fileurl}) failed: HTTP {result.get('status')} or empty body"
        )
    data = base64.b64decode(result["b64"])
    with open(out_path, "wb") as f:
        f.write(data)
    return {"path": out_path, "size": len(data), "url": fileurl}
