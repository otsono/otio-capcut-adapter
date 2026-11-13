"""OpenTimelineIO adapter that writes CapCut drafts."""

__version__ = "0.4.0"

from otio_capcut_adapter.errors import CapCutAdapterError, CapCutWarning  # noqa: E402,F401
from otio_capcut_adapter.paths import drafts_dir  # noqa: E402,F401
from otio_capcut_adapter.store import install  # noqa: E402,F401
