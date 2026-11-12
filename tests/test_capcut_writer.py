"""Tests for the CapCut writer.

The "app-made" projects here are SYNTHETIC: folders shaped like what CapCut
writes, built by hand. They test that the adapter keeps a project's identity
and writes every document it should. Whether a given CapCut build opens the
result can only be shown in the app; see "Tested CapCut versions" in the
README.
"""

import json
import warnings
from pathlib import Path

import opentimelineio as otio
import pytest

from otio_capcut_adapter.errors import CapCutAdapterError, CapCutWarning

RATE = 30


def rt(seconds):
    return otio.opentime.RationalTime(seconds * RATE, RATE)


def tr(start, duration):
    return otio.opentime.TimeRange(rt(start), rt(duration))


@pytest.fixture
def media(tmp_path):
    folder = tmp_path / "footage"
    folder.mkdir()
    for name, data in (("crowd.mp4", b"crowd"), ("dj.mp4", b"dj"), ("track.mp3", b"music")):
        (folder / name).write_bytes(data)
    return folder


def clip(name, path, start, duration, available=None):
    ref = otio.schema.ExternalReference(target_url=str(path))
    if available is not None:
        ref.available_range = tr(0, available)
    return otio.schema.Clip(name=name, media_reference=ref, source_range=tr(start, duration))


def timeline(media):
    tl = otio.schema.Timeline(name="Friday recap")
    video = otio.schema.Track(name="Video 1", kind=otio.schema.TrackKind.Video)
    video.append(clip("crowd wide", media / "crowd.mp4", 12, 1.5, available=60))
    slow = clip("DJ slow-mo", media / "dj.mp4", 3, 2)
    slow.effects.append(otio.schema.LinearTimeWarp(time_scalar=0.5))
    video.append(slow)
    video.append(clip("crowd again", media / "crowd.mp4", 40, 1.25, available=60))
    audio = otio.schema.Track(name="Music", kind=otio.schema.TrackKind.Audio)
    music = clip("music", media / "track.mp3", 30, 4.75)
    music.metadata["capcut"] = {"volume": 0.5}
    audio.append(music)
    tl.tracks.extend([video, audio])
    return tl


def write(tl, path, **kwargs):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        otio.adapters.write_to_file(tl, str(path), adapter_name="capcut", **kwargs)
    return [str(w.message) for w in caught if issubclass(w.category, CapCutWarning)]


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def assert_consistent(draft):
    """Every reference in the draft points at something that exists."""
    ids = {m["id"] for items in draft["materials"].values() if isinstance(items, list) for m in items}
    for track in draft["tracks"]:
        for seg in track["segments"]:
            assert seg["material_id"] in ids
            assert seg["raw_segment_id"] == track["id"]
            for ref in seg["extra_material_refs"]:
                assert ref in ids
            speed_ref = next(m for m in draft["materials"]["speeds"] if m["id"] in seg["extra_material_refs"])
            assert speed_ref["speed"] == seg["speed"]
            assert seg["source_timerange"]["duration"] == round(seg["target_timerange"]["duration"] * seg["speed"])


def make_app_project(folder, version="9.3.0", nested=True):
    """A synthetic project shaped like one CapCut made: empty timeline, real-looking markers."""
    folder.mkdir(parents=True)
    doc = {
        "id": "APP-DRAFT-ID",
        "name": "App project",
        "duration": 0,
        "fps": 30.0,
        "canvas_config": {"width": 1080, "height": 1920, "ratio": "9:16"},
        "platform": {"app_source": "cc", "app_version": version, "os": "mac"},
        "version": 360000,
        "new_version": "110.0.0",
        "tracks": [],
        "materials": {"videos": [], "audios": [], "app_only_list": []},
        "keyframes": {"videos": [], "audios": []},
        "some_app_setting": {"keep": True},
    }
    (folder / "draft_info.json").write_text(json.dumps(doc))
    (folder / "draft_meta_info.json").write_text(json.dumps({
        "draft_id": "APP-DRAFT-ID", "draft_name": "App project",
        "draft_materials": [{"type": 0, "value": []}, {"type": 1, "value": []}], "app_field": 1,
    }))
    if nested:
        (folder / "Timelines" / "TL-1").mkdir(parents=True)
        (folder / "Timelines" / "project.json").write_text(json.dumps({"main_timeline_id": "TL-1"}))
        nested_doc = dict(doc, id="TL-1")
        (folder / "Timelines" / "TL-1" / "draft_info.json").write_text(json.dumps(nested_doc))
    return folder


# ─── a new draft ────────────────────────────────────────────────────────────


def test_new_draft_writes_the_cut(tmp_path, media):
    out = tmp_path / "drafts" / "Friday"
    caught = write(timeline(media), out)
    assert any("bare skeleton" in w for w in caught)

    draft = load(out / "draft_content.json")
    assert load(out / "draft_info.json") == draft
    assert_consistent(draft)
    assert draft["name"] == "Friday recap"
    assert draft["fps"] == 30.0

    video, audio = draft["tracks"]
    assert [t["type"] for t in draft["tracks"]] == ["video", "audio"]
    starts = [s["target_timerange"]["start"] for s in video["segments"]]
    assert starts == [0, 1_500_000, 3_500_000]
    first = video["segments"][0]
    assert first["source_timerange"] == {"start": 12_000_000, "duration": 1_500_000}
    assert draft["duration"] == 4_750_000

    # Same file used twice: one copy in the draft.
    paths = [m["path"] for m in draft["materials"]["videos"]]
    assert paths[0] == paths[2] == str(out / "assets" / "video" / "crowd.mp4")
    assert (out / "assets" / "video" / "crowd.mp4").read_bytes() == b"crowd"
    assert draft["materials"]["videos"][0]["duration"] == 60_000_000

    assert audio["segments"][0]["volume"] == 0.5
    assert draft["materials"]["audios"][0]["path"] == str(out / "assets" / "audio" / "track.mp3")


def test_speed_uses_otio_timing(tmp_path, media):
    out = tmp_path / "Friday"
    write(timeline(media), out)
    slow = load(out / "draft_content.json")["tracks"][0]["segments"][1]
    # 2s on the timeline at half speed plays 1s of media.
    assert slow["speed"] == 0.5
    assert slow["target_timerange"]["duration"] == 2_000_000
    assert slow["source_timerange"] == {"start": 3_000_000, "duration": 1_000_000}


def test_sidecar_registers_media(tmp_path, media):
    out = tmp_path / "Friday"
    write(timeline(media), out)
    draft = load(out / "draft_content.json")
    meta = load(out / "draft_meta_info.json")
    entries = next(g for g in meta["draft_materials"] if g["type"] == 0)["value"]
    by_id = {e["id"]: e for e in entries}
    assert len(entries) == 3  # crowd, dj, track; crowd only once
    for mat in draft["materials"]["videos"] + draft["materials"]["audios"]:
        entry = by_id[mat["local_material_id"]]
        assert entry["file_Path"] == mat["path"]
    assert {e["metetype"] for e in entries} == {"video", "music"}
    assert meta["draft_id"] == draft["id"]
    assert meta["tm_duration"] == draft["duration"]


def test_refuses_a_non_project_folder(tmp_path, media):
    out = tmp_path / "busy"
    out.mkdir()
    (out / "notes.txt").write_text("hi")
    with pytest.raises(CapCutAdapterError):
        write(timeline(media), out)


# ─── filling a project CapCut made ──────────────────────────────────────────


def test_fill_keeps_the_projects_identity(tmp_path, media):
    project = make_app_project(tmp_path / "store" / "App project")
    caught = write(timeline(media), project)
    assert not any("skeleton" in w for w in caught)

    draft = load(project / "draft_info.json")
    assert_consistent(draft)
    assert draft["id"] == "APP-DRAFT-ID"
    assert draft["name"] == "App project"
    assert draft["platform"] == {"app_source": "cc", "app_version": "9.3.0", "os": "mac"}
    assert draft["version"] == 360000 and draft["some_app_setting"] == {"keep": True}
    assert draft["canvas_config"]["width"] == 1080  # the project's own canvas
    assert "app_only_list" in draft["materials"]
    assert len(draft["tracks"][0]["segments"]) == 3

    # The nested document newer builds read first gets the same cut, under its own id.
    nested = load(project / "Timelines" / "TL-1" / "draft_info.json")
    assert nested["id"] == "TL-1"
    assert nested["tracks"] == draft["tracks"]

    meta = load(project / "draft_meta_info.json")
    assert meta["draft_id"] == "APP-DRAFT-ID" and meta["app_field"] == 1
    assert (project / "draft_info.json.bak").exists()
    # No new root files invented in a project that did not have them.
    assert not (project / "draft_content.json").exists()


def test_fill_refuses_a_project_with_a_timeline(tmp_path, media):
    project = make_app_project(tmp_path / "store" / "App project")
    write(timeline(media), project)
    with pytest.raises(CapCutAdapterError, match="overwrite=True"):
        write(timeline(media), project)
    write(timeline(media), project, overwrite=True)
    assert len(load(project / "draft_info.json")["tracks"][0]["segments"]) == 3


def test_fill_leaves_encrypted_mirrors_alone(tmp_path, media):
    project = make_app_project(tmp_path / "store" / "App project", nested=False)
    (project / "template-2.tmp").write_bytes(b"\x00\x01encrypted")
    caught = write(timeline(media), project)
    assert (project / "template-2.tmp").read_bytes() == b"\x00\x01encrypted"
    assert any("template-2.tmp" in w for w in caught)


def test_canvas_option_overrides(tmp_path, media):
    project = make_app_project(tmp_path / "store" / "App project", nested=False)
    write(timeline(media), project, width=1920, height=1080)
    assert load(project / "draft_info.json")["canvas_config"] == {"width": 1920, "height": 1080, "ratio": "16:9"}


# ─── seeding a new draft ────────────────────────────────────────────────────


def test_new_draft_seeds_from_the_newest_app_project_beside_it(tmp_path, media):
    store = tmp_path / "store"
    make_app_project(store / "Old", version="8.7.0", nested=False)
    make_app_project(store / "New", version="9.3.0", nested=False)
    caught = write(timeline(media), store / "Friday")
    assert not any("skeleton" in w for w in caught)
    draft = load(store / "Friday" / "draft_content.json")
    assert draft["platform"]["app_version"] == "9.3.0"
    assert draft["id"] != "APP-DRAFT-ID"
    assert draft["name"] == "Friday recap"
    assert_consistent(draft)


def test_template_option(tmp_path, media):
    donor = make_app_project(tmp_path / "elsewhere" / "Donor", version="9.0.0", nested=False)
    write(timeline(media), tmp_path / "out" / "Friday", template=str(donor))
    assert load(tmp_path / "out" / "Friday" / "draft_content.json")["platform"]["app_version"] == "9.0.0"
    # The donor is untouched.
    assert load(donor / "draft_info.json")["tracks"] == []


# ─── what does not come across is said ──────────────────────────────────────


def test_main_track_gap_warns(tmp_path, media):
    tl = otio.schema.Timeline(name="gaps")
    track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    track.append(clip("a", media / "crowd.mp4", 0, 1))
    track.append(otio.schema.Gap(source_range=tr(0, 0.5)))
    track.append(clip("b", media / "dj.mp4", 0, 1))
    tl.tracks.append(track)
    caught = write(tl, tmp_path / "gaps")
    assert any("main track" in w for w in caught)
    seg_b = load(tmp_path / "gaps" / "draft_content.json")["tracks"][0]["segments"][1]
    assert seg_b["target_timerange"]["start"] == 1_500_000


def test_unsupported_things_warn(tmp_path, media):
    tl = timeline(media)
    tl.tracks[0][0].markers.append(otio.schema.Marker(name="FRIDAY.", marked_range=tr(0, 1)))
    tl.tracks[0].insert(1, otio.schema.Transition(in_offset=rt(0.25), out_offset=rt(0.25)))
    tl.tracks[0][0].effects.append(otio.schema.Effect(effect_name="Blur"))
    caught = write(tl, tmp_path / "x")
    assert any("marker" in w for w in caught)
    assert any("transition" in w for w in caught)
    assert any("Blur" in w for w in caught)


def test_missing_media_warns(tmp_path, media):
    tl = otio.schema.Timeline(name="missing")
    track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    track.append(clip("gone", tmp_path / "nope.mp4", 0, 1))
    track.append(otio.schema.Clip(name="no ref", source_range=tr(0, 1)))
    tl.tracks.append(track)
    caught = write(tl, tmp_path / "m")
    assert any("Media not found" in w for w in caught)
    assert any("no local media" in w for w in caught)
    draft = load(tmp_path / "m" / "draft_content.json")
    assert draft["materials"]["videos"][1]["path"] == ""
    assert_consistent(draft)


def test_file_urls(tmp_path, media):
    tl = otio.schema.Timeline(name="urls")
    track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    url = (media / "crowd.mp4").as_uri()
    track.append(otio.schema.Clip(
        name="c", media_reference=otio.schema.ExternalReference(target_url=url), source_range=tr(0, 1)))
    tl.tracks.append(track)
    write(tl, tmp_path / "u", copy_media=False)
    path = load(tmp_path / "u" / "draft_content.json")["materials"]["videos"][0]["path"]
    assert path == str((media / "crowd.mp4").resolve())
