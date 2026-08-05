# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['gui.py'],
    pathex=[],
    binaries=[],
    datas=[('weread_exporter\\hook.js', 'weread_exporter'), ('weread_exporter\\style.css', 'weread_exporter'), ('weread_exporter\\epub.css', 'weread_exporter'), ('weread_exporter\\bin\\win32', 'weread_exporter\\bin\\win32'), ('assets\\weread_exporter.ico', 'assets')],
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
    a.binaries,
    a.datas,
    [],
    name='WeReadExporterGUI',
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
    icon='assets\\weread_exporter.ico',
    version='version_file.txt',
)
