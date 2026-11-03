# -*- mode: python ; coding: utf-8 -*-
# 打包命令：python -m PyInstaller --clean --noconfirm open24g-batmon.spec
# 产物：dist/设备电量监控.exe（单文件、无控制台、内嵌图标）
# configs/ 与 user_settings.json / devices.json 均不打包，运行时从 exe 同目录读取。


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[('icon.ico', '.')],
    hiddenimports=['PySide6.QtSvg', 'yaml'],
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
    a.binaries,
    a.datas,
    [],
    name='设备电量监控',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
)
