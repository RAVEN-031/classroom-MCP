# Issues — firefox-classroom-agent-submission

Problems and gotchas encountered during work on this plan.

_Auto-scaffolded by /ulw-execute. Append new entries below - never overwrite._

---
# Issues

- Submission selectors uncalibrated until Phase 5 live run; RemUI may rename EN/ID labels.
## 2026-10-24 Atlas
- BUG (service.login): poll loop calls session.user_id() expecting return 0; contract says user_id() RAISES LoginRequired when userId==0 -> login() must catch LoginRequired inside the loop and continue polling until user_id() succeeds.

## 2026-10-24 Worker 1 (Transport)
- state.py + client.py deleted; auth.py (16, 88-89), assign.py (13, 21, 56, 184), submitter.py (22, 24) still reference them -> runtime ImportError until those workers land their rewrites. auth.py's fate (delete vs rewrite) is not covered by any visible checkbox — Orchestrator to confirm.
- sesskey retry in ajax() re-navigates only if sesskey is gone from the page (via sesskey() internal refresh); if the page is stale but sesskey still present, retry reuses the same key. Acceptable per contract wording.

## 2026-10-24 Worker 2 (Data layer)
- assign.py/submitter.py now import only browser/errors (no .state/.client); runtime ImportErrors from learnings entry "Worker 1" resolved for these files.
- service.login() user_id()-raises bug STILL OPEN (owned by another dispatch).
- Selectors (EDITOR_SELECTORS, file inputs, confirm labels) uncalibrated until Phase 5 live run; RemUI may rename labels.
