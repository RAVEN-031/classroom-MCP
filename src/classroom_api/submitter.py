"""Submission flow: drive the real mod_assign UI on the FirefoxSession page.

The session's own page (persistent profile, live cookies) navigates the
assignment flow: "Add submission" -> fill text / attach files -> "Save
changes" -> optional "Submit assignment" confirmation -> server-side verify
via ``submission_status()``.

Bilingual (EN/ID) label lists cover the English and Indonesian Moodle packs.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from .assign import submission_status
from .browser import BASE_URL, FirefoxSession
from .errors import SubmissionError, TranscriptNotFound, VerificationError

ADD_SUBMISSION_TEXTS = ["Add submission", "Tambah pengumpulan"]
SAVE_TEXTS = ["Save changes", "Simpan perubahan"]
SUBMIT_TEXTS = ["Submit assignment", "Kumpulkan submission"]
MODAL_CONFIRM_TEXTS = ["Continue", "Lanjutkan", "Confirm", "Ya"]

EDITOR_SELECTORS = [
    'div[id*="onlineeditor"] div[contenteditable="true"]',
    '[id^="id_"][id*="editor_editable"]',
]


async def submit(
    s: FirefoxSession,
    *,
    cmid: int,
    file_paths: list[str],
    text: str | None,
    note: str | None,
) -> dict:
    """Push a submission through the real UI, then verify on the server.

    Raises SubmissionError / TranscriptNotFound before or during the flow,
    and VerificationError when the server does not report the submission
    afterwards.
    """
    files = [os.path.expanduser(fp) for fp in file_paths if fp and fp.strip()]
    if not files and not (text and text.strip()):
        raise SubmissionError("Provide files and/or text; refusing empty submission.")
    for fp in files:
        if not os.path.isfile(fp):
            raise TranscriptNotFound(f"File not found: {fp}")

    page = s.page
    if page is None:
        raise SubmissionError("FirefoxSession has no live page.")
    try:
        await page.goto(f"{BASE_URL}/mod/assign/view.php?id={cmid}", wait_until="domcontentloaded")
    except Exception as exc:
        raise SubmissionError(f"Could not open assignment {cmid}: {exc}") from exc

    if await _doc_has_text(page, ADD_SUBMISSION_TEXTS):
        await _click_first(page, ADD_SUBMISSION_TEXTS)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass

    if text:
        if not await _fill_online_text(page, text):
            raise SubmissionError("Online text editor not found (assignment may be file-only).")

    for fp in files:
        if not await _upload_file(page, fp):
            raise SubmissionError(f"Could not attach {fp} to the file manager.")

    if note:
        # Best-effort: submission comments editor, if present.
        await _fill_any_editor(page, note)

    if not await _click_first(page, SAVE_TEXTS):
        raise SubmissionError("Could not find 'Save changes'. Check selectors.")
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=20000)
    except Exception:
        pass

    confirmed = False
    if await _doc_has_text(page, SUBMIT_TEXTS):
        await _click_first(page, SUBMIT_TEXTS)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        confirmed = await _confirm_dialog(page)
        if not confirmed:
            await _click_first(page, SUBMIT_TEXTS)
    else:
        confirmed = await _doc_has_text(page, ["Submitted for grading"])

    # Authoritative server-side check; do not trust the page alone.
    status = await submission_status(s, cmid)
    if status.get("status") not in ("submitted", "draft"):
        raise VerificationError(f"After submit, server reports {status.get('status')!r}.")
    return {
        "cmid": cmid,
        "confirmed": bool(confirmed),
        "server_status": status,
    }


async def _doc_has_text(page: Any, candidates: list[str]) -> bool:
    """True when any candidate label appears in the page text."""
    contents = await page.content()
    lowered = contents.lower()
    return any(t.lower() in lowered for t in candidates)


async def _click_first(page: Any, texts: list[str]) -> bool:
    """Click the first visible button/link matching any bilingual label."""
    for t in texts:
        for loc in (page.get_by_role("button", name=t).first, page.get_by_role("link", name=t).first):
            try:
                if await loc.count() and await loc.first.is_visible():
                    await loc.click(timeout=5000)
                    return True
            except Exception:
                continue
    return False


async def _confirm_dialog(page: Any) -> bool:
    """Tick visible required checkboxes, then click the confirm button."""
    boxes = page.locator('form input[type="checkbox"][required], form input[type="checkbox"]')
    try:
        cnt = await boxes.count()
        for i in range(min(cnt, 10)):
            box = boxes.nth(i)
            if await box.is_visible():
                await box.check()
    except Exception:
        pass
    return await _click_first(page, MODAL_CONFIRM_TEXTS)


async def _fill_online_text(page: Any, text: str) -> bool:
    """Fill the online-text contenteditable editor (EN/ID themes)."""
    for sel in EDITOR_SELECTORS:
        loc = page.locator(sel).first
        try:
            if await loc.count():
                await loc.fill(text)
                return True
        except Exception:
            continue
    return False


async def _fill_any_editor(page: Any, text: str) -> bool:
    """Best-effort fill of any contenteditable (submission comments)."""
    try:
        ce = page.locator('div[contenteditable="true"]').first
        if await ce.count():
            await ce.fill(text)
            return True
    except Exception:
        pass
    return False


async def _upload_file(page: Any, file_path: str) -> bool:
    """Attach a file via any input[type=file] (first working wins)."""
    inputs = page.locator('input[type="file"]')
    try:
        n = await inputs.count()
    except Exception:
        return False
    for i in range(min(n, 8)):
        try:
            await inputs.nth(i).set_input_files(file_path)
            await asyncio.sleep(1.5)
            return True
        except Exception:
            continue
    return False
