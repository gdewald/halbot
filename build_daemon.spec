# -*- mode: python ; coding: utf-8 -*-
# PyInstaller onedir spec for halbot-daemon.

from PyInstaller.utils.hooks import collect_submodules

hidden = collect_submodules("grpc") + [
    "halbot._gen.mgmt_pb2",
    "halbot._gen.mgmt_pb2_grpc",
]

a = Analysis(
    ["halbot_daemon_entry.py"],
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
    name="halbot-daemon",
    debug=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="halbot-daemon",
)
