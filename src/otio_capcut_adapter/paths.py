"""Where CapCut keeps its drafts.

A plain helper, not part of the adapter: OpenTimelineIO only ever calls an
adapter's read and write functions.
"""

import os
import sys
from pathlib import Path
from typing import Optional

from otio_capcut_adapter.errors import CapCutAdapterError

_RELATIVE = Path("CapCut") / "User Data" / "Projects" / "com.lveditor.draft"


def drafts_dir(must_exist: bool = True, platform: Optional[str] = None) -> Path:
    """CapCut's drafts folder on this computer.

    CapCut only lists projects that sit in this folder. It creates the
    folder the first time it runs, so a missing folder almost always means
    CapCut has not been opened yet on this computer.

    ``must_exist``
        Raise ``CapCutAdapterError`` when the folder is not there (default).
    ``platform``
        ``"darwin"`` or ``"win32"``; defaults to this computer's.
    """
    platform = platform or sys.platform
    if platform == "darwin":
        folder = Path.home() / "Movies" / _RELATIVE
    elif platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            raise CapCutAdapterError("LOCALAPPDATA is not set, so CapCut's drafts folder cannot be found.")
        folder = Path(base) / _RELATIVE
    else:
        raise CapCutAdapterError(
            "CapCut's desktop app runs on macOS and Windows only. Pass the drafts folder's path yourself."
        )
    if must_exist and not folder.is_dir():
        raise CapCutAdapterError(
            f"CapCut's drafts folder is not at {folder}. Open CapCut once so it creates it, then try again."
        )
    return folder
