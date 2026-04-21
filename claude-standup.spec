# -*- mode: python ; coding: utf-8 -*-
a = Analysis(
    ['claude_standup/cli.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['claude_standup.daemon', 'claude_standup.service', 'claude_standup.llm', 'claude_standup.classifier', 'claude_standup.reporter', 'claude_standup.parser', 'claude_standup.cache', 'claude_standup.models'],
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
    name='claude-standup',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)
