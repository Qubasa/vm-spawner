import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def user_data_dir() -> Path:
    if sys.platform == "win32":
        return Path(
            Path(os.getenv("LOCALAPPDATA", Path("~\\AppData\\Local\\").expanduser()))
        )
    xdg_data = os.getenv("XDG_DATA_HOME")
    if xdg_data:
        return Path(xdg_data)
    if sys.platform == "darwin":
        return Path("~/Library/Application Support/").expanduser()
    return Path("~/.local/share").expanduser()


def user_cache_dir() -> Path:
    if sys.platform == "win32":
        return Path(
            Path(os.getenv("LOCALAPPDATA", Path("~\\AppData\\Local\\").expanduser()))
        )
    xdg_cache = os.getenv("XDG_CACHE_HOME")
    if xdg_cache:
        return Path(xdg_cache)
    if sys.platform == "darwin":
        return Path("~/Library/Caches/").expanduser()
    return Path("~/.cache").expanduser()
