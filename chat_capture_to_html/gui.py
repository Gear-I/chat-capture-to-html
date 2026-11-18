"""A small window for chat-capture-to-html, on Windows, macOS and Linux.

Start it with ``chat-capture-to-html-gui`` or ``python -m chat_capture_to_html.gui``.
It builds the same command line the terminal tool takes and runs it, so the
report is exactly what the command would produce. Tkinter ships with Python on
Windows and macOS; on Linux install the python3-tk package.
"""

from __future__ import annotations

import contextlib
import io
import queue
import re
import threading
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from . import __version__

RESULTS = {
    0: "Done. Every screenshot matched its SHA-256 hash.",
    2: "Done, with hash problems: see the Warnings at the top of the report.",
    3: "No OCR engine found. Install Tesseract, or tick “No OCR”.",
}


@dataclass
class Settings:
    capture: str = ""
    output: str = ""
    no_ocr: bool = False
    diagnose: bool = False
    verified_only: bool = False
    me: str = "Device owner"
    them: str = ""
    lang: str = ""          # Tesseract languages, e.g. deu+eng; empty: the tool's default


def validate(s: Settings) -> str | None:
    """A message for the user when the settings cannot run, else None."""
    if not s.capture.strip():
        return "Choose the capture folder (the capture's screenshots folder, or one chat folder in it)."
    cap = Path(s.capture).expanduser()
    if not cap.is_dir():
        return f"The capture folder does not exist: {cap}"
    if not s.output.strip():
        return "Choose a folder for the reports."
    out = Path(s.output).expanduser()
    try:
        cap_r, out_r = cap.resolve(), out.resolve()
    except OSError as e:
        return f"Cannot use that folder: {e}"
    if out_r == cap_r or cap_r in out_r.parents:
        return "Put the reports outside the capture folder: the evidence folder must stay as UFADE or ALEX wrote it."
    if s.lang.strip() and not s.no_ocr and not re.fullmatch(r"[A-Za-z_]+(\+[A-Za-z_]+)*", s.lang.strip()):
        return ("OCR languages: Tesseract language codes joined by +, for example deu+eng "
                "(the names of the .traineddata files in Tesseract's tessdata folder).")
    return None


def build_args(s: Settings) -> list[str]:
    """The command line for cli.main()."""
    args = [str(Path(s.capture).expanduser()), "-o", str(Path(s.output).expanduser())]
    if s.no_ocr:
        args.append("--no-ocr")
    if s.diagnose:
        args.append("--diagnose")
    if s.verified_only:
        args.append("--verified-only")
    if s.me.strip() and s.me.strip() != "Device owner":
        args += ["--me", s.me.strip()]
    if s.them.strip():
        args += ["--them", s.them.strip()]
    if s.lang.strip() and not s.no_ocr:
        args += ["--lang", s.lang.strip()]
    return args


def capture_summary(folder: Path) -> str:
    """What the capture folder holds, for the window's side panel: chats,
    screenshots, hash checks, devices and accessibility captions. Only reads the
    files (it hashes every screenshot, as the tool does)."""
    from .capture import HASH_OK, find_capture_folders, load_capture
    folder = Path(folder).expanduser()
    if not folder.is_dir():
        return "No capture chosen."
    caps = [load_capture(f) for f in find_capture_folders(folder)]
    if not caps:
        return "No screenshots (PNG files)\nin this folder."
    shots = [s for c in caps for s in c.shots]
    ok = sum(1 for s in shots if s.status == HASH_OK)
    lines = ["Capture", f"  {len(caps)} chat(s)"] + [f"   - {c.app} / {c.chat}" for c in caps[:8]]
    if len(caps) > 8:
        lines.append(f"   ... and {len(caps) - 8} more")
    lines += [f"  {len(shots)} screenshots", f"  {ok} hash-verified"]
    if ok != len(shots):
        lines.append(f"  ! {len(shots) - ok} with hash problems")
    orphans = sum(len(c.orphan_hash_files) for c in caps)
    if orphans:
        lines.append(f"  ! {orphans} hash file(s) without PNG")
    devices = {k: d for c in caps for k, d in c.devices.items()}
    lines += ["", "Device"]
    if not devices:
        lines.append("  not recorded (no .case.json)")
    for k, d in sorted(devices.items()):
        lines += [f"  {' '.join(x for x in (d.manufacturer, d.model) if x) or 'unknown model'}",
                  f"  {' '.join(x for x in (d.os_name, d.os_version) if x) or 'system not recorded'}"]
        if d.serial:
            lines.append(f"  serial {d.serial}")
    if len(devices) > 1:
        lines.append(f"  ! {len(devices)} different devices")
    captions = sum(1 for s in shots if s.caption)
    if captions:
        lines += ["", "Accessibility captions", f"  {captions} of {len(shots)} screenshots"]
    return "\n".join(lines)


def new_reports(out: Path, since: float) -> list[Path]:
    """Reports in ``out`` written at or after ``since`` (a time.time() value)."""
    if not out.is_dir():
        return []
    return sorted((p for p in out.glob("*.html") if p.stat().st_mtime >= since - 1), key=lambda p: p.name)


class _Lines(io.TextIOBase):
    """A file-like object that hands each written line to a queue."""

    def __init__(self, q: "queue.Queue[tuple[str, object]]"):
        self.q, self.buf = q, ""

    def write(self, text: str) -> int:
        self.buf += text
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self.q.put(("line", line))
        return len(text)

    def flush(self) -> None:
        if self.buf:
            self.q.put(("line", self.buf))
            self.buf = ""


def run_tool(args: list[str], q: "queue.Queue[tuple[str, object]]") -> None:
    """Run the tool with its output going to ``q``; ends with ("done", exit code)."""
    from .cli import main
    out = _Lines(q)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = main(args)
    except SystemExit as e:  # argparse errors
        code = e.code if isinstance(e.code, int) else 1
    except Exception as e:  # noqa: BLE001 - shown to the user, never swallowed
        out.write(f"error: {type(e).__name__}: {e}\n")
        code = 1
    out.flush()
    q.put(("done", code))


def set_icon(root) -> None:
    """The logo as the window's icon, on every system.

    Windows: the .ico, whose sizes give a sharp icon in the title bar and the
    taskbar. Linux and macOS: the PNGs from 512 down to 16 pixels, so the title
    bar, the taskbar or Alt-Tab view and the macOS Dock each get a sharp size
    (the largest first: macOS uses it for the Dock)."""
    import tkinter as tk
    here = Path(__file__).parent
    ico = here / "logo.ico"
    if root.tk.call("tk", "windowingsystem") == "win32" and ico.exists():
        with contextlib.suppress(tk.TclError):
            root.iconbitmap(default=str(ico))
            return
    images = []
    for size in (512, 256, 64, 32, 16):
        png = here / f"logo_{size}.png"
        if png.exists():
            with contextlib.suppress(tk.TclError):
                images.append(tk.PhotoImage(master=root, file=str(png)))
    if images:
        with contextlib.suppress(tk.TclError):
            root.iconphoto(True, *images)
        root._logo_images = images  # keep them alive as long as the window


def build_window(root):
    """Lay out the window on ``root`` and return its parts (for main and tests)."""
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter.scrolledtext import ScrolledText

    root.title(f"chat-capture-to-html {__version__}")
    root.minsize(640, 520)
    set_icon(root)

    capture = tk.StringVar()
    output = tk.StringVar(value=str(Path.home() / "chat-capture-reports"))
    no_ocr, diagnose, verified = tk.BooleanVar(), tk.BooleanVar(), tk.BooleanVar()
    me, them, lang = tk.StringVar(value="Device owner"), tk.StringVar(), tk.StringVar()
    status = tk.StringVar(value="Choose the capture folder, then Run.")
    q: "queue.Queue[tuple[str, object]]" = queue.Queue()
    state = {"started": 0.0, "reports": []}

    frm = ttk.Frame(root, padding=12)
    frm.pack(fill="both", expand=True)
    frm.columnconfigure(1, weight=1)

    def folder_row(r, label, var, title):
        ttk.Label(frm, text=label).grid(row=r, column=0, sticky="w", pady=3)
        ttk.Entry(frm, textvariable=var).grid(row=r, column=1, sticky="ew", padx=6)

        def pick():
            d = filedialog.askdirectory(title=title, initialdir=var.get() or str(Path.home()))
            if d:
                var.set(d)
        ttk.Button(frm, text="Browse…", command=pick).grid(row=r, column=2)

    folder_row(0, "Capture folder", capture, "The capture's screenshots folder, or one chat folder in it")
    folder_row(1, "Reports folder", output, "Where to write the reports (not inside the capture)")

    opts = ttk.LabelFrame(frm, text="Options", padding=8)
    opts.grid(row=2, column=0, columnspan=3, sticky="ew", pady=8)
    opts.columnconfigure(1, weight=1)
    ttk.Checkbutton(opts, text="No OCR: show each message as it looks in its screenshot (no Tesseract needed)",
                    variable=no_ocr).grid(row=0, column=0, columnspan=2, sticky="w")
    ttk.Checkbutton(opts, text="Only use screenshots whose hash verifies", variable=verified).grid(
        row=1, column=0, columnspan=2, sticky="w")
    ttk.Checkbutton(opts, text="Diagnose: show how the screenshots were lined up (numbers only)",
                    variable=diagnose).grid(row=2, column=0, columnspan=2, sticky="w")
    ttk.Label(opts, text="Right-hand bubbles").grid(row=3, column=0, sticky="w", pady=(6, 0))
    ttk.Entry(opts, textvariable=me).grid(row=3, column=1, sticky="ew", pady=(6, 0))
    ttk.Label(opts, text="Left-hand bubbles").grid(row=4, column=0, sticky="w")
    ttk.Entry(opts, textvariable=them).grid(row=4, column=1, sticky="ew")
    ttk.Label(opts, text="(empty: the chat folder name)", foreground="gray").grid(row=5, column=1, sticky="w")
    ttk.Label(opts, text="OCR languages").grid(row=6, column=0, sticky="w", pady=(6, 0))
    ttk.Entry(opts, textvariable=lang).grid(row=6, column=1, sticky="ew", pady=(6, 0))
    ttk.Label(opts, text="(empty: German and English, where installed; e.g. eng, or deu+eng)",
              foreground="gray").grid(row=7, column=1, sticky="w")

    buttons = ttk.Frame(frm)
    buttons.grid(row=3, column=0, columnspan=3, sticky="ew")
    run_btn = ttk.Button(buttons, text="Run")
    open_btn = ttk.Button(buttons, text="Open report", state="disabled")
    folder_btn = ttk.Button(buttons, text="Open reports folder")
    run_btn.pack(side="left")
    open_btn.pack(side="left", padx=6)
    folder_btn.pack(side="left")

    # no wrapping, so the --diagnose tables keep their columns; scroll sideways instead
    log = ScrolledText(frm, height=14, wrap="none", state="disabled", font=("TkFixedFont", 9))
    log.grid(row=4, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
    sideways = ttk.Scrollbar(frm, orient="horizontal", command=log.xview)
    log.configure(xscrollcommand=sideways.set)
    sideways.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(0, 4))
    frm.rowconfigure(4, weight=1)
    ttk.Label(frm, textvariable=status, wraplength=600).grid(row=6, column=0, columnspan=3, sticky="w")

    def add(line: str) -> None:
        log.configure(state="normal")
        log.insert("end", line + "\n")
        log.see("end")
        log.configure(state="disabled")

    def poll() -> None:
        try:
            while True:
                kind, value = q.get_nowait()
                if kind == "line":
                    add(str(value))
                else:
                    code = int(value)
                    reports = new_reports(Path(output.get()).expanduser(), state["started"])
                    state["reports"] = reports
                    status.set(RESULTS.get(code, f"Stopped with exit code {code}: see the log above.")
                               + (f"  {len(reports)} report(s) written." if reports else ""))
                    run_btn.configure(state="normal")
                    open_btn.configure(state="normal" if reports else "disabled")
                    return
        except queue.Empty:
            pass
        root.after(100, poll)

    def run() -> None:
        s = Settings(capture.get(), output.get(), no_ocr.get(), diagnose.get(), verified.get(), me.get(), them.get(),
                     lang.get())
        problem = validate(s)
        if problem:
            messagebox.showwarning("chat-capture-to-html", problem)
            return
        Path(s.output).expanduser().mkdir(parents=True, exist_ok=True)
        log.configure(state="normal")
        log.delete("1.0", "end")
        log.configure(state="disabled")
        status.set("Working…")
        run_btn.configure(state="disabled")
        open_btn.configure(state="disabled")
        state["started"] = time.time()
        threading.Thread(target=run_tool, args=(build_args(s), q), daemon=True).start()
        root.after(100, poll)

    def open_report() -> None:
        for p in state["reports"][:5]:
            webbrowser.open(p.resolve().as_uri())

    def open_folder() -> None:
        d = Path(output.get()).expanduser()
        if d.is_dir():
            webbrowser.open(d.resolve().as_uri())

    run_btn.configure(command=run)
    open_btn.configure(command=open_report)
    folder_btn.configure(command=open_folder)
    return {"capture": capture, "output": output, "no_ocr": no_ocr, "diagnose": diagnose, "verified": verified,
            "me": me, "them": them, "lang": lang, "status": status, "log": log, "run": run, "run_button": run_btn,
            "open_button": open_btn, "state": state}


def main() -> int:
    try:
        import tkinter as tk
    except ImportError:
        print("Tkinter is not available. On Linux install python3-tk (e.g. sudo apt install python3-tk).")
        return 1
    try:  # the dark window, when CustomTkinter is installed (pip install .[gui])
        from .gui_ctk import run_window
    except ImportError:
        run_window = None
    if run_window is not None:
        run_window()
        return 0
    root = tk.Tk()
    build_window(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
