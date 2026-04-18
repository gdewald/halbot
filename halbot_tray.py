"""Halbot Windows tray app.

Spawns the Discord bot as a child process and puts a tray icon in the
notification area with Start/Stop controls and a live-tailing log viewer.
The child is a plain `python bot.py` subprocess, so Stop/Restart never has
to untangle discord.py's event loop, voice websockets, or whisper state —
we just `terminate()` the process and spawn a fresh one.

Usage
-----
Normal run (no console window):
    pythonw halbot_tray.py

Autostart management (stdlib winreg, HKCU Run key):
    python halbot_tray.py --install-autostart
    python halbot_tray.py --uninstall-autostart
    python halbot_tray.py --autostart-status
"""
from __future__ import annotations

import argparse
import ctypes
import logging
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path
from tkinter import scrolledtext

from PIL import Image, ImageDraw
import pystray

import bot


# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
APP_DIR = Path(__file__).resolve().parent
LOG_DIR = APP_DIR / "logs"
LOG_FILE = LOG_DIR / "halbot.log"

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_KEY_NAME = "Halbot"

# Session-local mutex (no "Global\\" prefix) so multiple users on the same
# machine can each run their own tray app without colliding.
SINGLE_INSTANCE_MUTEX = "Halbot.SingleInstance"

log = logging.getLogger("halbot")


# ---------------------------------------------------------------------------
# Single-instance guard (Windows named mutex)
# ---------------------------------------------------------------------------
_mutex_handle = None  # held for the lifetime of the process


def acquire_single_instance() -> bool:
    """Acquire a process-wide Windows named mutex. Returns True if this is the
    only running instance, False if another tray app is already up. Call
    release_single_instance() on shutdown to free it immediately.
    """
    global _mutex_handle
    ERROR_ALREADY_EXISTS = 183
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    CreateMutexW = kernel32.CreateMutexW
    CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    CreateMutexW.restype = wintypes.HANDLE

    handle = CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX)
    err = ctypes.get_last_error()
    if not handle:
        # CreateMutexW itself failed — treat as "can't guarantee single instance"
        # and let the app run rather than silently refuse.
        return True
    if err == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False
    _mutex_handle = handle
    return True


def release_single_instance() -> None:
    """Explicitly release the single-instance mutex so a new launch succeeds
    immediately, even if the process takes a moment to fully exit."""
    global _mutex_handle
    if _mutex_handle is not None:
        ctypes.WinDLL("kernel32").CloseHandle(_mutex_handle)
        _mutex_handle = None


def _show_already_running_dialog() -> None:
    """Show a native MB_OK message box telling the user to check the tray."""
    MB_OK = 0x0
    MB_ICONINFORMATION = 0x40
    ctypes.windll.user32.MessageBoxW(
        None,
        "Halbot is already running. Check the tray icon in the notification area.",
        "Halbot",
        MB_OK | MB_ICONINFORMATION,
    )


# ---------------------------------------------------------------------------
# Bot lifecycle (subprocess model)
# ---------------------------------------------------------------------------
def _child_python_exe() -> str:
    """Path to the Python interpreter used to run the bot child process.

    Prefer pythonw.exe so the child has no console window. Falls back to the
    current interpreter if pythonw is missing (non-Windows, embedded builds).
    """
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


class BotRunner:
    """Spawn bot.py as a child process. Start = Popen, Stop = terminate."""

    STOP_GRACE_SECONDS = 8.0   # time after terminate() before kill()
    KILL_WAIT_SECONDS = 5.0    # time after kill() before giving up

    def __init__(self, on_state_change=None):
        self._proc: subprocess.Popen | None = None
        self._watcher: threading.Thread | None = None
        self._on_state_change = on_state_change or (lambda running: None)
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        p = self._proc
        return p is not None and p.poll() is None

    def start(self) -> None:
        with self._lock:
            if self.is_running:
                return
            if not bot.DISCORD_TOKEN:
                log.error("DISCORD_TOKEN not set — cannot start bot")
                return

            creationflags = 0
            if os.name == "nt":
                # CREATE_NO_WINDOW so the child never flashes a console window
                # even if python.exe (not pythonw.exe) is picked.
                creationflags |= 0x08000000  # CREATE_NO_WINDOW

            try:
                proc = subprocess.Popen(
                    [_child_python_exe(), str(APP_DIR / "bot.py")],
                    cwd=str(APP_DIR),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    creationflags=creationflags,
                    close_fds=True,
                )
            except Exception:
                log.exception("Failed to spawn bot subprocess")
                return

            self._proc = proc
            log.info("Bot subprocess started (pid=%s)", proc.pid)
            self._watcher = threading.Thread(
                target=self._watch, args=(proc,), name="halbot-watcher", daemon=True,
            )
            self._watcher.start()
        self._on_state_change(True)

    def _watch(self, proc: subprocess.Popen) -> None:
        rc = proc.wait()
        log.info("Bot subprocess (pid=%s) exited with code %s", proc.pid, rc)
        with self._lock:
            if self._proc is proc:
                self._proc = None
        self._on_state_change(False)

    def stop(self) -> None:
        with self._lock:
            proc = self._proc
        if proc is None or proc.poll() is not None:
            with self._lock:
                self._proc = None
            return

        log.info("Stopping bot subprocess (pid=%s)", proc.pid)
        try:
            proc.terminate()
        except Exception:
            log.exception("terminate() failed")

        deadline = time.monotonic() + self.STOP_GRACE_SECONDS
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(0.1)

        if proc.poll() is None:
            log.warning("Bot didn't exit after terminate — killing (pid=%s)", proc.pid)
            try:
                proc.kill()
            except Exception:
                log.exception("kill() failed")
            try:
                proc.wait(timeout=self.KILL_WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                log.error("Bot subprocess (pid=%s) survived kill — abandoning", proc.pid)

        watcher = self._watcher
        if watcher is not None and watcher.is_alive():
            watcher.join(timeout=1.0)

        with self._lock:
            if self._proc is proc:
                self._proc = None


# ---------------------------------------------------------------------------
# Tray icon (PIL-drawn, no asset files)
# ---------------------------------------------------------------------------
def _make_icon(running: bool) -> Image.Image:
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = (76, 175, 80, 255) if running else (120, 120, 120, 255)
    draw.ellipse((4, 4, size - 4, size - 4), fill=color, outline=(30, 30, 30, 255), width=2)
    # Simple "H" glyph
    draw.rectangle((20, 18, 26, 46), fill=(255, 255, 255, 255))
    draw.rectangle((38, 18, 44, 46), fill=(255, 255, 255, 255))
    draw.rectangle((20, 29, 44, 35), fill=(255, 255, 255, 255))
    return img


# ---------------------------------------------------------------------------
# Log viewer window
# ---------------------------------------------------------------------------
class LogWindow:
    """Live-tailing log viewer. Tails logs/halbot.log from disk."""

    POLL_MS = 250
    READ_CHUNK = 65536  # per-tick byte cap to stay responsive

    def __init__(
        self,
        root: tk.Tk,
        runner=None,
        on_start=None,
        on_stop=None,
        on_restart=None,
        is_busy=None,
    ):
        self._root = root
        self._runner = runner
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_restart = on_restart
        self._is_busy = is_busy or (lambda: False)
        self._win: tk.Toplevel | None = None
        self._text: scrolledtext.ScrolledText | None = None
        self._btn_startstop: tk.Button | None = None
        self._btn_restart: tk.Button | None = None
        self._autoscroll = True
        self._file_pos = 0  # last-read offset in LOG_FILE
        self._file_inode_key = None  # (size, mtime) snapshot for rotation detection

    def show(self) -> None:
        if self._win is not None and self._win.winfo_exists():
            self._win.deiconify()
            self._win.lift()
            return
        self._build()
        self._root.after(self.POLL_MS, self._drain)

    def _build(self) -> None:
        self._win = tk.Toplevel(self._root)
        self._win.title("Halbot — logs")
        self._win.geometry("900x500")
        self._win.protocol("WM_DELETE_WINDOW", self._on_close)

        toolbar = tk.Frame(self._win)
        toolbar.pack(side=tk.TOP, fill=tk.X)
        tk.Button(toolbar, text="Clear", command=self._clear).pack(side=tk.LEFT, padx=4, pady=4)
        self._autoscroll_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            toolbar, text="Autoscroll", variable=self._autoscroll_var,
            command=self._on_autoscroll_toggle,
        ).pack(side=tk.LEFT, padx=4)
        tk.Button(toolbar, text="Open file", command=open_log_file).pack(side=tk.LEFT, padx=4)
        tk.Button(toolbar, text="Open folder", command=open_log_folder).pack(side=tk.LEFT, padx=4)

        # Log level picker
        tk.Label(toolbar, text="Level:").pack(side=tk.LEFT, padx=(12, 2))
        current = logging.getLevelName(logging.getLogger().level)
        self._level_var = tk.StringVar(value=current)
        level_menu = tk.OptionMenu(
            toolbar, self._level_var,
            "DEBUG", "INFO", "WARNING", "ERROR",
            command=self._on_level_change,
        )
        level_menu.pack(side=tk.LEFT, padx=2)

        # Bot controls (right side of toolbar)
        if self._runner is not None:
            self._btn_restart = tk.Button(
                toolbar, text="Restart", width=8, command=self._on_restart_click,
            )
            self._btn_restart.pack(side=tk.RIGHT, padx=4, pady=4)
            self._btn_startstop = tk.Button(
                toolbar, text="Start", width=8, command=self._on_startstop_click,
            )
            self._btn_startstop.pack(side=tk.RIGHT, padx=4, pady=4)

        self._text = scrolledtext.ScrolledText(
            self._win, wrap=tk.NONE, font=("Consolas", 9), state=tk.DISABLED,
        )
        self._text.pack(fill=tk.BOTH, expand=True)

        # Seed with the tail of the log file so the window isn't empty on open.
        self._seed_from_file()
        self._refresh_buttons()

    def _seed_from_file(self) -> None:
        if not LOG_FILE.exists() or self._text is None:
            self._file_pos = 0
            return
        try:
            # Read the last ~64KB of the file — enough to give context without
            # loading an unbounded amount.
            with LOG_FILE.open("rb") as fh:
                fh.seek(0, os.SEEK_END)
                end = fh.tell()
                start = max(0, end - 65536)
                fh.seek(start)
                chunk = fh.read().decode("utf-8", errors="replace")
            if start > 0:
                chunk = chunk.split("\n", 1)[-1]  # drop partial first line
            self._append(chunk)
            self._file_pos = end
            self._file_inode_key = self._stat_key()
        except Exception:
            log.exception("Could not seed log viewer from file")
            self._file_pos = 0

    def _stat_key(self):
        try:
            st = LOG_FILE.stat()
            return (st.st_size, st.st_mtime_ns)
        except FileNotFoundError:
            return None

    def _drain(self) -> None:
        if self._win is None or not self._win.winfo_exists():
            return
        try:
            if LOG_FILE.exists():
                size = LOG_FILE.stat().st_size
                # Detect rotation/truncate: file shrank below last-known offset.
                if size < self._file_pos:
                    self._file_pos = 0
                if size > self._file_pos:
                    with LOG_FILE.open("rb") as fh:
                        fh.seek(self._file_pos)
                        chunk = fh.read(self.READ_CHUNK)
                        self._file_pos = fh.tell()
                    if chunk:
                        self._append(chunk.decode("utf-8", errors="replace"))
        except Exception:
            log.exception("Log tail read failed")
        self._refresh_buttons()
        self._root.after(self.POLL_MS, self._drain)

    def _on_startstop_click(self) -> None:
        if self._runner is None:
            return
        if self._runner.is_running:
            if self._on_stop:
                self._on_stop()
        else:
            if self._on_start:
                self._on_start()

    def _on_restart_click(self) -> None:
        if self._on_restart:
            self._on_restart()

    def _refresh_buttons(self) -> None:
        if self._btn_startstop is None or self._runner is None:
            return
        busy = self._is_busy()
        running = self._runner.is_running
        self._btn_startstop.config(
            text="Stop" if running else "Start",
            state=tk.DISABLED if busy else tk.NORMAL,
        )
        if self._btn_restart is not None:
            self._btn_restart.config(
                state=tk.NORMAL if (running and not busy) else tk.DISABLED,
            )

    def _append(self, text: str) -> None:
        if not text or self._text is None:
            return
        self._text.configure(state=tk.NORMAL)
        self._text.insert(tk.END, text)
        # Keep the buffer bounded (~5000 lines)
        line_count = int(self._text.index("end-1c").split(".")[0])
        if line_count > 5000:
            self._text.delete("1.0", f"{line_count - 5000}.0")
        if self._autoscroll:
            self._text.see(tk.END)
        self._text.configure(state=tk.DISABLED)

    def _clear(self) -> None:
        if self._text is None:
            return
        self._text.configure(state=tk.NORMAL)
        self._text.delete("1.0", tk.END)
        self._text.configure(state=tk.DISABLED)

    def _on_autoscroll_toggle(self) -> None:
        self._autoscroll = bool(self._autoscroll_var.get())

    def _on_level_change(self, level_name: str) -> None:
        level = getattr(logging, level_name, logging.INFO)
        logging.getLogger().setLevel(level)
        log.info("Log level changed to %s", level_name)

    def _on_close(self) -> None:
        if self._win is not None:
            self._win.withdraw()  # hide instead of destroy for fast re-open


# ---------------------------------------------------------------------------
# Shell helpers
# ---------------------------------------------------------------------------
def open_log_file() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_FILE.touch(exist_ok=True)
    os.startfile(str(LOG_FILE))  # noqa: PLC1901 — Windows-only


def open_log_folder() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    os.startfile(str(LOG_DIR))


# ---------------------------------------------------------------------------
# Autostart (HKCU Run key)
# ---------------------------------------------------------------------------
def _pythonw_exe() -> str:
    """Return the path to pythonw.exe for the current interpreter, with fallback."""
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def _autostart_command() -> str:
    return f'"{_pythonw_exe()}" "{Path(__file__).resolve()}"'


def install_autostart() -> None:
    import winreg
    cmd = _autostart_command()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, RUN_KEY_NAME, 0, winreg.REG_SZ, cmd)
    print(f"Autostart installed: {cmd}")


def uninstall_autostart() -> None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, RUN_KEY_NAME)
        print("Autostart removed.")
    except FileNotFoundError:
        print("Autostart was not installed.")


def autostart_status() -> None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
            value, _ = winreg.QueryValueEx(key, RUN_KEY_NAME)
            print(f"Autostart is ENABLED: {value}")
    except FileNotFoundError:
        print("Autostart is DISABLED.")


# ---------------------------------------------------------------------------
# App wiring
# ---------------------------------------------------------------------------
def run_app() -> None:
    if not acquire_single_instance():
        _show_already_running_dialog()
        return

    # Tray's own logs share the bot's rotating file. The child bot process
    # reconfigures logging on its own startup; both writers appending to the
    # same RotatingFileHandler-managed file is fine for typical volumes, and
    # the log viewer tails the file so it sees both streams.
    bot.configure_logging(LOG_FILE)

    # Tk runs on the main thread; create root up front and keep it hidden.
    root = tk.Tk()
    root.withdraw()

    runner = BotRunner()
    icon_holder: dict = {}
    busy_event = threading.Event()  # set while any start/stop/restart is in flight

    def refresh_icon(running: bool) -> None:
        icon = icon_holder.get("icon")
        if icon is None:
            return
        try:
            icon.icon = _make_icon(running)
            icon.update_menu()
        except Exception:
            log.exception("Failed to refresh tray icon")

    runner._on_state_change = lambda running: root.after(0, refresh_icon, running)

    def _schedule(fn, name="halbot-op"):
        """Run fn on a background thread, holding busy_event for its duration."""
        def _wrapped():
            busy_event.set()
            try:
                fn()
            except Exception:
                log.exception("Bot operation failed")
            finally:
                busy_event.clear()
        threading.Thread(target=_wrapped, name=name, daemon=True).start()

    def do_start(icon=None, item=None):
        _schedule(runner.start, name="halbot-start")

    def do_stop(icon=None, item=None):
        _schedule(runner.stop, name="halbot-stop")

    def do_restart(icon=None, item=None):
        def _restart():
            runner.stop()
            runner.start()
        _schedule(_restart, name="halbot-restart")

    log_window = LogWindow(
        root,
        runner=runner,
        on_start=do_start,
        on_stop=do_stop,
        on_restart=do_restart,
        is_busy=busy_event.is_set,
    )

    def do_show_logs(icon, item):
        root.after(0, log_window.show)

    def do_open_log(icon, item):
        open_log_file()

    def do_quit(icon, item):
        def _teardown():
            runner.stop()
            release_single_instance()
            try:
                icon.stop()
            except Exception:
                pass
            root.after(0, root.destroy)
        threading.Thread(target=_teardown, daemon=True).start()

    menu = pystray.Menu(
        pystray.MenuItem(
            lambda item: "Stop bot" if runner.is_running else "Start bot",
            lambda icon, item: do_stop(icon, item) if runner.is_running else do_start(icon, item),
            default=True,
            enabled=lambda item: not busy_event.is_set(),
        ),
        pystray.MenuItem(
            "Restart bot", do_restart,
            visible=lambda item: runner.is_running,
            enabled=lambda item: not busy_event.is_set(),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open log window", do_show_logs),
        pystray.MenuItem("Open log file", do_open_log),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", do_quit),
    )

    icon = pystray.Icon("halbot", _make_icon(False), "Halbot", menu=menu)
    icon_holder["icon"] = icon
    icon.run_detached()

    # Auto-start the bot on launch.
    root.after(100, runner.start)

    try:
        root.mainloop()
    finally:
        release_single_instance()
        try:
            icon.stop()
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Halbot Windows tray app")
    parser.add_argument("--install-autostart", action="store_true",
                        help="Register Halbot to start automatically at Windows login")
    parser.add_argument("--uninstall-autostart", action="store_true",
                        help="Remove the autostart registration")
    parser.add_argument("--autostart-status", action="store_true",
                        help="Print current autostart registration")
    args = parser.parse_args()

    if args.install_autostart:
        install_autostart()
        return
    if args.uninstall_autostart:
        uninstall_autostart()
        return
    if args.autostart_status:
        autostart_status()
        return

    run_app()


if __name__ == "__main__":
    main()
