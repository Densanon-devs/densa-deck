# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Densa Deck desktop binary.

Build with:
    pyinstaller densa-deck.spec --clean

Output:
    dist/densa-deck/        (folder mode — faster startup, smaller per-file)
    dist/densa-deck.exe     (single-file mode — slower startup, easier to ship)

This spec uses folder mode by default for better performance.
"""

from PyInstaller.utils.hooks import collect_submodules

# Collect all submodules of our package and key dependencies
hidden_imports = (
    collect_submodules("densa_deck")
    + collect_submodules("rich")
    + collect_submodules("pydantic")
    + collect_submodules("httpx")
    # pywebview is optional but add it when present so the desktop app
    # ships inside the bundle without needing a separate install.
    + collect_submodules("webview", on_error="ignore")
)

a = Analysis(
    ["src/densa_deck/__main__.py"],
    pathex=["src"],
    binaries=[],
    # Ship the desktop app's HTML/CSS/JS assets inside the bundle. Without
    # this PyInstaller strips the static/ dir and the frozen app launches
    # with a blank window.
    datas=[
        ("src/densa_deck/app/static/*", "densa_deck/app/static"),
    ],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "pandas",
        "PIL",
        "test",
        "tests",
        "pytest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="densa-deck",
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
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="densa-deck",
)
