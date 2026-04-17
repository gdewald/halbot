"""Halbot Windows tray app.

Runs the Discord bot in a worker thread and puts a tray icon in the
notification area with Start/Stop controls and a live-tailing log viewer.

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
import asyncio
import ctypes
import logging
import logging.handlers
import os
import queue
import subprocess
import sys
import threading
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
# Bot lifecycle
# ---------------------------------------------------------------------------
class BotRunner:
    """Drive bot.client on a private asyncio loop in a background thread."""

    def __init__(self, on_state_change=None):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._on_state_change = on_state_change or (lambda running: None)
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    def start(self) -> None:
        with self._lock:
            if self.is_running:
                return
            if not bot.DISCORD_TOKEN:
                log.error("DISCORD_TOKEN not set — cannot start bot")
                return
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run, name="halbot-worker", daemon=True
            )
            self._thread.start()
        self._on_state_change(True)

    def _run(self) -> None:
        assert self._loop is not None
        loop = self._loop
        asyncio.set_event_loop(loop)
        # IMPORTANT: build the discord.Client on this thread, AFTER set_event_loop.
        # aiohttp's connector binds to the running-loop context, so creating the
        # client on the main thread produces "Connector is closed." at login time.
        bot.build_client()
        try:
            loop.run_until_complete(bot.client.start(bot.DISCORD_TOKEN))
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass  # clean shutdown via _close_and_cancel()
        except Exception:
            log.exception("Bot worker crashed")
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
            self._on_state_change(False)

    def stop(self, timeout: float = 10.0) -> None:
        with self._lock:
            thread = self._thread
            loop = self._loop
            client = bot.client
        if thread is None or not thread.is_alive():
            with self._lock:
                self._thread = None
                self._loop = None
            return

        if loop is not None and loop.is_running() and client is not None:
            async def _close_and_cancel():
                # Snapshot voice state here (on the event loop thread) so we
                # beat on_voice_state_update which fires during client.close().
                bot.snapshot_voice_state()
                # Give discord.py up to 5s to close gracefully, then move on.
                try:
                    await asyncio.wait_for(client.close(), timeout=5.0)
                except Exception:
                    pass
                # Cancel every remaining task so run_until_complete() returns.
                tasks = [t for t in asyncio.all_tasks()
                         if not t.done() and t is not asyncio.current_task()]
                for t in tasks:
                    t.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

            fut = asyncio.run_coroutine_threadsafe(_close_and_cancel(), loop)
            try:
                fut.result(timeout=timeout)
            except TimeoutError:
                log.warning("Shutdown coroutine timed out after %ss — forcing loop stop", timeout)
                try:
                    loop.call_soon_threadsafe(loop.stop)
                except RuntimeError:
                    pass
            except Exception:
                log.exception("Error during bot shutdown")

        thread.join(timeout=5.0)
        if thread.is_alive():
            log.warning("Worker thread did not exit — abandoning (daemon, will die with process)")
        with self._lock:
            self._thread = None
            self._loop = None


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
    """Live-tailing log viewer. A Toplevel that shows records pulled from a queue."""

    POLL_MS = 200

    def __init__(
        self,
        root: tk.Tk,
        record_queue: queue.Queue,
        runner=None,
        on_start=None,
        on_stop=None,
        on_restart=None,
        is_busy=None,
    ):
        self._root = root
        self._queue = record_queue
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
        self._formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

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
        except Exception:
            log.exception("Could not seed log viewer from file")

    def _drain(self) -> None:
        if self._win is None or not self._win.winfo_exists():
            return
        pulled = 0
        try:
            while pulled < 500:  # cap per-tick to stay responsive
                record = self._queue.get_nowait()
                self._append(self._formatter.format(record) + "\n")
                pulled += 1
        except queue.Empty:
            pass
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

    # Logging: stdout + rotating file. Add a QueueHandler for the GUI.
    bot.configure_logging(LOG_FILE)
    record_queue: queue.Queue = queue.Queue(maxsize=10000)
    qh = logging.handlers.QueueHandler(record_queue)
    logging.getLogger().addHandler(qh)

    bot.db_init()

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
        root, record_queue,
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
