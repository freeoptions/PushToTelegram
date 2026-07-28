# -*- mode: python ; coding: utf-8 -*-

import pathlib

from PyInstaller.depend import bindepend

datas = [
    ('push_to_bili_qt.ico', '.'),
    ('check_mark_green.png', '.'),
]
binaries = []
hiddenimports = [
    'shadowcopy',
    'wmi',
]


_orig_get_paths_for_parent_directory_preservation = bindepend._get_paths_for_parent_directory_preservation


def _safe_get_paths_for_parent_directory_preservation():
    try:
        return _orig_get_paths_for_parent_directory_preservation()
    except PermissionError:
        import site
        import sys

        orig_paths = []
        for path in site.getsitepackages():
            if path:
                orig_paths.append(path)

        user_site = site.getusersitepackages()
        if user_site:
            try:
                user_site_path = pathlib.Path(user_site)
                if user_site_path.is_dir():
                    orig_paths.append(user_site)
            except PermissionError:
                pass

        excluded_paths = {
            pathlib.Path(sys.base_prefix),
            pathlib.Path(sys.base_prefix).resolve(),
            pathlib.Path(sys.prefix),
            pathlib.Path(sys.prefix).resolve(),
        }

        resolved_paths = []
        for path in orig_paths:
            try:
                resolved_paths.append(pathlib.Path(path).resolve())
            except PermissionError:
                continue
        orig_paths.extend(resolved_paths)

        paths = set()
        for path in orig_paths:
            if not path:
                continue
            path = pathlib.Path(path)
            try:
                if not path.is_dir():
                    continue
            except PermissionError:
                continue
            if path in excluded_paths:
                continue
            paths.add(path)

        return sorted(paths, key=lambda x: len(x.parents), reverse=True)


bindepend._get_paths_for_parent_directory_preservation = _safe_get_paths_for_parent_directory_preservation


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
    name='PushToBili',
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
    icon='push_to_bili_icon.ico',
)
