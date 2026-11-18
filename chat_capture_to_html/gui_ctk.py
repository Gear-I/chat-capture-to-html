"""The dark window, with a side panel and one step at a time (its look follows UFADE's).

Used by ``chat-capture-to-html-gui`` when CustomTkinter is installed
(``pip install .[gui]``); without it the plain window in gui.py opens. Both
build the same command line and run the same tool, so the report is exactly
what the command would produce.

It is always dark. Steps: 1 choose the capture and the reports folder, 2 options, 3 run and
result. The side panel shows what the chosen capture holds (screenshots,
hash checks, device, accessibility captions), read from the files without changing them.
"""

from __future__ import annotations

import contextlib
import queue
import sys
import threading
import time
import webbrowser
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from . import __version__
from .gui import RESULTS, Settings, build_args, capture_summary, new_reports, run_tool, set_icon, validate

SIDE = "#2c353e"       # side panel
MAIN = "#212121"       # main area
FAINT = "#5c636b"      # header line and hints
INFO = "#abb3bd"       # side panel text
BAD = "#ff8a80"


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"chat-capture-to-html {__version__}")
        self.geometry("1040x640")
        self.minsize(900, 560)
        set_icon(self)  # on Windows through iconbitmap, which also keeps CustomTkinter's own icon away
        self.font = ctk.CTkFont(size=14)
        self.big = ctk.CTkFont(size=15, weight="bold")
        self.mono = ctk.CTkFont(family="Consolas" if sys.platform == "win32" else "monospace", size=13)

        self.s = Settings(output=str(Path.home() / "chat-capture-reports"))
        self.q: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self.started = 0.0
        self.reports: list[Path] = []
        self.summary_for = None

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        side = ctk.CTkFrame(self, width=300, corner_radius=0, fg_color=SIDE)
        side.grid(row=0, column=0, sticky="ns")
        side.grid_propagate(False)
        side.grid_rowconfigure(1, weight=1)
        logo = None
        big_png = Path(__file__).with_name("logo_256.png")
        if big_png.exists():
            with contextlib.suppress(Exception):
                from PIL import Image
                logo = ctk.CTkImage(Image.open(big_png), size=(48, 48))
        ctk.CTkLabel(side, text=f"  chat-capture-to-html\n  version {__version__}", image=logo, compound="left",
                     font=self.big, text_color=INFO, justify="left", anchor="w").grid(
            row=0, column=0, sticky="ew", padx=14, pady=(16, 8))
        self.info = ctk.CTkTextbox(side, width=270, fg_color=SIDE, text_color=INFO, font=self.mono, wrap="none",
                                   activate_scrollbars=True, corner_radius=0)
        self.info.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 4))
        ctk.CTkLabel(side, text="For captures made with UFADE and ALEX\nby Christian Peter (prosch88)", font=self.font,
                     text_color=FAINT, justify="left", anchor="w").grid(row=2, column=0, sticky="ew", padx=16,
                                                                         pady=(0, 14))
        self._set_info("No capture chosen yet.")

        right = ctk.CTkFrame(self, corner_radius=0, fg_color=MAIN)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)
        self.header = ctk.CTkLabel(right, text="", text_color=FAINT, font=self.font, anchor="w")
        self.header.grid(row=0, column=0, sticky="ew", padx=30, pady=(14, 0))
        self.page = ctk.CTkFrame(right, corner_radius=0, fg_color=MAIN)
        self.page.grid(row=1, column=0, sticky="nsew", padx=20, pady=10)
        self.show_folders()

    # -------------------------------------------------------------- helpers
    def _clear(self, step: str) -> None:
        for w in self.page.winfo_children():
            w.destroy()
        for r in range(20):  # each step sets its own stretching row
            self.page.grid_rowconfigure(r, weight=0)
        self.page.grid_columnconfigure(1, weight=1)
        self.header.configure(text=f"{step}   ·   Reports: {self.s.output or 'not chosen'}")

    def _set_info(self, text: str) -> None:
        self.info.configure(state="normal")
        self.info.delete("1.0", "end")
        self.info.insert("1.0", text)
        self.info.configure(state="disabled")

    def _row(self, r: int, text: str, command, note: str, **kw) -> ctk.CTkButton:
        """A large button with what it does written beside it."""
        b = ctk.CTkButton(self.page, text=text, command=command, width=200, height=64, font=self.font, **kw)
        b.grid(row=r, column=0, padx=(10, 20), pady=8, sticky="w")
        ctk.CTkLabel(self.page, text=note, font=self.font, anchor="w", justify="left",
                     wraplength=440).grid(row=r, column=1, sticky="w")
        return b

    def _summarise(self) -> None:
        """Fill the side panel for the chosen capture, in the background: hashing takes a moment."""
        folder = self.s.capture
        if not folder or folder == self.summary_for:
            return
        self.summary_for = folder
        self._set_info("Reading the capture…\n(hashing every screenshot)")

        done: "queue.Queue[str]" = queue.Queue()

        def work() -> None:  # no Tk calls here: Tk is used from the main thread only
            try:
                done.put(capture_summary(Path(folder)))
            except Exception as e:  # shown, not hidden; the run itself reports details
                done.put(f"Could not read the capture:\n{e}")

        def check() -> None:
            try:
                text = done.get_nowait()
            except queue.Empty:
                self.after(150, check)
                return
            if self.summary_for == folder:
                self._set_info(text)
        threading.Thread(target=work, daemon=True).start()
        self.after(150, check)

    # -------------------------------------------------------------- step 1
    def show_folders(self) -> None:
        self._clear("Step 1 of 3: folders")
        cap, out = ctk.StringVar(value=self.s.capture), ctk.StringVar(value=self.s.output)

        def pick(var, title) -> None:
            d = filedialog.askdirectory(title=title, initialdir=var.get() or str(Path.home()))
            if d:
                var.set(d)
                self.s.capture, self.s.output = cap.get(), out.get()
                self.header.configure(text=f"Step 1 of 3: folders   ·   Reports: {self.s.output}")
                self._summarise()

        for r, (label, var, title, note) in enumerate((
                ("Capture folder", cap, "The capture's screenshots folder, or one chat folder in it",
                 "The capture's screenshots folder (from UFADE or ALEX), or one chat folder in it. It is only read."),
                ("Reports folder", out, "Where to write the reports (not inside the capture)",
                 "Where the reports go. Not inside the capture folder."))):
            self._row(r * 2, label, lambda v=var, t=title: pick(v, t), note)
            ctk.CTkEntry(self.page, textvariable=var, font=self.font).grid(
                row=r * 2 + 1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 10))

        def next_step() -> None:
            self.s.capture, self.s.output = cap.get(), out.get()
            problem = validate(Settings(self.s.capture, self.s.output))
            if problem:
                self.error.configure(text=problem)
                return
            self._summarise()
            self.show_options()

        self.error = ctk.CTkLabel(self.page, text="", text_color=BAD, font=self.font, wraplength=640, anchor="w")
        self.error.grid(row=4, column=0, columnspan=2, sticky="ew", padx=10, pady=(6, 0))
        self.page.grid_rowconfigure(5, weight=1)
        ctk.CTkButton(self.page, text="Next", command=next_step, font=self.font).grid(
            row=6, column=1, sticky="e", padx=10, pady=10)

    # -------------------------------------------------------------- step 2
    def show_options(self) -> None:
        self._clear("Step 2 of 3: options")
        s = self.s
        no_ocr, verified, diagnose = ctk.BooleanVar(value=s.no_ocr), ctk.BooleanVar(value=s.verified_only), \
            ctk.BooleanVar(value=s.diagnose)
        me, them, lang = ctk.StringVar(value=s.me), ctk.StringVar(value=s.them), ctk.StringVar(value=s.lang)
        r = 0
        for var, text, note in (
                (no_ocr, "No OCR", "Show each message as it looks in its screenshot. No Tesseract needed; "
                                   "captions are then not matched to messages."),
                (verified, "Only verified shots", "Leave out screenshots whose hash does not verify."),
                (diagnose, "Diagnose", "Also print how the screenshots were lined up (numbers only).")):
            ctk.CTkSwitch(self.page, text=text, variable=var, font=self.font, width=200).grid(
                row=r, column=0, padx=(10, 20), pady=8, sticky="w")
            ctk.CTkLabel(self.page, text=note, font=self.font, anchor="w", justify="left",
                         wraplength=440).grid(row=r, column=1, sticky="w")
            r += 1
        for var, text, note in (
                (me, "Right-hand bubbles", "Label for the device owner's side."),
                (them, "Left-hand bubbles", "Empty: the chat folder name."),
                (lang, "OCR languages", "Empty: German and English, where installed. E.g. eng, or deu+eng.")):
            ctk.CTkLabel(self.page, text=text, font=self.font, anchor="w").grid(
                row=r, column=0, padx=(10, 20), pady=(10, 0), sticky="w")
            ctk.CTkEntry(self.page, textvariable=var, font=self.font).grid(
                row=r, column=1, sticky="ew", padx=(0, 10), pady=(10, 0))
            ctk.CTkLabel(self.page, text=note, text_color=FAINT, font=self.font, anchor="w").grid(
                row=r + 1, column=1, sticky="w")
            r += 2
        self.error = ctk.CTkLabel(self.page, text="", text_color=BAD, font=self.font, wraplength=640, anchor="w")
        self.error.grid(row=r, column=0, columnspan=2, sticky="ew", padx=10, pady=(6, 0))
        self.page.grid_rowconfigure(r + 1, weight=1)

        def keep() -> None:
            s.no_ocr, s.verified_only, s.diagnose = no_ocr.get(), verified.get(), diagnose.get()
            s.me, s.them, s.lang = me.get(), them.get(), lang.get()

        def back() -> None:
            keep()
            self.show_folders()

        def run() -> None:
            keep()
            problem = validate(s)
            if problem:
                self.error.configure(text=problem)
                return
            self.show_run()

        bar = ctk.CTkFrame(self.page, fg_color=MAIN)
        bar.grid(row=r + 2, column=0, columnspan=2, sticky="e", padx=10, pady=10)
        ctk.CTkButton(bar, text="Back", command=back, font=self.font, fg_color="#3a3f45").pack(side="left", padx=6)
        ctk.CTkButton(bar, text="Run", command=run, font=self.font).pack(side="left")

    # -------------------------------------------------------------- step 3
    def show_run(self) -> None:
        self._clear("Step 3 of 3: run")
        self.page.grid_rowconfigure(0, weight=1)
        log = ctk.CTkTextbox(self.page, font=self.mono, wrap="none")
        log.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=10, pady=(4, 6))
        log.configure(state="disabled")
        status = ctk.CTkLabel(self.page, text="Working…", font=self.font, anchor="w", justify="left", wraplength=640)
        status.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10)
        bar = ctk.CTkFrame(self.page, fg_color=MAIN)
        bar.grid(row=2, column=0, columnspan=2, sticky="e", padx=10, pady=10)
        open_btn = ctk.CTkButton(bar, text="Open report", font=self.font, state="disabled", command=self._open_report)
        folder_btn = ctk.CTkButton(bar, text="Open reports folder", font=self.font, command=self._open_folder)
        again_btn = ctk.CTkButton(bar, text="New run", font=self.font, state="disabled", fg_color="#3a3f45",
                                  command=self.show_folders)
        for b in (again_btn, folder_btn, open_btn):
            b.pack(side="left", padx=6)

        Path(self.s.output).expanduser().mkdir(parents=True, exist_ok=True)
        self.started = time.time()
        self.q = queue.Queue()
        threading.Thread(target=run_tool, args=(build_args(self.s), self.q), daemon=True).start()

        def poll() -> None:
            try:
                while True:
                    kind, value = self.q.get_nowait()
                    if kind == "line":
                        log.configure(state="normal")
                        log.insert("end", f"{value}\n")
                        log.see("end")
                        log.configure(state="disabled")
                    else:
                        code = int(value)
                        self.reports = new_reports(Path(self.s.output).expanduser(), self.started)
                        status.configure(
                            text=RESULTS.get(code, f"Stopped with exit code {code}: see the log above.")
                            + (f"  {len(self.reports)} report(s) written." if self.reports else ""),
                            text_color=INFO if code == 0 else BAD)
                        open_btn.configure(state="normal" if self.reports else "disabled")
                        again_btn.configure(state="normal")
                        return
            except queue.Empty:
                pass
            self.after(100, poll)
        self.after(100, poll)

    def _open_report(self) -> None:
        for p in self.reports[:5]:
            webbrowser.open(p.resolve().as_uri())

    def _open_folder(self) -> None:
        d = Path(self.s.output).expanduser()
        if d.is_dir():
            webbrowser.open(d.resolve().as_uri())


def run_window() -> None:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    App().mainloop()
