"""Captions, reading drafts back, and CapCut's project index."""

import json
import warnings
from pathlib import Path

import opentimelineio as otio
import pytest

import otio_capcut_adapter
from otio_capcut_adapter import install
from otio_capcut_adapter.errors import CapCutAdapterError, CapCutWarning
from otio_capcut_adapter.text import content_json, text_from_content, utf16_length

from test_capcut_writer import assert_consistent, load, make_app_project, rt, timeline, tr  # noqa: F401


def write(tl, path, **kwargs):
    kwargs.setdefault("cover", False)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        otio.adapters.write_to_file(tl, str(path), adapter_name="capcut", **kwargs)
    return [str(w.message) for w in caught if issubclass(w.category, CapCutWarning)]


def read(path):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        tl = otio.adapters.read_from_file(str(path), adapter_name="capcut")
    read.warnings = [str(w.message) for w in caught if issubclass(w.category, CapCutWarning)]
    return tl


def captioned(media):
    tl = timeline(media)
    tl.tracks.markers.extend([
        otio.schema.Marker(name="FRIDAY.", marked_range=tr(0, 1.5)),
        otio.schema.Marker(name="see you 🔥 soon", marked_range=tr(1, 2)),   # overlaps the first
        otio.schema.Marker(name="doors 10pm", marked_range=tr(3, 0)),        # no length
    ])
    return tl


# ─── captions ───────────────────────────────────────────────────────────────


def test_utf16_ranges():
    assert utf16_length("abc") == 3
    assert utf16_length("🔥") == 2  # one character, two UTF-16 units
    assert json.loads(content_json("hi 🔥"))["styles"][0]["range"] == [0, 5]
    assert text_from_content(content_json("hi 🔥")) == "hi 🔥"
    assert text_from_content("plain old text") == "plain old text"


def test_timeline_markers_become_captions(tmp_path, media):
    out = tmp_path / "c"
    caught = write(captioned(media), out)
    assert not any("marker" in w for w in caught)
    draft = load(out / "draft_content.json")
    assert_consistent(draft)

    text_tracks = [t for t in draft["tracks"] if t["type"] == "text"]
    assert len(text_tracks) == 2  # the overlap needs a second track
    texts = {m["id"]: m for m in draft["materials"]["texts"]}
    placed = sorted(
        (s["target_timerange"]["start"], s["target_timerange"]["duration"],
         text_from_content(texts[s["material_id"]]["content"]))
        for t in text_tracks for s in t["segments"]
    )
    assert placed == [(0, 1_500_000, "FRIDAY."), (1_000_000, 2_000_000, "see you 🔥 soon"),
                      (3_000_000, 2_000_000, "doors 10pm")]
    # Within a track nothing overlaps.
    for t in text_tracks:
        spans = sorted((s["target_timerange"]["start"], s["target_timerange"]["duration"]) for s in t["segments"])
        assert all(a + d <= b for (a, d), (b, _) in zip(spans, spans[1:]))
    # The caption at 3s + 2s runs past the last shot, so the draft is that long.
    assert draft["duration"] == 5_000_000
    seg = text_tracks[0]["segments"][0]
    assert seg["source_timerange"] is None and seg["render_index"] == 14000
    anim = {m["id"] for m in draft["materials"]["material_animations"]}
    assert set(seg["extra_material_refs"]) <= anim


def test_captions_off_and_other_markers(tmp_path, media):
    caught = write(captioned(media), tmp_path / "off", captions=False)
    assert any("captions=False" in w for w in caught)
    assert not [t for t in load(tmp_path / "off" / "draft_content.json")["tracks"] if t["type"] == "text"]

    tl = timeline(media)
    tl.tracks[0].markers.append(otio.schema.Marker(name="note", marked_range=tr(0, 1)))
    tl.tracks.markers.append(otio.schema.Marker(name="", marked_range=tr(0, 1)))
    caught = write(tl, tmp_path / "notes")
    assert any("not carried" in w for w in caught)  # track markers are notes
    assert any("no name" in w for w in caught)


# ─── reading back ───────────────────────────────────────────────────────────


def test_round_trip_keeps_every_shot(tmp_path, media):
    original = captioned(media)
    write(original, tmp_path / "rt")
    back = read(tmp_path / "rt")
    assert back.name == "Friday recap"

    video, audio = back.tracks
    assert video.kind == "Video" and audio.kind == "Audio"
    clips = list(video.find_clips())
    assert [c.name for c in clips] == ["crowd", "dj", "crowd"]
    # Whole frames on the way back: the sample's 1.25s shot is 37.5 frames at 30fps, so it rounds.
    half_frame = 0.5 / 30 + 1e-9
    for orig, got in zip(original.tracks[0].find_clips(), clips):
        for a, b in ((got.source_range, orig.source_range),
                     (video.range_of_child(got), original.tracks[0].range_of_child(orig))):
            assert abs(a.start_time.to_seconds() - b.start_time.to_seconds()) <= half_frame
            assert abs(a.duration.to_seconds() - b.duration.to_seconds()) <= half_frame
    assert [e.time_scalar for e in clips[1].effects] == [0.5]
    assert clips[0].media_reference.available_range.duration.to_seconds() == 60
    assert url_path(clips[0]).endswith("assets/video/crowd.mp4")
    assert dict(list(audio.find_clips())[0].metadata["capcut"])["volume"] == 0.5

    captions = [(m.name, m.marked_range.start_time.to_seconds(), m.marked_range.duration.to_seconds())
                for m in back.tracks.markers]
    assert captions == [("FRIDAY.", 0, 1.5), ("see you 🔥 soon", 1, 2), ("doors 10pm", 3, 2)]

    # And a second trip changes nothing.
    write(back, tmp_path / "rt2", copy_media=False)
    again = read(tmp_path / "rt2")
    assert [c.source_range for c in again.find_clips()] == [c.source_range for c in back.find_clips()]
    assert [m.name for m in again.tracks.markers] == [m.name for m in back.tracks.markers]


def url_path(clip):
    from opentimelineio import url_utils
    return Path(url_utils.filepath_from_url(clip.media_reference.target_url)).as_posix()


def test_gaps_come_back(tmp_path, media):
    tl = otio.schema.Timeline(name="gaps")
    track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    track.append(otio.schema.Gap(source_range=tr(0, 1)))
    track.append(otio.schema.Clip(name="a", source_range=tr(5, 2),
                                  media_reference=otio.schema.ExternalReference(target_url=str(media / "dj.mp4"))))
    tl.tracks.append(track)
    write(tl, tmp_path / "g")
    back = read(tmp_path / "g").tracks[0]
    assert isinstance(back[0], otio.schema.Gap) and back[0].duration().to_seconds() == 1
    assert back.range_of_child(back[1]).start_time.to_seconds() == 1


def test_reader_prefers_the_nested_document(tmp_path, media):
    project = make_app_project(tmp_path / "store" / "App project")
    write(timeline(media), project)
    nested = project / "Timelines" / "TL-1" / "draft_info.json"
    doc = json.loads(nested.read_text())
    doc["name"] = "the nested one"
    nested.write_text(json.dumps(doc))
    back = read(project)
    assert back.name == "the nested one"
    assert back.metadata["capcut"]["read_from"] == "Timelines/TL-1/draft_info.json"
    assert back.metadata["capcut"]["app_version"] == "9.3.0"
    # A file inside the folder works too.
    assert read(project / "draft_info.json").name == "the nested one"


def test_reader_reports_what_it_cannot_read(tmp_path, media):
    out = tmp_path / "s"
    write(timeline(media), out)
    doc = load(out / "draft_content.json")
    doc["tracks"].append({"id": "x", "type": "sticker", "segments": [{"id": "s", "material_id": "m",
                          "target_timerange": {"start": 0, "duration": 1000000}}]})
    for name in ("draft_content.json", "draft_info.json"):
        (out / name).write_text(json.dumps(doc))
    read(out)
    assert any("sticker" in w for w in read.warnings)

    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "draft_info.json").write_bytes(b"\x00encrypted")
    with pytest.raises(CapCutAdapterError, match="encrypt"):
        read(locked)


def test_reader_falls_back_to_the_project_name(tmp_path, media):
    out = tmp_path / "folder-name"
    write(timeline(media), out)
    for name in ("draft_content.json", "draft_info.json"):
        doc = load(out / name)
        doc["name"] = ""  # CapCut often leaves it blank
        (out / name).write_text(json.dumps(doc))
    meta = load(out / "draft_meta_info.json")
    meta["draft_name"] = "What CapCut shows"
    (out / "draft_meta_info.json").write_text(json.dumps(meta))
    assert read(out).name == "What CapCut shows"


def test_missing_media_reads_as_missing_reference(tmp_path, media):
    write(timeline(media), tmp_path / "m", media_policy="AllMissing")
    clips = list(read(tmp_path / "m").find_clips())
    assert all(isinstance(c.media_reference, otio.schema.MissingReference) for c in clips)


# ─── CapCut's project index ─────────────────────────────────────────────────


def make_index(store, entries=()):
    store.mkdir(parents=True, exist_ok=True)
    index = {"all_draft_store": list(entries), "draft_ids": len(entries), "root_path": str(store)}
    (store / "root_meta_info.json").write_text(json.dumps(index))


def test_new_draft_is_added_to_the_index(tmp_path, media):
    store = tmp_path / "store"
    make_index(store, [{"draft_fold_path": "/elsewhere/Old", "draft_id": "OLD", "draft_name": "Old"}])
    write(timeline(media), store / "Friday")
    index = json.loads((store / "root_meta_info.json").read_text())
    assert index["draft_ids"] == 2
    old, new = index["all_draft_store"]
    assert old["draft_id"] == "OLD"  # others untouched
    draft = load(store / "Friday" / "draft_content.json")
    assert new["draft_id"] == draft["id"] and new["draft_name"] == "Friday recap"
    assert new["draft_fold_path"] == (store / "Friday").resolve().as_posix()
    assert new["tm_duration"] == draft["duration"]
    assert (store / "root_meta_info.json.bak").exists()


def test_filled_project_updates_its_entry(tmp_path, media):
    store = tmp_path / "store"
    project = make_app_project(store / "App project", nested=False)
    make_index(store, [{"draft_fold_path": project.resolve().as_posix(), "draft_id": "APP-DRAFT-ID",
                        "draft_name": "App project", "tm_duration": 0}])
    write(timeline(media), project)
    index = json.loads((store / "root_meta_info.json").read_text())
    assert len(index["all_draft_store"]) == 1 and index["draft_ids"] == 1
    assert index["all_draft_store"][0]["tm_duration"] == 4_750_000


def test_no_index_is_never_invented(tmp_path, media):
    write(timeline(media), tmp_path / "store" / "Friday")
    assert not (tmp_path / "store" / "root_meta_info.json").exists()


def test_sidecar_fold_path_uses_forward_slashes(tmp_path, media):
    write(timeline(media), tmp_path / "f")
    assert "\\" not in load(tmp_path / "f" / "draft_meta_info.json")["draft_fold_path"]


def test_install(tmp_path, media, monkeypatch):
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setattr(otio_capcut_adapter.store, "drafts_dir", lambda: store)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        folder = install(timeline(media), "Friday", cover=False)
    assert folder == store / "Friday" and (folder / "draft_content.json").exists()
    with pytest.raises(CapCutAdapterError, match="folder name"):
        install(timeline(media), "../escape")
