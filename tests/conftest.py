import sys
from pathlib import Path

import pytest

# The test modules share helpers by importing each other.
sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture
def media(tmp_path):
    """Stand-in media files: real files with known sizes, not real video."""
    folder = tmp_path / "footage"
    folder.mkdir()
    for name, data in (("crowd.mp4", b"crowd"), ("dj.mp4", b"dj"), ("track.mp3", b"music")):
        (folder / name).write_bytes(data)
    return folder
