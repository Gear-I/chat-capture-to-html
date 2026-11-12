# CLAUDE.md

Rules for working in this repository. They apply to Claude and to human
contributors alike. If a request conflicts with a rule here, say so and ask
before going ahead.

## 1. What this project is

`chat-capture-to-html` turns a UFADE Chat capture folder
(`screenshots/<app>/<chat>/`) into an HTML chat. It checks every PNG against
its `.txt` SHA-256, OCRs offline, merges overlapping shots, works out the
sender from bubble side and colour, and links each message to its screenshot
and hash.

The output supports a forensic examiner. It does not replace the screenshots,
which are the evidence.

## 2. Evidence integrity

1. **Never write to a capture folder.** Treat input folders as read-only:
   no renaming, re-encoding, re-saving, deleting or adding files, and no
   caches or temporary files inside them.
2. **Hash before use.** Every screenshot is hashed from its bytes on disk
   and compared with its `.txt` file before any result depends on it. Never
   skip, cache across runs, or "fix" a hash value.
3. **Report every failure.** A mismatch, a missing or unreadable hash file,
   an orphaned `.txt`, an undecodable PNG or a skipped shot must appear in
   the report and the exit code. Nothing is dropped silently.
4. **Link, don't copy.** The report links to the original screenshots. It
   never embeds, crops into files, or re-encodes them.
5. **Keep provenance.** Every message keeps the screenshots it came from,
   the shot its text was read from, that shot's SHA-256 and verification
   result, and its OCR confidence.
6. **Don't hide uncertainty.** Gaps between non-overlapping shots, low OCR
   confidence and doubtful sender attribution are shown to the reader, not
   smoothed over. OCR text is a reading aid, never presented as the evidence.

## 3. Offline and private

1. All processing runs locally. No network calls at runtime: no cloud OCR,
   telemetry, update checks or model downloads. OCR models must be installed
   ahead of time (Tesseract language data, or the models bundled in the
   RapidOCR wheel).
2. Real captures contain real people's messages. They must never be
   committed, pasted into issues, PRs, commit messages, logs or test
   fixtures, or sent to any external service. `screenshots/` is git-ignored;
   keep it that way.
3. The examiner's own test data lives outside the repository (for example
   `C:\ufade-test`). Refer to it by path only.

## 4. Test data

1. Tests use synthetic captures only: `tests/make_fixture.py` generates
   them with invented conversations.
2. A bug found on real data gets a synthetic fixture that reproduces it,
   never a copy of the real screenshot.
3. Never skip, weaken or delete a test to make CI pass. Fix the cause.

## 5. Validation levels

Be precise about what a check shows, and never claim more than it shows.

| Level | What it shows |
|-------|---------------|
| Code review / static analysis (CodeQL) | Code quality |
| Unit and end-to-end tests on synthetic captures | Logic works on known input |
| Run on real UFADE captures by the examiner | Behaviour on real apps, devices and themes |

Passing CI is not forensic validation. PR descriptions must say which
levels a change was checked at, and say plainly when it has not been run
on real captures.

## 6. Code

- Python 3.10 or later. Runtime dependencies stay minimal (`numpy`,
  `pillow`, `opencv-python-headless`); new ones need a stated reason.
- Must keep working on Windows (the examiner's platform): use `pathlib`,
  no shell-only tricks, handle paths with spaces and different drives.
- Before pushing, run `python -m pytest` (needs Tesseract with `deu` and
  `eng` for the end-to-end tests).
- Keep the README's options table and "Limits" section in step with
  behaviour.

## 7. Git, commits and pull requests

1. Never push to `main`. Work on a branch and open a pull request.
2. Branch names describe the change and do not contain `claude`.
3. Commit messages, PR titles and descriptions, code comments and docs
   contain no AI attribution: no `Co-Authored-By: Claude`, no
   "Generated with Claude Code", no session links, no model names.
4. Open pull requests as drafts. The repository owner marks them ready
   and merges.
5. GitHub Actions are pinned to full commit SHAs, with read-only
   `permissions` unless a job needs more.
