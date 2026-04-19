# -*- mode: python ; coding: utf-8 -*-
# PyInstaller onedir spec for halbot-tray.

from PyInstaller.utils.hooks import collect_submodules

hidden = (
    collect_submodules("grpc")
    + collect_submodules("tray")
    + [
        "halbot._gen.mgmt_pb2",
        "halbot._gen.mgmt_pb2_grpc",
        "pystray._win32",
    ]
)

a = Analysis(
    ["halbot_tray_entry.py"],
    pathex=["."],
    binaries=[],
    datas=[],
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
