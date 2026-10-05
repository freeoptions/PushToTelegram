from __future__ import annotations

import ctypes
import os
import sys


_DLL_HANDLES = []

if sys.platform == "win32" and getattr(sys, "frozen", False):
    bundle_dir = os.path.abspath(getattr(sys, "_MEIPASS", ""))
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    system_dll_dir = os.path.join(system_root, "System32")
    dll_dirs = [
        bundle_dir,
        os.path.join(bundle_dir, "PySide6"),
        os.path.join(bundle_dir, "shiboken6"),
        system_dll_dir,
    ]
    existing_dirs = [path for path in dll_dirs if os.path.isdir(path)]
    for path in existing_dirs:
        _DLL_HANDLES.append(os.add_dll_directory(path))
    os.environ["PATH"] = os.pathsep.join(existing_dirs + [os.environ.get("PATH", "")])

    # PySide6's extension modules can still fail to resolve their sibling
    # DLLs in a one-file build even after the DLL directories are added.
    # Load the dependency chain explicitly before app.py imports QtCore.
    preload_names = (
        os.path.join("PySide6", "icuuc.dll"),
        os.path.join("shiboken6", "shiboken6.abi3.dll"),
        os.path.join("PySide6", "Qt6Core.dll"),
        os.path.join("PySide6", "pyside6.abi3.dll"),
        os.path.join("PySide6", "Qt6Gui.dll"),
        os.path.join("PySide6", "Qt6Widgets.dll"),
    )
    for name in preload_names:
        path = os.path.join(bundle_dir, name)
        if os.path.isfile(path):
            try:
                _DLL_HANDLES.append(ctypes.WinDLL(path))
            except (OSError, ImportError):
                # ICU is also available in Windows System32. If a host has
                # already loaded an incompatible ICU, do not let this helper
                # turn a recoverable DLL selection issue into a hard crash.
                if os.path.basename(path).casefold() != "icuuc.dll":
                    raise
