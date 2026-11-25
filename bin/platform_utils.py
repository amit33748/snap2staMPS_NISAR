"""Cross-platform path, SNAP GPT discovery, and UTF-8 console helpers for Windows, Linux, and macOS."""

import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Optional


def configure_utf8_console() -> None:
    """Ensure stdout/stderr never fail on cp1252 Windows consoles when printing UTF-8 symbols."""
    for stream in (sys.stdout, sys.stderr):
        reconfig = getattr(stream, "reconfigure", None)
        if callable(reconfig):
            try:
                reconfig(encoding="utf-8", errors="replace")
            except Exception:
                pass


def default_project_folder() -> str:
    """Return an OS-agnostic default workspace directory under user home."""
    return (Path.home() / "NISAR_Project").as_posix()


def default_snap_folder() -> str:
    """Return the conventional ESA SNAP installation directory for the active OS."""
    sys_name = platform.system()
    if sys_name == "Windows":
        for cand in (r"C:\Program Files\esa-snap", r"C:\Program Files\snap"):
            if os.path.isdir(cand):
                return cand.replace("\\", "/")
        return "C:/Program Files/esa-snap"
    if sys_name == "Darwin":
        for cand in (
            "/Applications/esa-snap",
            "/Applications/snap",
            str(Path.home() / "esa-snap"),
            str(Path.home() / "snap"),
        ):
            if os.path.isdir(cand):
                return cand
        return "/Applications/esa-snap"
    # Linux / POSIX
    for cand in (
        str(Path.home() / "esa-snap"),
        str(Path.home() / "snap"),
        "/usr/local/esa-snap",
        "/usr/local/snap",
        "/opt/esa-snap",
        "/opt/snap",
    ):
        if os.path.isdir(cand):
            return cand
    return (Path.home() / "esa-snap").as_posix()


def default_snap_home() -> str:
    """Return the user's .snap directory path across Windows, Linux, and macOS."""
    return (Path.home() / ".snap").as_posix()


def find_gpt_executable(config=None) -> Optional[str]:
    """Locate ESA SNAP gpt executable across Windows, Linux, and macOS."""
    snap_folder = ""
    if config is not None:
        try:
            snap_folder = config.get("SNAP_GPT", "SNAP_INSTALLATION_FOLDER", fallback="").strip()
        except Exception:
            try:
                snap_folder = str(config["SNAP_GPT"].get("SNAP_INSTALLATION_FOLDER", "")).strip()
            except Exception:
                snap_folder = ""

    candidates = []
    if snap_folder:
        candidates.extend([
            os.path.join(snap_folder, "bin", "gpt.exe"),
            os.path.join(snap_folder, "bin", "gpt"),
            snap_folder,
        ])

    for cmd in ("gpt.exe", "gpt"):
        hit = shutil.which(cmd)
        if hit:
            candidates.append(hit)

    sys_name = platform.system()
    if sys_name == "Windows":
        candidates.extend([
            r"C:\Program Files\esa-snap\bin\gpt.exe",
            r"C:\Program Files\snap\bin\gpt.exe",
            r"C:\Program Files (x86)\esa-snap\bin\gpt.exe",
            r"C:\Program Files (x86)\snap\bin\gpt.exe",
            r"D:\Program Files\esa-snap\bin\gpt.exe",
            r"D:\Program Files\snap\bin\gpt.exe",
            os.path.expanduser(r"~\AppData\Local\Programs\esa-snap\bin\gpt.exe"),
            os.path.expanduser(r"~\AppData\Local\Programs\snap\bin\gpt.exe"),
        ])
    elif sys_name == "Darwin":
        candidates.extend([
            "/Applications/esa-snap/bin/gpt",
            "/Applications/snap/bin/gpt",
            "/Applications/esa-snap.app/Contents/Resources/esa-snap/bin/gpt",
            os.path.expanduser("~/esa-snap/bin/gpt"),
            os.path.expanduser("~/snap/bin/gpt"),
            "/usr/local/esa-snap/bin/gpt",
            "/usr/local/snap/bin/gpt",
        ])
    else:
        candidates.extend([
            os.path.expanduser("~/esa-snap/bin/gpt"),
            os.path.expanduser("~/snap/bin/gpt"),
            "/usr/local/esa-snap/bin/gpt",
            "/usr/local/snap/bin/gpt",
            "/opt/esa-snap/bin/gpt",
            "/opt/snap/bin/gpt",
        ])

    for cand in candidates:
        if cand and os.path.isfile(cand):
            if sys_name == "Windows" or os.access(cand, os.X_OK):
                return os.path.abspath(cand)

    return None
