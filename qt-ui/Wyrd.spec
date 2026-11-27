# PyInstaller one-file build for the Wyrd Qt6 desktop application.
from PyInstaller.utils.hooks import collect_all
from pathlib import Path

qt_datas, qt_binaries, qt_hiddenimports = collect_all("PySide6")

a = Analysis(
    ["main.py"],
    pathex=[str(Path.cwd())],
    binaries=qt_binaries,
    datas=qt_datas,
    hiddenimports=qt_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "test", "unittest"],
    noarchive=False,
)

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Wyrd",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
