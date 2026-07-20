# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for 인수인계10분 (handover-analyzer-mvp1).

onefile / windowed(no console) build. Entry point is app/main.py, matching
the project's real run command `python app/main.py` (see CLAUDE.md).
"""

from PyInstaller.building.splash import Splash
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

APP_NAME = "인수인계10분"

# googleapiclient.discovery.build() loads bundled API discovery JSON at
# runtime (app/services/package_loader.py) - make sure those data files ride
# along in the frozen exe, not just the .py modules.
datas = [
    ("style.qss", "."),
]
datas += collect_data_files("googleapiclient")

a = Analysis(
    ["app/main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Onefile exe launches take ~15s to unpack before the Qt main window can show
# up (see app/main.py's single-instance check + MainWindow construction) -
# without this, a user who thinks nothing happened will double-click again.
# text_pos is intentionally omitted: the "불러오는 중..." caption is baked
# into assets/splash.png itself, so no separate pyi_splash.update_text() area
# is needed.
splash = Splash(
    "assets/splash.png",
    binaries=a.binaries,
    datas=a.datas,
)

exe = EXE(
    pyz,
    a.scripts,
    splash,
    a.binaries,
    a.zipfiles,
    a.datas,
    splash.binaries,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
