# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Beads Manager — one-folder, Windows
# Build: pyinstaller beads_manager.spec (run from project root)

block_cipher = None

a = Analysis(
    ['run.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('app/templates', 'app/templates'),
        ('app/static',    'app/static'),
        ('migrations',    'migrations'),
    ],
    hiddenimports=[
        # Flask internals
        'flask', 'jinja2', 'jinja2.ext', 'werkzeug', 'werkzeug.serving',
        'werkzeug.middleware.proxy_fix', 'click', 'itsdangerous',
        # openpyxl — lazy-imported modules PyInstaller misses
        'openpyxl',
        'openpyxl.cell._writer',
        'openpyxl.styles.differential',
        'openpyxl.styles.stylesheet',
        'openpyxl.worksheet._writer',
        'openpyxl.worksheet._reader',
        'openpyxl.drawing.spreadsheet_drawing',
        'openpyxl.chart',
        # stdlib
        'sqlite3', '_sqlite3',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'email', 'html', 'http', 'xml',
              'xmlrpc', 'distutils', 'test', '_tkinter'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='BeadsManager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # no black console window on Windows
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='BeadsManager',
)
