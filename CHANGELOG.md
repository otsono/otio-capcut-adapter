# Changelog

## 0.4.0

The first release.

- **Writing** (`write_to_file`, `install()`): video and audio tracks, clips, gaps, speed
  (`LinearTimeWarp`), volume, and media copied into the draft and registered with it. Fills a
  project CapCut made (recommended), or makes a new draft seeded from a real one.
- **Captions**: markers on the timeline become CapCut text, on extra text tracks where they overlap.
- **Covers**: `draft_cover.jpg` from a random early frame, via ffmpeg.
- **CapCut's project list**: a new draft in CapCut's drafts folder is added to its project index.
  `install()` writes straight there; `drafts_dir()` finds the folder.
- **Reading** (`read_from_file`): a draft back into a timeline, captions as markers, from the
  document CapCut itself reads.
- Follows OpenTimelineIO's adapter conventions: `media_policy` and `dryrun` as in `otiod`,
  `NotSupportedError` for structure CapCut cannot hold, `CapCutWarning` for smaller losses.
- Python 3.10+, OpenTimelineIO 0.17+.
