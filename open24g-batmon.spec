# -*- mode: python ; coding: utf-8 -*-
# 打包命令：python -m PyInstaller --clean --noconfirm open24g-batmon.spec
#
# 产物：dist/设备电量监控/ 目录
#         ├── 设备电量监控.exe
#         └── _internal/          （Python + Qt 运行时，约 110MB）
#
# 为什么用「目录版（onedir）」而不是「单文件（onefile）」：
#   onefile 的 exe 每次启动都要把 ~112MB 运行时解包到 %TEMP%\_MEIxxxxxx，
#   正常退出才会自己删掉；一旦崩溃或被任务管理器强杀，这个目录就永久残留。
#   实测一周可堆积 8~9GB 垃圾，而且每次冷启动都要多花 3~5 秒解包。
#   onedir 只在解压发布包时落盘一次，之后启动直接加载，既快又不留垃圾。
#   代价：exe 必须和 _internal/ 放在一起，不能单独拷走。见 docs/quick-start.txt。
#
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
    [],
    exclude_binaries=True,      # 运行时文件交给 COLLECT 落地，不打进 exe
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

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='设备电量监控',
)
