# PyInstaller spec for the standalone UNSeQueL executable.
#
# Build from the repository root:
#     python -m PyInstaller packaging/unsequel.spec --noconfirm
#
# The result is a single-file executable (dist/unsequel.exe on Windows,
# dist/unsequel elsewhere) that bundles Python, the `unsequel` package, and
# DuckDB, so it runs without any installed toolchain.

import os

from PyInstaller.utils.hooks import collect_all

SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))
ROOT = os.path.dirname(SPEC_DIR)

duckdb_datas, duckdb_binaries, duckdb_hiddenimports = collect_all("duckdb")

a = Analysis(
    [os.path.join(SPEC_DIR, "entry.py")],
    pathex=[ROOT],
    binaries=duckdb_binaries,
    datas=duckdb_datas,
    hiddenimports=duckdb_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="unsequel",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
