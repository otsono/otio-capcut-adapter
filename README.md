# otio-capcut-adapter

An [OpenTimelineIO](https://opentimeline.io) adapter for [CapCut](https://www.capcut.com) drafts:
write any OTIO timeline as a CapCut project, and read a CapCut project back as an OTIO timeline.

```python
import opentimelineio as otio
from otio_capcut_adapter import install

timeline = otio.adapters.read_from_file("cut.otio")
install(timeline, "My project")          # straight into CapCut's project list

back = otio.adapters.read_from_file(
    "~/Movies/CapCut/User Data/Projects/com.lveditor.draft/My project", adapter_name="capcut"
)
```

> **Status: not yet tested in the CapCut app.** The output is checked by automated tests, and
> against drafts the CapCut app wrote, but no draft from this adapter has been opened in CapCut yet.
>
> **The CapCut format is reverse-engineered.** CapCut publishes no project format and changes it
> between versions without notice, so a CapCut update can break this adapter. See
> [Tested CapCut versions](#tested-capcut-versions).

## Install

```bash
pip install otio-capcut-adapter
```

OpenTimelineIO finds the adapter on its own once installed. It is named `capcut`, and reads and
writes.

For the generated cover, install [ffmpeg](https://ffmpeg.org) too. Everything else works without
it.

## Writing a draft

A CapCut draft is a **folder**, so the path you write to is a folder, not a file. Pass
`adapter_name="capcut"`, or use `install()`.

### Recommended: fill a project CapCut made

Current CapCut builds check that a draft carries the identity and version markers the app itself
writes, and refuse drafts that look tool-made. The reliable way past that is to let CapCut make the
project:

1. In CapCut, create a new, empty project and give it a name. Quit CapCut.
2. `install(timeline, "<that name>")`, then open CapCut.

`install()` writes into CapCut's drafts folder:

- Mac: `~/Movies/CapCut/User Data/Projects/com.lveditor.draft/<project>`
- Windows: `%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft\<project>`

`drafts_dir()` returns that folder, and says plainly when CapCut has never been opened on the
computer. Writing into a project folder with `otio.adapters.write_to_file(timeline, folder,
adapter_name="capcut")` does the same as `install()`.

The adapter keeps the project's id, name, version markers and canvas, and writes the cut into every
plain-JSON timeline file the project has, including the `Timelines/<id>/` document newer builds read
first. Files it overwrites are kept as `.bak`. A project that already has a timeline is refused
unless you pass `overwrite=True`.

### A new draft

`install()` with a name CapCut has no project for, or writing to a folder that does not exist yet,
makes a new draft. It borrows the version markers and settings, never the content, of a real
project, in this order:

1. `template="<path to a CapCut project folder>"`, if you pass one.
2. The newest CapCut-made project in the same folder, so a draft written into CapCut's drafts
   folder seeds itself.
3. Otherwise a bare skeleton, with a warning: CapCut 8.4 and later may refuse it.

A new draft in CapCut's drafts folder is added to CapCut's project index (`root_meta_info.json`), so
it shows in the project list. The index is only ever extended, with a `.bak` copy kept; if CapCut has
not written one, none is made.

### Options

| Option | Default | What it does |
| --- | --- | --- |
| `media_policy` | `"ErrorIfNotFile"` | As in OpenTimelineIO's own `otiod` adapter. `ErrorIfNotFile` raises `NotAFileOnDisk` for media that is not a local file. `MissingIfNotFile` writes such clips as placeholders CapCut asks you to relink. `AllMissing` writes every clip as a placeholder and copies no media. |
| `captions` | `True` | Markers on the timeline itself become CapCut captions. `False` leaves them out, with a warning. |
| `cover` | `None` | The tile in CapCut's project list. `None`: a random frame from early in the main track (the first 3 seconds, or the first quarter if the cut is shorter), cropped to the canvas. A path: that image. `False`: no cover. |
| `cover_seed` | none | Seed for the random pick, for the same cover every time. |
| `ffmpeg` | the one on `PATH` | ffmpeg, for the cover. Without it there is no generated cover and you get a warning; the draft is still written. |
| `dryrun` | `False` | Check everything and write nothing. Returns the bytes of media that would be copied, as `otiod` does. |
| `copy_media` | `True` | Copies media into the draft's `assets/` folder. On a Mac, CapCut cannot always read files under Desktop, Documents or Downloads, and a draft that carries its media can be moved. |
| `width`, `height` | the project's own, else 1920 × 1080 | Canvas size in pixels. OTIO has no standard field for it. |
| `template` | none | A CapCut project folder to seed a new draft from. |
| `overwrite` | `False` | Replace a project's existing timeline. |
| `name` | the timeline's name | Display name of a new draft. |

## Captions

OpenTimelineIO has no standard type for on-screen text, so this adapter uses a convention: **a
marker on the timeline itself (`timeline.tracks.markers`) is a caption**, its `name` the words (its
`comment` if it has no name), its `marked_range` when it shows. A marker with no length stays up for
2 seconds. Captions that overlap go on separate text tracks, as CapCut stacks them itself.

```python
timeline.tracks.markers.append(otio.schema.Marker(
    name="doors at 10",
    marked_range=otio.opentime.TimeRange(otio.opentime.RationalTime(0, 30), otio.opentime.RationalTime(60, 30)),
))
```

Captions are written in CapCut's default style: white, centred, in CapCut's default font. Markers on
tracks and clips are notes, not captions; they are not written, and a warning says so. Reading a
draft turns its captions back into timeline markers, so a caption survives the round trip.

## Reading a draft

```python
timeline = otio.adapters.read_from_file("<draft folder>", adapter_name="capcut")
```

Pass the draft folder, or any file inside it. The reader picks the document CapCut itself reads (the
nested `Timelines/<id>/` one on newer builds) and gives you:

- Video and audio tracks of clips and gaps, with `ExternalReference`s to the media (or
  `MissingReference` where CapCut has none), speed as `LinearTimeWarp`, and volume other than 1 as
  `metadata["capcut"]["volume"]`.
- Captions as timeline markers.
- `timeline.metadata["capcut"]`: the draft id, canvas size, which document was read and the CapCut
  version that wrote it.

Times are rounded to whole frames at the draft's frame rate. Sticker, effect and filter tracks have
no OpenTimelineIO equivalent and are reported. Drafts CapCut has encrypted cannot be read.

## What comes across

| OpenTimelineIO | CapCut | Note |
| --- | --- | --- |
| Video tracks | Video tracks, same order | The first is CapCut's main track |
| Audio tracks | Audio tracks | |
| Clip | Material + segment | Times in microseconds |
| Gap | Space before the next segment | CapCut closes gaps on the **main** track when it opens a draft; you get a warning |
| `LinearTimeWarp` | Segment speed | The clip keeps its length on the track; `time_scalar` sets how much media plays in it |
| `metadata["capcut"]["volume"]` | Segment volume | OTIO has no standard volume field |
| Timeline marker | Caption (text segment) | See [Captions](#captions) |
| `ExternalReference` | Media path | `file://` URLs and plain paths |
| `available_range` | Material length | Without it, a material is as long as the cut uses |
| Nested stack, other track kinds | Not supported | Raises `NotSupportedError`; flatten the timeline first |

Nothing is dropped silently. As in OpenTimelineIO's other adapters, what CapCut cannot hold at all
raises `otio.exceptions.NotSupportedError`. Smaller losses are a `CapCutWarning` and the draft is
still written: transitions (written as cuts), effects other than `LinearTimeWarp`, and markers on
tracks and clips.

To see the warnings, or turn them into errors with `warnings.simplefilter("error", CapCutWarning)`:

```python
import warnings
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    otio.adapters.write_to_file(timeline, path, adapter_name="capcut")
for w in caught:
    print(w.message)
```

## Tested CapCut versions

| CapCut | OS | Result | Date |
| --- | --- | --- | --- |
| none yet | | | |

If you open a draft from this adapter in CapCut, please
[open an issue](https://github.com/otsono/otio-capcut-adapter/issues) with your CapCut version, OS,
which way you wrote it (filled project, template, or bare) and whether it opened with the right
timing.

## Development

```bash
pip install -e '.[dev]'
pytest
flake8
```

## Releasing

Releases go to PyPI from GitHub, with no token stored anywhere:

1. On PyPI, add a trusted publisher for this repository: workflow `release.yaml`, environment
   `pypi`.
2. Bump `__version__` in `src/otio_capcut_adapter/__init__.py` and add the release to
   `CHANGELOG.md`.
3. Publish a GitHub release tagged `v<version>`. The workflow checks the tag matches the version,
   runs the tests, builds and publishes.

## License

MIT. See [LICENSE](LICENSE).
