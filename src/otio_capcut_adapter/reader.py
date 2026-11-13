"""Reading a CapCut draft as an OpenTimelineIO timeline.

The inverse of the writer, by the same rules, so a timeline written and read
back keeps every shot where it was:

- Video and audio tracks become OTIO tracks of clips and gaps.
- A clip's ``source_range`` starts where the segment starts in its media and
  lasts as long as the segment lasts on the timeline. Speed becomes a
  ``LinearTimeWarp``, which says how much media plays in that time.
- Text tracks become markers on the timeline's stack, the caption's words as
  the marker's name: the convention the writer reads captions from.
- Volume other than 1 is kept as ``metadata["capcut"]["volume"]``.
- Sticker, effect and filter tracks have no OTIO equivalent and are reported.

Times are converted from CapCut's microseconds to whole frames at the
draft's frame rate, rounding where each item starts and ends rather than its
length, so lengths never drift.
"""

import warnings
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import opentimelineio as otio
from opentimelineio import url_utils

from otio_capcut_adapter.errors import CapCutAdapterError, CapCutWarning
from otio_capcut_adapter.text import text_from_content

TIMELINE_FILES = ("draft_info.json", "draft_content.json", "template-2.tmp")


def _warn(message: str) -> None:
    warnings.warn(message, CapCutWarning, stacklevel=3)


def read_from_file(filepath: str) -> otio.schema.Timeline:
    """Read the CapCut draft at ``filepath``: the draft folder, or a file inside it."""
    folder = Path(filepath).expanduser()
    if folder.is_file():
        folder = folder.parent
    if not folder.is_dir():
        raise CapCutAdapterError(f"{filepath} is not a CapCut draft folder.")
    source, doc = _live_document(folder)

    rate = _rate(doc)
    tl = otio.schema.Timeline(name=doc.get("name") or _project_name(folder) or folder.name)
    tl.metadata["capcut"] = {
        "draft_id": doc.get("id", ""),
        "read_from": source,
        "canvas_config": dict(doc["canvas_config"]) if isinstance(doc.get("canvas_config"), dict) else {},
    }
    if isinstance(doc.get("platform"), dict):
        tl.metadata["capcut"]["app_version"] = doc["platform"].get("app_version", "")

    materials = doc.get("materials") if isinstance(doc.get("materials"), dict) else {}
    by_id: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    for kind in ("videos", "audios", "texts"):
        for m in materials.get(kind) or []:
            if isinstance(m, dict) and m.get("id"):
                by_id[m["id"]] = (kind, m)

    captions = []
    for t_index, track in enumerate(doc.get("tracks") or []):
        kind = track.get("type")
        segments = sorted(
            (s for s in track.get("segments") or [] if isinstance(s, dict)),
            key=lambda s: _range(s, "target_timerange")[0],
        )
        if kind in ("video", "audio"):
            tl.tracks.append(_media_track(track, kind, segments, by_id, rate, t_index))
        elif kind == "text":
            captions.extend(m for m in (_caption(seg, by_id, rate) for seg in segments) if m is not None)
        else:
            if segments:
                _warn(f"Track {t_index} is a CapCut {kind} track, which OpenTimelineIO has no place for; left out.")
    # In time order, whichever text track each caption sat on.
    tl.tracks.markers.extend(sorted(captions, key=lambda m: m.marked_range.start_time.to_seconds()))
    return tl


# ─── which document ─────────────────────────────────────────────────────────


def _read(path: Path) -> Optional[Dict[str, Any]]:
    import json

    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    ok = isinstance(doc, dict) and isinstance(doc.get("tracks"), list) and isinstance(doc.get("materials"), dict)
    return doc if ok else None


def _live_document(folder: Path) -> Tuple[str, Dict[str, Any]]:
    """The document CapCut itself reads: the nested ``Timelines/<id>/`` one when newer builds keep it."""
    import json

    try:
        pointer = json.loads((folder / "Timelines" / "project.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        pointer = None
    if isinstance(pointer, dict):
        timeline_id = pointer.get("main_timeline_id") or pointer.get("id")
        if isinstance(timeline_id, str) and timeline_id.replace("-", "").replace("_", "").isalnum():
            for name in ("draft_info.json", "draft_content.json"):
                doc = _read(folder / "Timelines" / timeline_id / name)
                if doc is not None:
                    return f"Timelines/{timeline_id}/{name}", doc
    for name in TIMELINE_FILES:
        doc = _read(folder / name)
        if doc is not None:
            return name, doc
    raise CapCutAdapterError(
        f"No readable timeline in {folder}. CapCut encrypts drafts in some versions; those cannot be read."
    )


def _project_name(folder: Path) -> str:
    """The name CapCut shows for the project. The app keeps it in the sidecar and often leaves the timeline's blank."""
    import json

    try:
        meta = json.loads((folder / "draft_meta_info.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return ""
    name = meta.get("draft_name") if isinstance(meta, dict) else None
    return name if isinstance(name, str) else ""


# ─── time ───────────────────────────────────────────────────────────────────


def _rate(doc: Dict[str, Any]) -> float:
    fps = doc.get("fps")
    return float(fps) if isinstance(fps, (int, float)) and fps > 0 else 30.0


def _frames(us: float, rate: float) -> int:
    return int(round(us * rate / 1_000_000))


def _rt(frames: int, rate: float) -> otio.opentime.RationalTime:
    return otio.opentime.RationalTime(frames, rate)


def _range(seg: Dict[str, Any], key: str) -> Tuple[int, int]:
    r = seg.get(key)
    if not isinstance(r, dict):
        return 0, 0
    return int(r.get("start") or 0), int(r.get("duration") or 0)


# ─── tracks ─────────────────────────────────────────────────────────────────


def _media_track(track, kind, segments, by_id, rate, t_index) -> otio.schema.Track:
    otio_kind = otio.schema.TrackKind.Video if kind == "video" else otio.schema.TrackKind.Audio
    out = otio.schema.Track(name=track.get("name") or "", kind=otio_kind)
    cursor = 0  # frames
    for seg in segments:
        start_us, dur_us = _range(seg, "target_timerange")
        start, end = _frames(start_us, rate), _frames(start_us + dur_us, rate)
        if end <= start:
            continue
        if start < cursor:
            _warn(f"Track {t_index}: overlapping segments; the later one is placed after the earlier.")
            start, end = cursor, cursor + (end - start)
        if start > cursor:
            out.append(otio.schema.Gap(source_range=otio.opentime.TimeRange(_rt(0, rate), _rt(start - cursor, rate))))
        out.append(_clip(seg, by_id, rate, end - start))
        cursor = end
    return out


def _clip(seg, by_id, rate, duration_frames) -> otio.schema.Clip:
    kind, material = by_id.get(seg.get("material_id"), (None, {}))
    name = material.get("material_name") or material.get("name") or ""
    path = material.get("path") or ""
    source_start = _frames(_range(seg, "source_timerange")[0], rate)

    if path:
        ref: otio.core.MediaReference = otio.schema.ExternalReference(target_url=url_utils.url_from_filepath(path))
        length = material.get("duration")
        if isinstance(length, (int, float)) and length > 0 and material.get("type") != "photo":
            ref.available_range = otio.opentime.TimeRange(_rt(0, rate), _rt(_frames(length, rate), rate))
    else:
        ref = otio.schema.MissingReference()
    ref.name = name

    clip = otio.schema.Clip(
        name=Path(name).stem if name else "",
        media_reference=ref,
        source_range=otio.opentime.TimeRange(_rt(source_start, rate), _rt(duration_frames, rate)),
    )
    speed = seg.get("speed")
    if isinstance(speed, (int, float)) and speed > 0 and abs(speed - 1) > 1e-9:
        clip.effects.append(otio.schema.LinearTimeWarp(time_scalar=float(speed)))
    if seg.get("reverse"):
        _warn(f"{name or 'A clip'} plays in reverse in CapCut; OpenTimelineIO gets it forwards.")
    volume = seg.get("volume")
    capcut = {"segment_id": seg.get("id", "")}
    if isinstance(volume, (int, float)) and abs(volume - 1) > 1e-9:
        capcut["volume"] = float(volume)
    clip.metadata["capcut"] = capcut
    if seg.get("visible") is False:
        clip.enabled = False
    return clip


def _caption(seg, by_id, rate) -> Optional[otio.schema.Marker]:
    kind, material = by_id.get(seg.get("material_id"), (None, {}))
    words = text_from_content(material.get("content")) if kind == "texts" else None
    if not words:
        return None
    start_us, dur_us = _range(seg, "target_timerange")
    start = _frames(start_us, rate)
    duration = _frames(start_us + dur_us, rate) - start
    return otio.schema.Marker(
        name=words,
        marked_range=otio.opentime.TimeRange(_rt(start, rate), _rt(max(duration, 0), rate)),
        metadata={"capcut": {"kind": "caption"}},
    )
