# -*- mode: python ; coding: utf-8 -*-
import os
import sys


def _find_sqlite_dll():
    """Localiza o sqlite3.dll (o Python do conda guarda em Library\\bin).

    Sem isto o PyInstaller nao copia a DLL e o executavel falha ao iniciar com
    "ImportError: DLL load failed while importing _sqlite3".
    """
    candidates = [
        os.path.join(sys.base_prefix, "Library", "bin", "sqlite3.dll"),
        os.path.join(sys.base_prefix, "DLLs", "sqlite3.dll"),
        os.path.join(sys.base_prefix, "sqlite3.dll"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _conda_binaries():
    """DLLs do conda que o PyInstaller nao encontra sozinho (sqlite, lzma...)."""
    library_bin = os.path.join(sys.base_prefix, "Library", "bin")
    names = [
        "sqlite3.dll",
        "liblzma.dll",
        "libbz2.dll",
        "libmpdec-4.dll",
        "libcrypto-3-x64.dll",
        "libssl-3-x64.dll",
        # O _ctypes.pyd do conda depende do libffi; sem esta DLL o exe fecha
        # com "ImportError: DLL load failed while importing _ctypes".
        "ffi.dll",
        "ffi-8.dll",
    ]
    found = []
    for name in names:
        path = os.path.join(library_bin, name)
        if os.path.isfile(path):
            found.append((path, "."))
    return found


binaries = _conda_binaries()
_sqlite_dll = _find_sqlite_dll()
if _sqlite_dll and not any(os.path.basename(src).lower() == "sqlite3.dll" for src, _ in binaries):
    binaries.append((_sqlite_dll, "."))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=[('justatest.jpg', '.'), ('justatest.ico', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='main',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='justatest.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='main',
)
