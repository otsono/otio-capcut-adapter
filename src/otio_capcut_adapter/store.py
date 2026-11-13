"""CapCut's drafts folder: finding it, listing a draft in it, and writing straight into it.

CapCut keeps an index of its projects, ``root_meta_info.json``, in the
drafts folder. A new draft is added to it so it shows in CapCut's project
list; a project CapCut already made is in it, and its entry is only brought
up to date. The index is only ever extended, never created: if it is not
there, CapCut has not written one and the adapter does not invent its shape.
"""

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional

from otio_capcut_adapter.errors import CapCutAdapterError
from otio_capcut_adapter.paths import drafts_dir

INDEX_FILE = "root_meta_info.json"


def _entries_key(index: Dict[str, Any]) -> Optional[str]:
    """The list in the index that holds one entry per draft."""
    if isinstance(index.get("all_draft_store"), list):
        return "all_draft_store"
    for key, value in index.items():
        if isinstance(value, list) and any(isinstance(e, dict) and "draft_fold_path" in e for e in value):
            return key
    return None


def register_draft(draft_dir: Path, draft: Dict[str, Any], json_file: str) -> bool:
    """Add the draft to CapCut's project index, or update its entry. Returns whether the index changed.

    ``draft_dir`` must sit directly in the drafts folder; anywhere else there
    is no index to add it to.
    """
    index_path = draft_dir.parent / INDEX_FILE
    if not index_path.is_file():
        return False
    try:
        index = json.loads(index_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    if not isinstance(index, dict):
        return False
    key = _entries_key(index)
    if key is None:
        return False

    now = int(time.time() * 1_000_000)  # CapCut stamps in microseconds
    fold = draft_dir.as_posix()
    entries = index[key]
    entry = next(
        (e for e in entries if isinstance(e, dict)
         and (e.get("draft_fold_path") in (fold, str(draft_dir)) or e.get("draft_id") == draft.get("id"))),
        None,
    )
    if entry is None:
        entries.append({
            "draft_cover": str(draft_dir / "draft_cover.jpg") if (draft_dir / "draft_cover.jpg").is_file() else "",
            "draft_fold_path": fold,
            "draft_id": draft.get("id", ""),
            "draft_is_ai_shorts": False,
            "draft_is_invisible": False,
            "draft_json_file": str(draft_dir / json_file),
            "draft_name": draft.get("name", ""),
            "draft_new_version": "",
            "draft_root_path": str(draft_dir.parent),
            "draft_timeline_materials_size": 0,
            "tm_draft_create": now,
            "tm_draft_modified": now,
            "tm_draft_removed": 0,
            "tm_duration": draft.get("duration", 0),
        })
        if isinstance(index.get("draft_ids"), int):
            index["draft_ids"] += 1
    else:
        entry["tm_draft_modified"] = now
        entry["tm_duration"] = draft.get("duration", 0)

    shutil.copy2(index_path, index_path.with_name(INDEX_FILE + ".bak"))
    tmp = index_path.with_name(f".{INDEX_FILE}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, index_path)
    return True


def install(timeline, name: str, **kwargs: Any) -> Path:
    """Write ``timeline`` into CapCut's drafts folder as the project ``name``, and return its folder.

    If CapCut already has an empty project called ``name``, it is filled:
    the recommended way, since it keeps the identity CapCut gave it. Otherwise
    a new draft is made there, seeded from CapCut's newest project, and added
    to CapCut's project list. Takes the same options as the adapter.
    """
    import opentimelineio as otio

    if not name or any(c in name for c in '/\\:*?"<>|') or name.strip(" .") != name:
        raise CapCutAdapterError(f"{name!r} cannot be a project folder name.")
    folder = drafts_dir() / name
    otio.adapters.write_to_file(timeline, str(folder), adapter_name="capcut", **kwargs)
    return folder
