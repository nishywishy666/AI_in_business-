# 0007 — The UI hash manifest read a Windows checkout as an edited design

**Date:** 2026-09-14
**Area:** frontend / tests

## What happened
`tests/test_dashboard_ui.py::test_ui_files_are_the_recorded_export` failed on a clean tree: the sha256
of `Uncle Tony Overlord Dashboard.dc.html` on disk did not match `tests/fixtures/ui_manifest.json`,
even though `git status` was clean and `git log -- UI/` showed no change since the export was committed.
Two sibling failures were in the same run: the anchor test rejected the two `dc-ai-line` patches
(their replacement *appends* to the anchor, so the anchor text legitimately survives), and
`test_refresh_trends_rereads_the_brief_now` asserted an empty state on the module-scoped client after
an earlier test had already run a scan.

## Root cause
`core.autocrlf=true` on this machine rewrites LF to CRLF at checkout (`git ls-files --eol` shows
`i/lf w/crlf`). The manifest was written from an LF checkout, and `_sha()` hashed raw bytes, so the
same file produced a different digest on Windows. The other two were tests written in a session
without a venv (plans 0010/0011 say "not run") that were never executed.

## Fix
- `_sha()` normalises `\r\n` to `\n` before hashing; the manifest digests stay valid on both line endings.
- The anchor-removed check skips a patch whose replacement contains its own anchor.
- The refresh test builds its own app on `tmp_path` instead of sharing the module client.

## How to avoid next time
Hash text fixtures after normalising line endings, or add a `.gitattributes` `eol=lf` rule for any
folder pinned by hash. Any test written without being run must be run before it is marked Done in
its plan — `uv run pytest` is the gate, not `node --check`.

## Recurred (2026-09-14, plan 0013)
Same root cause, different symptom: exact-substring edits scripted with `\n` in the search text found zero matches,
because every tracked file is CRLF on disk here. `grep -q $'\r'` in Git Bash even reported them as LF. Any scripted
edit in this checkout must normalise `\r\n` → `\n` before matching and write back with the file's own newline
(`dashboard/ui.py::render_page` already does exactly this for its template anchors).
