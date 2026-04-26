# -*- mode: python ; coding: utf-8 -*-
# PyInstaller onedir spec for halbot-daemon.

from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_submodules,
    collect_data_files,
)

# collect_all(pkg) = data + binaries + submodules in one go. Covers all the
# pieces each of these packages hides at runtime (CUDA DLLs under nvidia/*/bin,
# espeak-ng-data next to espeak-ng.dll, spaCy model tree, BCP47 JSON tables,
# etc.) without hand-curating which sub-bits to pull.
_pkgs = [
    "kokoro",
    "misaki",
    "transformers",
    "spacy",
    "en_core_web_sm",
    "language_tags",
    "espeakng_loader",
    "nvidia",
    "ctranslate2",
]
datas, binaries, hidden = [], [], []
for _p in _pkgs:
    _d, _b, _h = collect_all(_p)
    datas += _d
    binaries += _b
    hidden += _h

# Ship the built React dashboard so /halbot-stats can render a static snapshot
# from inside the daemon. Mounted at halbot/web to avoid clashing with tray's
# dashboard/web mount. paths.frontend_dist_dir() resolves the same target.
_fe_dist = Path(SPECPATH) / "frontend" / "dist"
if _fe_dist.exists():
    datas.append((str(_fe_dist), "halbot/web"))

a = Analysis(
    ["halbot_daemon_entry.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas + collect_data_files("halbot"),
    hiddenimports=(
        hidden
        + collect_submodules("grpc")
        + collect_submodules("halbot")
        + ["halbot._gen.mgmt_pb2", "halbot._gen.mgmt_pb2_grpc"]
    ),
    # pyz+py ships .py source alongside .pyc so runtime code that does
    # open(cls.__module__.__file__)  (transformers 4.57's
    # _can_set_experts_implementation) or  Path(__file__).parent
    # (spacy.util.load_model_from_init_py) resolves correctly.
    module_collection_mode={
        "kokoro": "pyz+py",
        "misaki": "pyz+py",
        "transformers": "pyz+py",
        "spacy": "pyz+py",
        "en_core_web_sm": "pyz+py",
    },
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
)

# Strip MSVC runtime DLLs — PyInstaller's dep scan walks PATH and can pick up
# an old copy (e.g. JDK/bin/msvcp140.dll v14.36) that's older than the system
# Microsoft VC++ Redistributable. torch 2.11 c10.dll is built against modern
# runtime and segfaults inside msvcp140 DllMain when it finds the old one
# alongside itself. Removing these forces the Windows loader to resolve from
# System32, which is always current.
_msvc_runtime = {"msvcp140.dll", "msvcp140_1.dll", "vcruntime140.dll", "vcruntime140_1.dll"}
a.binaries = [b for b in a.binaries if b[0].split("/")[-1].split("\\")[-1].lower() not in _msvc_runtime]
a.datas = [d for d in a.datas if d[0].split("/")[-1].split("\\")[-1].lower() not in _msvc_runtime]

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
