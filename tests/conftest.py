import json
import os
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

from fixtures.generate import available, build_all  # noqa: E402
from audio_production.adapter import FfmpegSkill  # noqa: E402
from audio_production.errors import AudioError  # noqa: E402


def ffmpeg_skill_dir() -> Path:
    """The ffmpeg-skill checkout the integration tests run against. Nothing is skipped: a missing checkout fails."""
    try:
        return FfmpegSkill.locate(os.environ.get("AUDIO_PRODUCTION_FFMPEG_SKILL_DIR")).directory
    except AudioError as e:
        pytest.fail(f"ffmpeg-skill checkout is required for the integration tests (set AUDIO_PRODUCTION_FFMPEG_SKILL_DIR or clone ../ffmpeg-skill): {e.message} tried={e.details.get('tried')}")


@pytest.fixture(scope="session")
def skill_dir() -> Path:
    return ffmpeg_skill_dir()


@pytest.fixture(scope="session")
def media(tmp_path_factory):
    if not available():
        pytest.fail("ffmpeg / ffprobe are required for the integration tests (install FFmpeg); they are not skipped")
    return build_all(tmp_path_factory.mktemp("fixtures"))


@pytest.fixture
def workspace(tmp_path, media):
    """A fresh workspace with copies of the fixtures; the process cwd is moved there for the test."""
    ws = tmp_path / "ws"
    ws.mkdir()
    for p in media.values():
        shutil.copy(p, ws / p.name)
    old = os.getcwd()
    os.chdir(ws)
    try:
        yield ws
    finally:
        os.chdir(old)


def request_doc(ops, outputs=None, sources=None, tracks=None, project_id="p1", options=None):
    sources = sources or [{"source_id": "a", "path": "tone.wav"}]
    tracks = tracks or [{"track_id": "t1", "source_id": "a"}]
    last = ops[-1]["op_id"] if ops else None
    outputs = outputs or [{"output_id": "main", "operation": f"op:{last}" if last else "track:t1", "path": "out/main.wav", "format": "wav"}]
    doc = {"schema": "audio-production/request@1", "project": {"project_id": project_id, "sources": sources, "tracks": tracks, "operations": ops, "outputs": outputs}}
    if options:
        doc["options"] = options
    return doc


def run_cli(args, stdin_text=None, cwd=None):
    """Run the CLI in a subprocess (the real process boundary) and return (exit, stdout, stderr)."""
    import subprocess
    env = dict(os.environ)
    env["PYTHONPATH"] = str(HERE.parent / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("AUDIO_PRODUCTION_FFMPEG_SKILL_DIR", str(ffmpeg_skill_dir()))
    proc = subprocess.run([sys.executable, "-m", "audio_production.cli", *args], input=stdin_text, capture_output=True, text=True, env=env, cwd=cwd)
    return proc.returncode, proc.stdout, proc.stderr


def one_json(text: str):
    """stdout must be exactly one JSON document."""
    doc = json.loads(text)
    assert isinstance(doc, dict)
    return doc
