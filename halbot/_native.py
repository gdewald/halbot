"""Native-binary discovery for voice path: libopus + ffmpeg.exe.

Frozen PyInstaller bundle ships pyav's DLLs under _internal/av.libs/ with
content-hash-suffixed filenames (e.g. libopus-0-fb6521a3...dll). discord.py's
auto-load (ctypes.util.find_library("opus")) never sees them, so inbound
voice-recv blows up with OpusNotLoaded on the first RTP packet.

ffmpeg.exe is not bundled at all — pyav vendors the codec DLLs but not the
CLI wrapper. Under NSSM/LocalSystem PATH omits the installing user's
WinGet shim dir, so discord.py's FFmpegPCMAudio subprocess spawn fails
with "ffmpeg was not found."

Both resolvers cache their result. Safe to call repeatedly.
"""
from __future__ import annotations

import glob
import logging
import os
import shutil
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def _bundle_roots() -> list[Path]:
    roots: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    exe_dir = Path(sys.executable).parent
    roots.append(exe_dir)
    roots.append(exe_dir / "_internal")
    return [r for r in roots if r.is_dir()]


_opus_loaded = False


def load_opus() -> bool:
    """Ensure discord.opus has a libopus loaded. Returns True on success."""
    global _opus_loaded
    if _opus_loaded:
        return True

    import discord.opus as _opus

    if _opus.is_loaded():
        _opus_loaded = True
        return True

    candidates: list[str] = []
    for root in _bundle_roots():
        candidates += glob.glob(str(root / "av.libs" / "libopus-0*.dll"))
        candidates += glob.glob(str(root / "libopus-0*.dll"))
        candidates += glob.glob(str(root / "libopus.dll"))

    # Source run: also let discord.py's own find_library fire first.
    try:
        _opus.load_opus("opus")
        if _opus.is_loaded():
            _opus_loaded = True
            log.info("[opus] loaded via discord.opus default resolution")
            return True
    except Exception:
        pass

    for path in candidates:
        try:
            _opus.load_opus(path)
            if _opus.is_loaded():
                _opus_loaded = True
                log.info("[opus] loaded %s", path)
                return True
        except Exception as e:
            log.debug("[opus] load %s failed: %s", path, e)

    log.error("[opus] no libopus found; voice-recv decode will fail")
    return False


_UNSET: object = object()
_ffmpeg_cache: object | str | None = _UNSET


def ffmpeg_path() -> str | None:
    """Resolve an ffmpeg.exe path the daemon can actually spawn."""
    global _ffmpeg_cache
    if _ffmpeg_cache is not _UNSET:
        return _ffmpeg_cache  # type: ignore[return-value]

    env = os.environ.get("HALBOT_FFMPEG") or os.environ.get("FFMPEG_BINARY")
    if env and os.path.isfile(env):
        _ffmpeg_cache = env
        log.info("[ffmpeg] using %s (from env)", env)
        return env

    which = shutil.which("ffmpeg")
    if which:
        _ffmpeg_cache = which
        log.info("[ffmpeg] using %s (from PATH)", which)
        return which

    known: list[str] = []
    for root in _bundle_roots():
        known.append(str(root / "ffmpeg.exe"))
    pf = os.environ.get("ProgramFiles")
    if pf:
        known.append(str(Path(pf) / "ffmpeg" / "bin" / "ffmpeg.exe"))
        known.append(str(Path(pf) / "Halbot" / "daemon" / "ffmpeg.exe"))
    # WinGet per-user installs: service PATH omits these, but exe exists.
    # Scan all user profiles so a LocalSystem daemon can still find one the
    # installing user already set up.
    known += glob.glob(
        r"C:\Users\*\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
    )
    known += glob.glob(
        r"C:\Users\*\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_*\ffmpeg-*-full_build\bin\ffmpeg.exe"
    )

    for cand in known:
        if cand and os.path.isfile(cand):
            _ffmpeg_cache = cand
            log.info("[ffmpeg] using %s", cand)
            return cand

    _ffmpeg_cache = None
    log.error("[ffmpeg] no ffmpeg.exe found; audio playback will fail")
    return None
