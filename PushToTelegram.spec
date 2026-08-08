# -*- mode: python ; coding: utf-8 -*-

import pathlib

from PyInstaller.depend import bindepend

datas = [
    ('artemis_symbol.ico', '.'),
    ('assets/artemis_symbol_1024.png', '.'),
    ('check_mark_green.png', '.'),
]
binaries = []
hiddenimports = [
    'shadowcopy',
    'wmi',
]

# The desktop client uses classic Qt Widgets only. PySide's general hook also
# collects optional QML/PDF/virtual-keyboard components and all translations;
# excluding those keeps one-file extraction substantially smaller and faster.
excluded_modules = [
    'PIL',
    'PySide6.QtNetwork',
    'PySide6.QtOpenGL',
    'PySide6.QtPdf',
    'PySide6.QtQml',
    'PySide6.QtQuick',
    'PySide6.QtVirtualKeyboard',
]

excluded_bundle_prefixes = (
    'pyside6\\translations\\',
    'pyside6\\plugins\\generic\\',
    'pyside6\\plugins\\networkinformation\\',
    'pyside6\\plugins\\platforminputcontexts\\',
    'pyside6\\plugins\\tls\\',
)
excluded_bundle_files = {
    'pyside6\\opengl32sw.dll',
    'pyside6\\qt6network.dll',
    'pyside6\\qt6opengl.dll',
    'pyside6\\qt6pdf.dll',
    'pyside6\\qt6qml.dll',
    'pyside6\\qt6qmlmeta.dll',
    'pyside6\\qt6qmlmodels.dll',
    'pyside6\\qt6qmlworkerscript.dll',
    'pyside6\\qt6quick.dll',
    'pyside6\\qt6virtualkeyboard.dll',
    'pyside6\\qtnetwork.pyd',
    'pyside6\\plugins\\platforms\\qdirect2d.dll',
    'pyside6\\plugins\\platforms\\qminimal.dll',
    'pyside6\\plugins\\platforms\\qoffscreen.dll',
}


def _remove_unused_bundle_entries(entries):
    kept = []
    for entry in entries:
        destination = str(entry[0]).replace('/', '\\').casefold()
        if destination in excluded_bundle_files:
            continue
        if destination.startswith(excluded_bundle_prefixes):
            continue
        kept.append(entry)
    return kept


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
    excludes=excluded_modules,
    noarchive=False,
    optimize=1,
)
a.binaries = _remove_unused_bundle_entries(a.binaries)
a.datas = _remove_unused_bundle_entries(a.datas)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PushToTelegram',
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
    icon='artemis_symbol.ico',
)
