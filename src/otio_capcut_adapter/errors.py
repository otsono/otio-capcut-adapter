"""Errors and warnings the adapter raises.

Kept out of the adapter file on purpose: OpenTimelineIO loads an adapter's
file as a module of its own, so classes defined there would be different
objects from the ones a user imports, and ``except CapCutAdapterError``
would never match.
"""

import opentimelineio as otio


class CapCutAdapterError(otio.exceptions.OTIOError):
    """The draft could not be written; nothing was changed."""


class CapCutWarning(UserWarning):
    """Something in the timeline did not come across to CapCut."""
