# otio-capcut-adapter

An [OpenTimelineIO](https://opentimeline.io) adapter that writes a timeline as a
[CapCut](https://www.capcut.com) draft, so anything that speaks OTIO can hand a cut to CapCut.

```python
import opentimelineio as otio

timeline = otio.adapters.read_from_file("cut.otio")
otio.adapters.write_to_file(
    timeline,
    "~/Movies/CapCut/User Data/Projects/com.lveditor.draft/My project",
    adapter_name="capcut",
)
```

> **Status: v0.1, not yet tested in the CapCut app.** The output is checked by automated tests
> only. CapCut publishes no project format and changes it between versions; see
> [Tested CapCut versions](#tested-capcut-versions).

## Install

```bash
pip install otio-capcut-adapter
```

OpenTimelineIO finds the adapter on its own once installed. It is named `capcut`.

## Writing a draft

A CapCut draft is a **folder**, so the path you write to is a folder, not a file. Pass
`adapter_name="capcut"`.

### Recommended: fill a project CapCut made

Current CapCut builds check that a draft carries the identity and version markers the app itself
writes, and refuse drafts that look tool-made. The reliable way past that is to let CapCut make the
project:

1. In CapCut, create a new, empty project. Quit CapCut.
2. Find its folder in CapCut's drafts folder:
   - Mac: `~/Movies/CapCut/User Data/Projects/com.lveditor.draft/<project>`
   - Windows: `%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft\<project>`
3. Write the timeline into that folder (the example above), then open CapCut.

The adapter keeps the project's id, name, version markers and canvas, and writes the cut into every
plain-JSON timeline file the project has, including the `Timelines/<id>/` document newer builds read
first. Files it overwrites are kept as `.bak`. A project that already has a timeline is refused
unless you pass `overwrite=True`.

### A new draft

Write to a folder that does not exist yet. The adapter borrows the version markers and settings,
never the content, of a real project, in this order:

1. `template="<path to a CapCut project folder>"`, if you pass one.
2. The newest CapCut-made project in the same folder, so writing straight into CapCut's drafts
   folder seeds itself.
3. Otherwise a bare skeleton, with a warning: CapCut 8.4 and later may refuse it.

### Options

| Option | Default | What it does |
| --- | --- | --- |
| `copy_media` | `True` | Copies media into the draft's `assets/` folder. On a Mac, CapCut cannot always read files under Desktop, Documents or Downloads, and a draft that carries its media can be moved. |
| `width`, `height` | the project's own, else 1920 × 1080 | Canvas size in pixels. OTIO has no standard field for it. |
| `template` | none | A CapCut project folder to seed a new draft from. |
| `overwrite` | `False` | Replace a project's existing timeline. |
| `name` | the timeline's name | Display name of a new draft. |

## What comes across

| OpenTimelineIO | CapCut | Note |
| --- | --- | --- |
| Video tracks | Video tracks, same order | The first is CapCut's main track |
| Audio tracks | Audio tracks | |
| Clip | Material + segment | Times in microseconds |
| Gap | Space before the next segment | CapCut closes gaps on the **main** track when it opens a draft; you get a warning |
| `LinearTimeWarp` | Segment speed | The clip keeps its length on the track; `time_scalar` sets how much media plays in it |
| `metadata["capcut"]["volume"]` | Segment volume | OTIO has no standard volume field |
| `ExternalReference` | Media path | `file://` URLs and plain paths; a missing file becomes a relink prompt in CapCut |
| `available_range` | Material length | Without it, a material is as long as the cut uses |

Not yet carried, each reported as a `CapCutWarning` rather than dropped silently: markers and
captions, transitions, nested stacks, and effects other than `LinearTimeWarp`. There is no reading
of CapCut drafts yet.

To see the warnings:

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

## Credits

What is known about which drafts CapCut accepts comes largely from the version notes and draft
schema documentation of [capcut-cli](https://github.com/renezander030/capcut-cli) (MIT). This
adapter does not use or depend on capcut-cli.

## License

MIT. See [LICENSE](LICENSE).
