"""Tkinter Text widget that tails the daemon log file."""

from __future__ import annotations

import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import scrolledtext


class LogViewer:
    def __init__(self, log_path: Path) -> None:
        self._path = log_path
        self._root: tk.Tk | None = None
        self._text: scrolledtext.ScrolledText | None = None
        self._stop = threading.Event()

    def open(self) -> None:
        if self._root is not None:
            try:
                self._root.lift()
                return
            except tk.TclError:
                self._root = None
        self._stop.clear()
        self._root = tk.Tk()
        self._root.title(f"Halbot log — {self._path}")
        self._root.geometry("1000x600")
        self._text = scrolledtext.ScrolledText(
            self._root, wrap=tk.NONE, bg="#111", fg="#ddd",
            insertbackground="#ddd", font=("Consolas", 10),
        )
        self._text.pack(fill=tk.BOTH, expand=True)
        self._root.protocol("WM_DELETE_WINDOW", self._close)
        threading.Thread(target=self._tail_loop, daemon=True).start()
        self._root.mainloop()

    def _close(self) -> None:
        self._stop.set()
        if self._root is not None:
            try:
                self._root.destroy()
            except tk.TclError:
                pass
            self._root = None

    def _append(self, chunk: str) -> None:
        if self._text is None:
            return
        try:
            self._text.insert(tk.END, chunk)
            self._text.see(tk.END)
        except tk.TclError:
            pass

    def _tail_loop(self) -> None:
        pos = 0
        while not self._stop.is_set():
            try:
                if self._path.exists():
                    with self._path.open("r", encoding="utf-8", errors="replace") as f:
                        f.seek(pos)
                        chunk = f.read()
                        pos = f.tell()
                        if chunk and self._root is not None:
                            self._root.after(0, self._append, chunk)
            except Exception:
                pass
            time.sleep(0.5)
