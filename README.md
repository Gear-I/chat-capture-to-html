<p align="center"><img src="assets/logo.svg" width="128" height="128" alt="chat-capture-to-html logo: two overlapping screenshots with chat bubbles and a verified hash badge"></p>

# chat-capture-to-html

Turns a UFADE Chat capture folder into a readable chat HTML report. OCRs each screenshot, merges overlapping shots, works out the sender from bubble side and colour, and links every message back to its source screenshot and SHA-256 hash.

## What it does

1. **Reads the capture.** UFADE stores each chat as `screenshots/<app>/<chat>/<chat>_MM_DD_YYYY_HH_MM_SS[_n].png`, with a `.txt` file next to each PNG holding its SHA-256. It also reads a `.json` file next to each PNG instead of, or as well as, the `.txt`; this is a format proposed for UFADE that adds the device (see below). You can point the tool at one chat folder, or at `screenshots/` to process every chat in it.
2. **Checks every hash.** It recomputes the SHA-256 of each PNG and compares it with the `.json` and/or `.txt` file; when both exist, they must agree. Each shot is marked *verified*, *HASH MISMATCH*, *no hash file* or *hash file unreadable*. It also lists hash files that have no PNG.
3. **Lines up the overlapping shots.** On an iPad, where the conversation list stays beside the chat, it first finds the chat pane (the columns that change from shot to shot) and works on that alone; sides are then judged within the chat, and the list is never read as messages. It compares where text and bubble edges sit, not colours, so iOS bubble shading and fixed wallpapers don't disturb it. Shots are first matched using the middle of the screen, which is always chat. Then the chat area is grown outwards over every row that moves with the scroll; the status bar, title and input bar stay put, even when they are translucent. This places every shot on one vertical axis, whether UFADE scrolled up or down. A match is refused if it is ambiguous, or if the screens only look alike (for example repeated automated messages). Where two shots can't be matched, the report shows a visible gap marker instead of guessing; `--diagnose` prints why.
4. **Finds the bubbles and the sender.** A bubble is a smooth area whose colour differs from the background. Right-hand bubbles are the device owner; left-hand bubbles are the other party; centred items are dates and system notices. Small text just above a left-hand bubble (group chats) is used as the sender's name. The report shows each bubble's colour and a colour legend, and warns if one colour appears on both sides.
5. **OCRs offline.** Tesseract, or RapidOCR as a fallback. Each bubble that appears in several shots is read once, from a verified shot where it is fully visible. A bubble taller than the screen is read from each part and joined. Apps that print a time inside the bubble (e.g. WhatsApp) get that time split into its own field.

   With `--no-ocr` nothing is read: each message is shown as it appears in its screenshot (see below), and Tesseract isn't needed.
6. **Writes the HTML.** Every message carries:
   - links to every screenshot it appears in; clicking one opens the shot with the bubble outlined;
   - the file name, SHA-256 and verification result of the shot its text was read from;
   - the OCR confidence.

   At the end is a table of all screenshots with their computed and recorded hashes. Screenshots are linked, not copied or embedded, so the report never holds an altered copy of the evidence.

The exit code is `2` when any hash fails, a hash file is orphaned, or a chat folder mixes devices, `3` when no OCR engine is available, and `0` otherwise.

## Install (Windows)

1. Python 3.10 or newer.
2. Tesseract (not needed with `--no-ocr`): install the [UB Mannheim build](https://github.com/UB-Mannheim/tesseract/releases/tag/v5.4.0.20240606) and tick **German** under *Additional language data*. The tool finds it in `C:\Program Files\Tesseract-OCR` by itself.
3. In this folder:

```bash
py -m pip install .
```

Optional fallback engine (no Tesseract needed; weaker on umlauts and short words):

```bash
py -m pip install .[rapidocr]
```

## Use

```bash
chat-capture-to-html "C:\ufade-test\screenshots" -o C:\ufade-reports
```

This writes one `<app>_<chat>.html` per chat folder. For a single chat you can name the file:

```bash
chat-capture-to-html "C:\ufade-test\screenshots\WhatsApp\Anna" -o C:\ufade-reports\anna.html --me "Owner name" --them "Anna"
```

Keep the report outside the capture folder, so the evidence folder holds only what UFADE wrote. The links stay relative as long as the report and the captures are on the same drive.

| Option | |
|---|---|
| `--me`, `--them` | Labels for right-hand and left-hand bubbles (default: `Device owner` and the chat folder name) |
| `--ocr auto\|tesseract\|rapidocr` | OCR engine (default `auto`: Tesseract if installed) |
| `--no-ocr` | Read no text. Each message, and each date or name line between bubbles, is shown as it appears in its screenshot: the original PNG, linked and displayed through a window around the bubble, so no image is cut out or saved. Order, sender side, merging and hashes work as with OCR. The text is then not searchable, and sender names in group chats and in-bubble times are not split out (they stay visible in the bubble) |
| `--lang deu+eng` | Tesseract languages (default: `deu+eng` where installed) |
| `--tesseract PATH` | Path to `tesseract.exe` if it is somewhere unusual |
| `--top N`, `--bottom N` | Pixels of fixed header/footer to ignore, if detection gets them wrong |
| `--left N`, `--right N` | Pixels on the left/right that are not the chat (for example the iPad conversation list), if detection gets them wrong. Read the divider's position in an image viewer that shows pixel coordinates |
| `--verified-only` | Leave out screenshots whose hash does not verify (they are still listed) |
| `--diagnose` | Print screen size, the chat area found and how each screenshot was lined up, in numbers only (no message text), so the output can be shared when something looks wrong |

## The window

For a window instead of the command line:

```bash
chat-capture-to-html-gui
```

or `py -m chat_capture_to_html.gui` on Windows, `python3 -m chat_capture_to_html.gui` elsewhere. Choose the capture folder and a reports folder, tick the options you want, and press **Run**:

- **No OCR**, **Only use screenshots whose hash verifies** and **Diagnose** are the `--no-ocr`, `--verified-only` and `--diagnose` options. The two sender labels are `--me` and `--them`.
- The tool's output appears in the log as it runs. Afterwards the window says in plain words whether every hash matched, and **Open report** opens the report in your browser.
- It refuses a reports folder inside the capture folder, so the evidence folder stays as UFADE wrote it.
- The title bar shows the version, so you can tell which build ran.

It uses Tkinter, which comes with Python on Windows and macOS. On Linux, install it first, for example `sudo apt install python3-tk`.

## Use on Linux or macOS

`scripts/run.sh` does the setup for you. It keeps its own Python environment in `.venv` inside this folder, installs the tool there, reinstalls it whenever the code changes, and checks that the version that runs is the one in this folder. Everything after its own options goes to the tool unchanged:

```bash
scripts/run.sh ~/captures/screenshots -o ~/reports --no-ocr --diagnose
```

| Option | |
|---|---|
| `--update` | `git pull` before running |
| `--open` | Open the report when done (when `-o` names one `.html` file, or a folder that receives exactly one new report) |
| `--python PY` | Python to build the environment with (default `python3`, 3.10 or later) |
| `--no-venv` | Use that Python directly instead of `.venv`; it must already have the dependencies |

It passes on the tool's exit code (`0`, `2` for a hash problem, `3` for no OCR engine), so it can be used in other scripts. For OCR, install Tesseract with your package manager first, for example `sudo apt install tesseract-ocr tesseract-ocr-deu` or `brew install tesseract tesseract-lang`, or use `--no-ocr`.

## Device information (`.json` files)

A proposed addition to UFADE's Chat capture: next to each PNG, a `.json` file that holds the hash and also names the device. Only `sha256` is required:

```json
{
  "schema": "ufade-shot/1",
  "file": "Anna_11_12_2026_14_30_05.png",
  "sha256": "…64 hex…",
  "captured_utc": "2026-11-12T14:30:05Z",
  "device": {"udid": "…", "product_type": "iPad13,18", "os_version": "26.1", "name": "…"},
  "app": "Messages",
  "chat": "Anna",
  "tool": {"name": "UFADE", "version": "…"}
}
```

When a capture has these files:

- Each report shows the device (name, model, iOS version, UDID). The screenshot table says which file each hash was checked against.
- A chat folder whose screenshots name different devices, or where some name none, gets a warning, and the exit code is `2`.
- A `.json` whose `file` names another screenshot, or that isn't valid JSON or holds no hash, is reported as unreadable.
- The capture time comes from `captured_utc` (UTC), not from the file names (the device's local time, with no time zone).
- When several chats are processed in one run, `index.html` in the reports folder lists them grouped by device UDID, with links to each report.

The format is a draft and may change when UFADE adopts it. Captures with only `.txt` files work as before.

## Limits

- With `--no-ocr` the report shows the screenshots themselves, so it can't be searched or copied as text.
- OCR text is a reading aid; the screenshot is the evidence. Emoji are not recognised, and `I` sometimes comes out as `|`. Low-confidence messages are highlighted.
- Photos, stickers and voice messages are not bubbles of flat colour, so they are not listed as messages. They are still visible in the linked screenshots.
- Alignment prefers a gap over a guess: a pair of shots that only look alike, or that match equally well in two places, is not merged, so a message can then appear twice on either side of the gap marker.
- Sender is decided by side; a bubble colour on both sides is flagged, not corrected.
- Detecting the header assumes at least two screenshots of the same size. Shots of a different size are skipped and reported.

## Tests

`tests/make_fixture.py` builds synthetic captures with no real messages: iMessage-style and WhatsApp-style, with a fixed wallpaper, a clock that changes between shots, and one deliberately wrong hash. The end-to-end tests compare the result with the known conversation.

```bash
py -m pip install .[test]
```

```bash
py -m pytest
```

Real capture data stays out of the repository (`screenshots/` is git-ignored).

## License

GNU General Public License v3.0, see [LICENSE](LICENSE).

Author: Dielle De Noon (@Gear-I), with Claude
