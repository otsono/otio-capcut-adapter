"""
OpenTimelineIO adapter: write a timeline as a CapCut draft.

A CapCut draft is a FOLDER, not a file, so this adapter only implements
``write_to_file`` and ``filepath`` is the draft folder.

How CapCut decides whether to open a draft is undocumented and changes
between versions. What is known (largely from the version notes kept by
capcut-cli, https://github.com/renezander030/capcut-cli) shapes the three
ways this adapter writes, safest first:

1. **Fill a project CapCut created.** Make an empty project in CapCut, quit
   CapCut, and write into that project's folder. The draft keeps the id,
   version markers and registration the app gave it, which is what current
   builds check. This is the recommended way.
2. **Seed a new draft from a real project** (``template=<project folder>``),
   or automatically from the newest app-made project in the folder the new
   draft is written into (CapCut's drafts folder). CapCut 8.4+ refuses drafts
   whose version markers look tool-made ("from an unusual path"), so the new
   draft borrows a real project's markers and settings, never its content.
3. **A bare skeleton**, when neither is available. Current builds may refuse
   it; a warning says so.

What is written, and what is not. Nothing is dropped silently: a loss
that changes the structure of the edit (a nested stack, a track kind CapCut
has no place for) raises ``NotSupportedError``, as OpenTimelineIO's other
adapters do; a smaller loss (a marker, a transition written as a cut) is a
``CapCutWarning``.

- Video and audio tracks, in order. The first video track is CapCut's main
  track, which CapCut makes gapless when it opens the draft, so gaps there
  are reported: the app will pull later shots left.
- Clips with their source ranges; gaps as offsets; ``LinearTimeWarp`` as
  speed. Timing follows OpenTimelineIO's own rule: a clip occupies
  ``source_range.duration`` on its track, and a time warp changes how much
  media plays in that time (``duration x time_scalar``).
- Clip volume from ``metadata["capcut"]["volume"]`` when present.
- Media follows ``media_policy``, with the same names and the same strict
  default as OpenTimelineIO's own ``otiod`` bundle adapter.
- A cover for CapCut's project list, from a random early frame (ffmpeg).
- Not yet: markers / captions, transitions, nested stacks, other effects.

Times in a CapCut draft are integer MICROSECONDS throughout.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import random
import shutil
import subprocess
import time
import uuid
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import opentimelineio as otio
from opentimelineio import url_utils
from opentimelineio.adapters.file_bundle_utils import MediaReferencePolicy, NotAFileOnDisk

from otio_capcut_adapter.errors import CapCutAdapterError, CapCutWarning

#: The timeline documents a draft folder can hold, in the order CapCut
#: versions are known to prefer them as the primary.
TIMELINE_FILES = ("draft_info.json", "draft_content.json", "template-2.tmp")

#: Material lists every draft written here carries, even when empty.
MATERIAL_KEYS = (
    "videos", "audios", "texts", "stickers", "video_effects", "transitions",
    "masks", "chromas", "audio_fades", "audio_effects", "canvases", "speeds",
    "sound_channel_mappings", "vocal_separations", "placeholders",
    "placeholder_infos", "material_colors", "common_mask",
)

#: Groups in ``draft_meta_info.json``; type 0 is the imported-media group.
IMPORTED_MEDIA_GROUP = 0
META_GROUP_TYPES = (0, 1, 2, 3, 6, 7, 8)

PHOTO_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "bmp", "tif", "tiff", "heic"}

#: The cover is drawn from a random moment in the first COVER_WINDOW_S
#: seconds of the main track, or its first quarter if that is shorter.
COVER_WINDOW_S = 3.0
COVER_MAX_SIDE = 1280
COVER_FILE = "draft_cover.jpg"

#: Stacking order CapCut writes for each kind of segment.
RENDER_INDEX = {"video": 14000, "audio": 11000}


#: Fields CapCut 8.7 writes on every segment, at the values it writes for a
#: plain, unedited clip. Copied from an app-made draft (capcut-cli's
#: ``capcut-8.7-windows-active`` fixture) so ours carry the same set.
SEGMENT_DEFAULTS: Dict[str, Any] = {
    "render_timerange": {"start": 0, "duration": 0}, "desc": "", "state": 0, "is_loop": False,
    "is_tone_modify": False, "intensifies_audio": False, "cartoon": False, "last_nonzero_volume": 1.0,
    "uniform_scale": {"on": True, "value": 1.0}, "enable_lut": True, "enable_adjust": True, "enable_hsl": False,
    "group_id": "", "enable_color_curves": True, "enable_hsl_curves": True,
    "hdr_settings": {"mode": 1, "intensity": 1.0, "nits": 1000}, "enable_color_wheels": True,
    "is_placeholder": False, "template_id": "", "enable_smart_color_adjust": False, "template_scene": "default",
    "caption_info": None,
    "responsive_layout": {"enable": False, "target_follow": "", "size_layout": 0, "horizontal_pos_layout": 0,
                          "vertical_pos_layout": 0},
    "enable_color_match_adjust": False, "enable_color_correct_adjust": False, "enable_adjust_mask": False,
    "lyric_keyframes": None, "enable_video_mask": True, "digital_human_template_group_id": "",
    "color_correct_alg_result": "", "source": "segmentsourcenormal", "enable_mask_stroke": False,
    "enable_mask_shadow": False, "enable_color_adjust_pro": False,
}

#: The same, for video materials.
VIDEO_MATERIAL_DEFAULTS: Dict[str, Any] = {
    "unique_id": "", "reverse_intensifies_path": "", "cartoon_path": "", "material_id": "", "crop_ratio": "free",
    "audio_fade": None, "crop_scale": 1.0,
    "matting": {"flag": 0, "path": "", "interactiveTime": [], "has_use_quick_brush": False, "strokes": [],
                "has_use_quick_eraser": False, "expansion": 0, "feather": 0, "reverse": False,
                "custom_matting_id": "", "enable_matting_stroke": False, "is_clould": False, "mask_video_path": "",
                "cloud_product_fps": 0},
    "source": 0, "is_set_beauty_mode": False, "smart_motion": None, "multi_camera_info": None,
    "picture_from": "none", "picture_set_category_id": "", "picture_set_category_name": "",
    "has_sound_separated": False, "aigc_type": "none", "aigc_history_id": "", "aigc_item_id": "",
    "local_material_from": "", "smart_match_info": None, "beauty_face_preset_infos": [],
    "beauty_body_preset_id": "", "beauty_face_auto_preset": {"preset_id": "", "name": "", "rate_map": "", "scene": ""},
    "beauty_face_auto_preset_infos": [], "beauty_body_auto_preset": None, "live_photo_timestamp": -1,
    "live_photo_cover_path": "", "content_feature_info": None, "corner_pin": None, "surface_trackings": [],
    "video_mask_stroke": {"resource_id": "", "path": "", "type": "", "color": "", "size": 0, "alpha": 0,
                          "distance": 0, "texture": 0, "horizontal_shift": 0, "vertical_shift": 0},
    "video_mask_shadow": {"resource_id": "", "path": "", "color": "", "alpha": 0, "blur": 0, "distance": 0,
                          "angle": 0},
}


def _warn(message: str) -> None:
    warnings.warn(message, CapCutWarning, stacklevel=3)


# ─── entry point ────────────────────────────────────────────────────────────


def write_to_file(
    input_otio: otio.schema.Timeline,
    filepath: str,
    template: Optional[str] = None,
    copy_media: bool = True,
    width: Optional[int] = None,
    height: Optional[int] = None,
    overwrite: bool = False,
    name: Optional[str] = None,
    media_policy: str = MediaReferencePolicy.ErrorIfNotFile,
    dryrun: bool = False,
    cover: Any = None,
    cover_seed: Optional[int] = None,
    ffmpeg: Optional[str] = None,
) -> Optional[int]:
    """Write ``input_otio`` as the CapCut draft folder ``filepath``.

    ``filepath`` an existing CapCut project folder
        Fill it (the recommended way). Its timeline must be empty unless
        ``overwrite=True``; overwritten files get a ``.bak`` copy.
    ``filepath`` a new folder
        Create a draft there, seeded from ``template`` (a real CapCut project
        folder) or from the newest app-made project beside it.
    ``media_policy``
        As in OpenTimelineIO's ``otiod`` adapter (``MediaReferencePolicy``):
        ``ErrorIfNotFile`` (default) raises ``NotAFileOnDisk`` for media that
        is not a local file; ``MissingIfNotFile`` writes such clips as
        placeholders CapCut asks you to relink; ``AllMissing`` writes every
        clip as a placeholder and copies no media.
    ``copy_media``
        Copy media into the draft's ``assets/`` folder (default). CapCut on
        macOS cannot always read files under Desktop, Documents or Downloads,
        and a draft that carries its media survives being moved.
    ``cover``
        The tile CapCut shows in its project list. ``None`` (default): a
        random frame from early in the main track, drawn with ffmpeg.
        A path: that image. ``False``: no cover.
    ``cover_seed``
        Seed for the random pick, for the same cover every time.
    ``ffmpeg``
        Path to ffmpeg. Default: the one on ``PATH``. Without one there is no
        generated cover, and a warning says so.
    ``dryrun``
        Check everything and write nothing. Returns the bytes of media that
        would be copied, as ``otiod`` does.
    ``width`` / ``height``
        Canvas size. Default: the project's own, else 1920x1080.
    ``name``
        Display name for a new draft. Default: the timeline's name, else the
        folder's.
    """
    if not isinstance(input_otio, otio.schema.Timeline):
        raise CapCutAdapterError("The CapCut adapter writes a Timeline.")
    policies = (MediaReferencePolicy.ErrorIfNotFile, MediaReferencePolicy.MissingIfNotFile,
                MediaReferencePolicy.AllMissing)
    if media_policy not in policies:
        raise CapCutAdapterError(f"media_policy must be one of {', '.join(policies)}; got {media_policy!r}.")
    cover_file: Optional[Path] = None
    if cover is not None and cover is not False:
        cover_file = Path(cover).expanduser()
        if not cover_file.is_file():
            raise CapCutAdapterError(f"cover {cover} is not a file.")

    draft_dir = Path(filepath).expanduser().resolve()
    existing = _find_timeline_files(draft_dir) if draft_dir.is_dir() else {}

    if existing:
        mode = "fill"
        primary_name, primary = _read_primary(draft_dir, existing)
        if _has_content(primary) and not overwrite:
            raise CapCutAdapterError(
                f"{draft_dir} already has a timeline. Pass overwrite=True to "
                "replace it, or create an empty project in CapCut and write into that."
            )
        skeleton = _empty_skeleton(primary)
    else:
        mode = "new"
        if draft_dir.exists() and any(draft_dir.iterdir()):
            raise CapCutAdapterError(f"{draft_dir} exists, is not empty, and is not a CapCut project.")
        donor = _donor_timeline(Path(template).expanduser() if template else None, draft_dir.parent)
        if donor is None:
            _warn(
                "No real CapCut project to seed from, so this draft uses a bare skeleton, which CapCut "
                "8.4 and later may refuse. Create an empty project in CapCut and write into its folder instead."
            )
            skeleton = _bare_skeleton()
        else:
            skeleton = _empty_skeleton(donor)
        skeleton["id"] = str(uuid.uuid4()).upper()
        skeleton["name"] = name or input_otio.name or draft_dir.name

    fps = _timeline_rate(input_otio)
    skeleton["fps"] = float(round(fps)) if abs(fps - round(fps)) < 0.01 else round(fps, 3)
    canvas = _canvas(skeleton, width, height)
    skeleton["canvas_config"] = canvas

    if media_policy == MediaReferencePolicy.AllMissing:
        _warn("media_policy is AllMissing: every clip is a placeholder for CapCut to relink.")
    media = _MediaStager(draft_dir, copy_media)
    draft = _build(input_otio, skeleton, canvas, media, media_policy)

    if dryrun:
        return media.total_bytes()

    sidecar_path = draft_dir / "draft_meta_info.json"
    sidecar = _read_json(sidecar_path) if sidecar_path.exists() else None
    if sidecar is None:
        sidecar = _new_sidecar(draft, draft_dir)
    _register_media(sidecar, draft, media)
    sidecar["tm_duration"] = draft["duration"]
    sidecar["tm_draft_modified"] = int(time.time() * 1_000_000)

    # Everything is built before anything is written.
    writes: List[Tuple[Path, Dict[str, Any]]] = []
    if mode == "fill":
        for file_name in existing:
            doc = _read_json(draft_dir / file_name)
            if doc is None:
                # Newer builds encrypt the mirrors they own. Leave those alone.
                if file_name != primary_name:
                    _warn(f"Left {file_name} alone: it is not plain JSON (CapCut encrypts some files).")
                continue
            writes.append((draft_dir / file_name, _with_own_id(draft, doc)))
        for nested in _nested_timeline_files(draft_dir):
            doc = _read_json(nested)
            if doc is not None:
                writes.append((nested, _with_own_id(draft, doc)))
    else:
        writes.append((draft_dir / "draft_content.json", draft))
        writes.append((draft_dir / "draft_info.json", draft))

    draft_dir.mkdir(parents=True, exist_ok=True)
    media.commit()

    # A cover is never worth failing the export over: any problem is a warning.
    if cover is not False:
        made = _write_cover(draft, draft_dir, canvas, cover_file, cover_seed, ffmpeg, backup=(mode == "fill"))
        if made:
            sidecar["draft_cover"] = COVER_FILE
        elif mode == "new":
            sidecar["draft_cover"] = ""
    elif mode == "new":
        sidecar["draft_cover"] = ""
    writes.append((sidecar_path, sidecar))

    for path, doc in writes:
        if mode == "fill" and path.exists():
            shutil.copy2(path, path.with_name(path.name + ".bak"))
        _write_json(path, doc)
    return None


# ─── reading a project ──────────────────────────────────────────────────────


def _find_timeline_files(draft_dir: Path) -> Dict[str, Path]:
    return {n: draft_dir / n for n in TIMELINE_FILES if (draft_dir / n).is_file()}


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8-sig")
        doc = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def _is_timeline(doc: Optional[Dict[str, Any]]) -> bool:
    return isinstance(doc, dict) and isinstance(doc.get("tracks"), list) and isinstance(doc.get("materials"), dict)


def _read_primary(draft_dir: Path, files: Dict[str, Path]) -> Tuple[str, Dict[str, Any]]:
    for file_name, path in files.items():
        doc = _read_json(path)
        if _is_timeline(doc):
            return file_name, doc  # type: ignore[return-value]
    raise CapCutAdapterError(
        f"None of {', '.join(files)} in {draft_dir} is a readable timeline. "
        "CapCut may have encrypted them; this adapter cannot write into such a project."
    )


def _has_content(doc: Dict[str, Any]) -> bool:
    return any(t.get("segments") for t in doc.get("tracks", []) if isinstance(t, dict))


def _nested_timeline_files(draft_dir: Path) -> List[Path]:
    """The live document newer builds keep at ``Timelines/<main_timeline_id>/``."""
    pointer = _read_json(draft_dir / "Timelines" / "project.json")
    if not pointer:
        return []
    timeline_id = pointer.get("main_timeline_id") or pointer.get("id")
    if not isinstance(timeline_id, str) or not timeline_id.replace("-", "").replace("_", "").isalnum():
        return []
    folder = draft_dir / "Timelines" / timeline_id
    return [folder / n for n in ("draft_info.json", "draft_content.json") if (folder / n).is_file()]


def _donor_timeline(template: Optional[Path], store: Path) -> Optional[Dict[str, Any]]:
    """A real project's timeline, to borrow its version markers and settings."""
    if template is not None:
        files = _find_timeline_files(template)
        if not files:
            raise CapCutAdapterError(f"template {template} is not a CapCut project folder.")
        return _read_primary(template, files)[1]
    best: Optional[Tuple[Tuple[int, ...], Dict[str, Any]]] = None
    if not store.is_dir():
        return None
    for child in store.iterdir():
        if not child.is_dir():
            continue
        for file_name in TIMELINE_FILES:
            doc = _read_json(child / file_name)
            if not _is_timeline(doc) or not isinstance(doc.get("platform"), dict):
                continue
            version = _version_tuple(doc["platform"].get("app_version"))
            if version and (best is None or version > best[0]):
                best = (version, doc)  # type: ignore[assignment]
            break
    return best[1] if best else None


def _version_tuple(value: Any) -> Tuple[int, ...]:
    try:
        return tuple(int(p) for p in str(value).split(".")[:4])
    except ValueError:
        return ()


def _empty_skeleton(doc: Dict[str, Any]) -> Dict[str, Any]:
    """The document with its content emptied and its settings and markers kept."""
    skeleton = copy.deepcopy(doc)
    skeleton["tracks"] = []
    materials = skeleton.get("materials") if isinstance(skeleton.get("materials"), dict) else {}
    for key, value in list(materials.items()):
        if isinstance(value, list):
            materials[key] = []
    for key in MATERIAL_KEYS:
        materials.setdefault(key, [])
    skeleton["materials"] = materials
    for key in ("keyframes", "relationships", "mutable_config"):
        if isinstance(skeleton.get(key), dict):
            skeleton[key] = {k: [] if isinstance(v, list) else v for k, v in skeleton[key].items()}
    skeleton["duration"] = 0
    if isinstance(skeleton.get("static_cover_image_path"), str):
        skeleton["static_cover_image_path"] = ""
    now = int(time.time())
    for key in ("create_time", "update_time"):
        if isinstance(skeleton.get(key), int):
            skeleton[key] = now
    return skeleton


def _bare_skeleton() -> Dict[str, Any]:
    # Deliberately carries no `platform` or version markers: inventing ones
    # that claim to be a real app build is worse than having none.
    return {
        "id": "",
        "name": "",
        "duration": 0,
        "fps": 30.0,
        "canvas_config": {"width": 1920, "height": 1080, "ratio": "16:9"},
        "tracks": [],
        "materials": {k: [] for k in MATERIAL_KEYS},
        "keyframes": {k: [] for k in ("videos", "audios", "texts", "stickers", "effects", "filters", "adjusts")},
        "extra_info": {"created_via": "otio-capcut-adapter"},
    }


def _timeline_rate(tl: otio.schema.Timeline) -> float:
    try:
        rate = tl.duration().rate
    except Exception:  # an empty timeline has no meaningful rate
        rate = 0
    return rate if rate and rate > 0 else 30.0


RATIOS = {(16, 9): "16:9", (9, 16): "9:16", (1, 1): "1:1", (4, 3): "4:3", (3, 4): "3:4", (4, 5): "4:5", (21, 9): "21:9"}


def _canvas(skeleton: Dict[str, Any], width: Optional[int], height: Optional[int]) -> Dict[str, Any]:
    current = skeleton.get("canvas_config") if isinstance(skeleton.get("canvas_config"), dict) else {}
    w = int(width or current.get("width") or 1920)
    h = int(height or current.get("height") or 1080)
    if w <= 0 or h <= 0:
        raise CapCutAdapterError("width and height must be positive.")
    from math import gcd

    g = gcd(w, h)
    canvas = dict(current)
    canvas.update({"width": w, "height": h, "ratio": RATIOS.get((w // g, h // g), "original")})
    return canvas


# ─── media ──────────────────────────────────────────────────────────────────


class _MediaStager:
    """Decides where each media file lives in the draft; copies on ``commit``."""

    def __init__(self, draft_dir: Path, copy_media: bool) -> None:
        self.draft_dir = draft_dir
        self.copy_media = copy_media
        self._planned: Dict[Tuple[str, str], Path] = {}
        self._taken: Dict[str, str] = {}

    def place(self, source: Optional[str], kind: str) -> str:
        """The path the draft should name for ``source``, a file known to exist; "" for none."""
        if not source:
            return ""
        src = Path(source).resolve()
        if not self.copy_media:
            return str(src)
        key = (kind, str(src.resolve()))
        if key in self._planned:
            return str(self._planned[key])
        folder = self.draft_dir / "assets" / ("audio" if kind == "audio" else "video")
        digest = _file_digest(src)
        stem, suffix = src.stem, src.suffix
        candidate = folder / src.name
        n = 2
        # Same name, different file: number it. Same file twice: one copy.
        while str(candidate) in self._taken and self._taken[str(candidate)] != digest:
            candidate = folder / f"{stem}-{n}{suffix}"
            n += 1
        self._taken[str(candidate)] = digest
        self._planned[key] = candidate
        return str(candidate)

    def total_bytes(self) -> int:
        """Bytes of media this draft would copy: each file once."""
        return sum(Path(src).stat().st_size for src in {src for (_, src) in self._planned})

    def commit(self) -> None:
        for (_, src), dest in self._planned.items():
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() and _file_digest(dest) == _file_digest(Path(src)):
                continue
            shutil.copy2(src, dest)


def _file_digest(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_media(clip: otio.schema.Clip, policy: str, where: str) -> Optional[str]:
    """The clip's media as an absolute path to a file, or None for a placeholder.

    Follows ``otiod``: under ``ErrorIfNotFile`` media that is not a local
    file raises ``NotAFileOnDisk``; under ``MissingIfNotFile`` it becomes a
    placeholder. Plain paths, relative ones included (to the working
    directory), are paths, Windows drive paths too; ``file:`` URLs go through
    OpenTimelineIO's own ``url_utils``.
    """
    if policy == MediaReferencePolicy.AllMissing:
        return None
    ref = clip.media_reference
    target = getattr(ref, "target_url", None) if isinstance(ref, otio.schema.ExternalReference) else None
    if not target:
        # otiod passes these through untouched; CapCut needs something, so a placeholder.
        _warn(f"{where}: no media reference, so CapCut will show a missing-media placeholder.")
        return None

    scheme = urlparse(target).scheme
    if scheme == "file":
        path = url_utils.filepath_from_url(target)
    elif scheme == "" or (len(scheme) == 1 and scheme.isalpha()):  # "C:\\..." parses as scheme "c"
        path = target
    else:
        if policy == MediaReferencePolicy.ErrorIfNotFile:
            raise NotAFileOnDisk(
                f"{where}: the CapCut adapter only works with local media; got '{target}'. "
                "Pass media_policy='MissingIfNotFile' to write it as a placeholder instead."
            )
        _warn(f"{where}: '{target}' is not a local file, so CapCut will ask to relink it.")
        return None

    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        if policy == MediaReferencePolicy.ErrorIfNotFile:
            raise NotAFileOnDisk(path)
        _warn(f"{where}: media not found at {path}, so CapCut will ask to relink it.")
        return None
    return path


# ─── building the draft ─────────────────────────────────────────────────────


def _us(t: otio.opentime.RationalTime) -> int:
    return int(round(t.to_seconds() * 1_000_000))


def _new_id() -> str:
    return str(uuid.uuid4()).upper()


def _companions(kind: str) -> List[Tuple[str, Dict[str, Any]]]:
    """The small materials CapCut expects every segment to reference."""
    out: List[Tuple[str, Dict[str, Any]]] = [
        ("speeds", {"id": _new_id(), "type": "speed", "speed": 1.0, "mode": 0, "curve_speed": None}),
        ("placeholder_infos", {"id": _new_id(), "type": "placeholder_info",
                               "error_path": "", "error_text": "",
                               "meta_type": "none", "res_path": "", "res_text": ""}),
        ("sound_channel_mappings", {"id": _new_id(), "type": "none", "audio_channel_mapping": 0,
                                    "is_config_open": False}),
        ("vocal_separations", {"id": _new_id(), "type": "vocal_separation", "choice": 0, "enter_from": "",
                               "final_algorithm": "", "production_path": "", "removed_sounds": [], "time_range": None}),
    ]
    if kind == "video":
        out.append(("canvases", {"id": _new_id(), "type": "canvas_color", "album_image": "", "blur": 0.0, "color": "",
                                 "image": "", "image_id": "", "image_name": "", "source_platform": 0, "team_id": ""}))
        out.append(("material_colors", {"id": _new_id(), "type": "material_color", "gradient_angle": 90.0,
                                        "gradient_colors": [], "gradient_percents": [], "height": 0.0,
                                        "is_color_clip": False, "is_gradient": False, "solid_color": "", "width": 0.0}))
    return out


def _video_material(mat_id: str, path: str, name: str, duration: int, canvas: Dict[str, Any]) -> Dict[str, Any]:
    photo = path.rsplit(".", 1)[-1].lower() in PHOTO_EXTENSIONS
    return {
        **copy.deepcopy(VIDEO_MATERIAL_DEFAULTS),
        "id": mat_id, "path": path, "material_name": name, "type": "photo" if photo else "video",
        "duration": duration, "width": canvas["width"], "height": canvas["height"],
        "category_id": "", "category_name": "local", "check_flag": 7,
        "crop": {"lower_left_x": 0.0, "lower_left_y": 1.0, "lower_right_x": 1.0, "lower_right_y": 1.0,
                 "upper_left_x": 0.0, "upper_left_y": 0.0, "upper_right_x": 1.0, "upper_right_y": 0.0},
        "has_audio": not photo, "extra_type_option": 0, "formula_id": "", "freeze": None,
        "intensifies_audio_path": "", "intensifies_path": "", "is_ai_generate_content": False,
        "is_copyright": False, "is_text_edit_overdub": False, "is_unified_beauty_mode": False,
        "local_id": "", "local_material_id": "", "material_url": "", "media_path": "",
        "object_locked": None, "origin_material_id": "", "request_id": "", "reverse_path": "",
        "source_platform": 0,
        "stable": {"matrix_path": "", "stable_level": 0, "time_range": {"duration": 0, "start": 0}},
        "team_id": "",
        "video_algorithm": {"algorithms": [], "deflicker": None, "motion_blur_config": None, "noise_reduction": None,
                            "path": "", "quality_enhance": None, "time_range": None},
    }


def _audio_material(mat_id: str, path: str, name: str, duration: int) -> Dict[str, Any]:
    return {
        "id": mat_id, "path": path, "name": name, "duration": duration, "type": "extract_music",
        "category_id": "", "category_name": "local", "check_flag": 1, "music_id": "", "request_id": "",
        "source_platform": 0, "team_id": "", "text_id": "", "tone_category_id": "", "tone_category_name": "",
        "tone_effect_id": "", "tone_effect_name": "", "tone_platform": "", "tone_second_category_id": "",
        "tone_second_category_name": "", "tone_speaker": "", "tone_type": "", "wave_points": [],
        "local_material_id": "",
    }


def _segment(mat_id: str, track_id: str, start: int, duration: int, source_start: int, speed: float,
             volume: float, refs: List[str], kind: str) -> Dict[str, Any]:
    return {
        **copy.deepcopy(SEGMENT_DEFAULTS),
        "id": _new_id(),
        "material_id": mat_id,
        "raw_segment_id": track_id,
        "target_timerange": {"start": start, "duration": duration},
        "source_timerange": {"start": source_start, "duration": int(round(duration * speed))},
        "speed": speed,
        "volume": volume,
        "last_nonzero_volume": volume if volume > 0 else 1.0,
        "visible": True,
        "reverse": False,
        "clip": {"alpha": 1.0, "rotation": 0.0, "scale": {"x": 1.0, "y": 1.0}, "transform": {"x": 0.0, "y": 0.0},
                 "flip": {"horizontal": False, "vertical": False}},
        "render_index": RENDER_INDEX[kind],
        "track_render_index": 0,
        "track_attribute": 0,
        "extra_material_refs": refs,
        "common_keyframes": [],
        "keyframe_refs": [],
    }


def _speed_of(clip: otio.schema.Clip, where: str) -> float:
    speed = 1.0
    for effect in clip.effects:
        if isinstance(effect, otio.schema.LinearTimeWarp) and effect.time_scalar > 0:
            speed *= effect.time_scalar
        else:
            _warn(f"{where}: effect {effect.effect_name or effect.schema_name()} is not carried to CapCut.")
    return speed


def _build(
    tl: otio.schema.Timeline, skeleton: Dict[str, Any], canvas: Dict[str, Any], media: _MediaStager, policy: str
) -> Dict[str, Any]:
    draft = skeleton
    materials = draft["materials"]
    end = 0
    seen_main = False

    for t_index, track in enumerate(tl.tracks):
        if not isinstance(track, otio.schema.Track):
            raise otio.exceptions.NotSupportedError(
                f"Item {t_index} of the timeline's stack is a {type(track).__name__}, not a track. "
                "CapCut has no nesting; flatten the timeline first."
            )
        if track.kind == otio.schema.TrackKind.Video:
            kind = "video"
        elif track.kind == otio.schema.TrackKind.Audio:
            kind = "audio"
        else:
            raise otio.exceptions.NotSupportedError(
                f"Track {track.name or t_index} is of kind {track.kind!r}; CapCut takes Video and Audio tracks."
            )
        if track.markers:
            _warn(f"Track {track.name or t_index}: {len(track.markers)} marker(s) are not carried yet.")

        track_id = _new_id()
        segments: List[Dict[str, Any]] = []
        is_main = kind == "video" and not seen_main
        seen_main = seen_main or kind == "video"
        cursor = 0  # microseconds; the end of the previous item

        for item in track:
            where = f"Track {track.name or t_index}, {item.name or type(item).__name__}"
            rng = track.range_of_child(item)
            start, duration = _us(rng.start_time), _us(rng.duration)
            if isinstance(item, otio.schema.Gap):
                if is_main and duration > 0:
                    _warn(
                        f"{where}: a gap on CapCut's main track. CapCut closes main-track gaps when it opens the "
                        "draft, so everything after it will move earlier. Put that shot on a second video track "
                        "to keep the gap."
                    )
                cursor = start + duration
                continue
            if isinstance(item, otio.schema.Transition):
                _warn(f"{where}: transitions are not carried yet; written as a cut.")
                continue
            if not isinstance(item, otio.schema.Clip):
                raise otio.exceptions.NotSupportedError(
                    f"{where}: a {type(item).__name__} inside a track. CapCut has no nesting; "
                    "flatten the timeline first."
                )
            if not item.enabled:
                _warn(f"{where}: disabled clip left out.")
                cursor = start + duration
                continue
            if item.markers:
                _warn(f"{where}: {len(item.markers)} marker(s) are not carried yet.")
            if duration <= 0:
                continue

            speed = _speed_of(item, where)
            source_start = _us(item.trimmed_range().start_time)
            consumed = int(round(duration * speed))
            ref = item.media_reference
            avail = getattr(ref, "available_range", None)
            # A material is as long as its file; without that fact, as long as this edit needs.
            mat_duration = _us(avail.start_time + avail.duration) if avail else source_start + consumed

            raw_path = _resolve_media(item, policy, where)
            path = media.place(raw_path, kind)
            name = Path(raw_path).name if raw_path else (item.name or "missing")

            mat_id = _new_id()
            if kind == "video":
                materials["videos"].append(_video_material(mat_id, path, name, mat_duration, canvas))
            else:
                materials["audios"].append(_audio_material(mat_id, path, name, mat_duration))

            refs = []
            for list_name, companion in _companions(kind):
                if list_name == "speeds":
                    companion["speed"] = speed
                materials.setdefault(list_name, []).append(companion)
                refs.append(companion["id"])

            volume = 1.0
            # OTIO metadata is its own mapping type, not a dict.
            capcut_meta = item.metadata.get("capcut")
            raw_volume = capcut_meta.get("volume") if hasattr(capcut_meta, "get") else None
            if isinstance(raw_volume, (int, float)) and not isinstance(raw_volume, bool) and raw_volume >= 0:
                volume = float(raw_volume)

            segments.append(_segment(mat_id, track_id, start, duration, source_start, speed, volume, refs, kind))
            cursor = start + duration
            end = max(end, cursor)

        draft["tracks"].append({
            "id": track_id, "type": kind, "name": track.name or "", "attribute": 0,
            "segments": segments, "is_default_name": not track.name, "flag": 0,
        })

    if tl.tracks.markers:
        _warn(f"{len(tl.tracks.markers)} timeline marker(s) are not carried yet.")
    draft["duration"] = end
    return draft


def _with_own_id(draft: Dict[str, Any], doc: Dict[str, Any]) -> Dict[str, Any]:
    """The built timeline under an existing document's own id and name."""
    out = copy.deepcopy(draft)
    for key in ("id", "name"):
        if key in doc:
            out[key] = doc[key]
    return out


# ─── the cover ──────────────────────────────────────────────────────────────


def _pick_cover_frame(draft: Dict[str, Any], seed: Optional[int]) -> Optional[Tuple[str, float, bool]]:
    """A random early moment on the main track: (file, seconds into it, is a photo).

    Early, because the opening is what a project is recognised by; random
    within that, because the very first frame is often black or a fade.
    """
    main = next((t for t in draft["tracks"] if t["type"] == "video" and t["segments"]), None)
    if main is None:
        return None
    by_id = {m["id"]: m for m in draft["materials"]["videos"]}
    segments = [s for s in main["segments"] if by_id.get(s["material_id"], {}).get("path")]
    if not segments:
        return None
    end = max(s["target_timerange"]["start"] + s["target_timerange"]["duration"] for s in segments)
    window = min(COVER_WINDOW_S * 1_000_000, end / 4) or 1
    t = random.Random(seed).uniform(0, window)
    # The segment playing at t, or the next one if t falls in a gap.
    seg = next(
        (s for s in segments if s["target_timerange"]["start"] + s["target_timerange"]["duration"] > t),
        segments[-1],
    )
    offset = max(0.0, t - seg["target_timerange"]["start"])
    source_us = seg["source_timerange"]["start"] + offset * seg["speed"]
    mat = by_id[seg["material_id"]]
    return mat["path"], source_us / 1_000_000, mat.get("type") == "photo"


def _cover_size(canvas: Dict[str, Any]) -> Tuple[int, int]:
    w, h = canvas["width"], canvas["height"]
    scale = min(1.0, COVER_MAX_SIDE / max(w, h))
    return max(2, int(w * scale) // 2 * 2), max(2, int(h * scale) // 2 * 2)


def _write_cover(
    draft: Dict[str, Any], draft_dir: Path, canvas: Dict[str, Any], given: Optional[Path],
    seed: Optional[int], ffmpeg: Optional[str], backup: bool,
) -> bool:
    """Write ``draft_cover.jpg``. Returns whether it was written; problems are warnings."""
    dest = draft_dir / COVER_FILE
    exe = ffmpeg or shutil.which("ffmpeg")
    if ffmpeg and not (Path(ffmpeg).is_file() or shutil.which(ffmpeg)):
        exe = None
    w, h = _cover_size(canvas)
    frame = ["-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}", "-frames:v", "1"]

    if given is not None:
        if exe is None and given.suffix.lower() not in (".jpg", ".jpeg"):
            _warn(f"No ffmpeg to turn {given.name} into a JPEG, so the draft has no cover.")
            return False
        if exe is None:
            _replace(dest, lambda tmp: shutil.copyfile(given, tmp), backup)
            return True
        args = ["-i", str(given)]
    else:
        pick = _pick_cover_frame(draft, seed)
        if pick is None:
            _warn("No video on the main track to take a cover from, so the draft has none.")
            return False
        if exe is None:
            _warn("ffmpeg is not installed, so the draft has no cover; CapCut shows a blank tile until one is set.")
            return False
        path, seconds, photo = pick
        args = ["-i", path] if photo else ["-ss", f"{seconds:.3f}", "-i", path]

    def run(tmp: Path) -> None:
        subprocess.run(
            [exe, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args, *frame, "-q:v", "3", str(tmp)],
            check=True, capture_output=True, timeout=60,
        )
        if not tmp.is_file() or tmp.stat().st_size == 0:
            raise RuntimeError("ffmpeg wrote no image")

    try:
        _replace(dest, run, backup)
    except (OSError, subprocess.SubprocessError, RuntimeError) as e:
        detail = getattr(e, "stderr", b"") or b""
        reason = detail.decode("utf-8", "replace").strip().splitlines()[-1:] or [str(e)]
        _warn(f"Could not make a cover ({reason[0]}), so the draft has none.")
        return False
    return True


def _replace(dest: Path, make, backup: bool) -> None:
    """Make a file next to ``dest``, then swap it in, keeping a .bak of what was there."""
    tmp = dest.with_name(f".{dest.stem}.tmp-{os.getpid()}{dest.suffix}")
    try:
        make(tmp)
        if backup and dest.exists():
            shutil.copy2(dest, dest.with_name(dest.name + ".bak"))
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()


# ─── the sidecar ────────────────────────────────────────────────────────────


def _new_sidecar(draft: Dict[str, Any], draft_dir: Path) -> Dict[str, Any]:
    now = int(time.time() * 1_000_000)
    return {
        "draft_cover": "",
        "draft_fold_path": str(draft_dir),
        "draft_id": draft.get("id", ""),
        "draft_is_ai_shorts": False,
        "draft_is_invisible": False,
        "draft_json_file": str(draft_dir / "draft_content.json"),
        "draft_name": draft.get("name", ""),
        "draft_new_version": "",
        "draft_root_path": str(draft_dir.parent),
        "draft_timeline_materials_size": 0,
        "draft_materials": [{"type": t, "value": []} for t in META_GROUP_TYPES],
        "tm_draft_create": now,
        "tm_draft_modified": now,
        "tm_draft_removed": 0,
        "tm_duration": 0,
    }


def _register_media(sidecar: Dict[str, Any], draft: Dict[str, Any], media: _MediaStager) -> None:
    """List each file in the sidecar and link materials to it.

    CapCut 9.1+ reads this list to decide what was imported, and 9.3 resolves
    local video by ``local_material_id``; a blank one leaves the clip
    unresolvable with no in-app repair.
    """
    groups = sidecar.get("draft_materials")
    if not isinstance(groups, list):
        groups = []
        sidecar["draft_materials"] = groups
    group = next((g for g in groups if isinstance(g, dict) and g.get("type") == IMPORTED_MEDIA_GROUP), None)
    if group is None:
        group = {"type": IMPORTED_MEDIA_GROUP, "value": []}
        groups.insert(0, group)
    if not isinstance(group.get("value"), list):
        group["value"] = []
    by_path = {e.get("file_Path"): e.get("id") for e in group["value"] if isinstance(e, dict) and e.get("id")}

    for list_name, kind in (("videos", None), ("audios", "music")):
        for mat in draft["materials"].get(list_name, []):
            path = mat.get("path")
            if not path:
                continue
            entry_id = by_path.get(path)
            if not entry_id:
                metetype = kind or ("photo" if mat.get("type") == "photo" else "video")
                entry_id = str(uuid.uuid4())
                group["value"].append({
                    "ai_group_type": "", "create_time": -1,
                    "duration": int(mat.get("duration") or 5_000_000), "enter_from": 0,
                    "extra_info": Path(path).name, "file_Path": path,
                    "height": 0 if metetype == "music" else int(mat.get("height") or 0),
                    "id": entry_id, "import_time": -1, "import_time_ms": -1, "item_source": 1,
                    "material_color_tag": "", "md5": "", "metetype": metetype,
                    "roughcut_time_range": {"duration": -1, "start": -1},
                    "sub_time_range": {"duration": -1, "start": -1}, "type": 0,
                    "width": 0 if metetype == "music" else int(mat.get("width") or 0),
                })
                by_path[path] = entry_id
            mat["local_material_id"] = entry_id


def _write_json(path: Path, doc: Dict[str, Any]) -> None:
    """Write next to the target, then rename: a crash never leaves half a draft file."""
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)
