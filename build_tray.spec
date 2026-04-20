# -*- mode: python ; coding: utf-8 -*-
# PyInstaller onedir spec for halbot-tray (with dashboard).

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hidden = (
    collect_submodules("grpc")
    + collect_submodules("tray")
    + collect_submodules("dashboard")
    + [
        "halbot._gen.mgmt_pb2",
        "halbot._gen.mgmt_pb2_grpc",
        "pystray._win32",
        "webview",
        "webview.platforms.edgechromium",
        "psutil",
    ]
)

datas = []

# Bundle the built frontend (if present) under dashboard/web/.
_fe_dist = Path("frontend/dist")
if (_fe_dist / "index.html").exists():
    datas += [(str(_fe_dist), "dashboard/web")]

# Always bundle the step-2 stub so the window still opens if the
# frontend build was skipped (e.g. Node missing on a daemon-only
# build). dashboard/paths.py falls back to it.
datas += [("dashboard/_stub.html", "dashboard")]

# pywebview carries platform-specific JS shim files it loads via
# importlib.resources. collect_data_files picks them up.
datas += collect_data_files("webview")

a = Analysis(
    ["halbot_tray_entry.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="halbot-tray",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="halbot-tray",
)
