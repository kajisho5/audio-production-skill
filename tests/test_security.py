"""Security tests: the AI boundary (no command / argv / filter passthrough), path escapes through the whole
process boundary, malformed input, and source-level guarantees (no shell)."""
import json
import os
import re
from pathlib import Path

import pytest

from audio_production import adapter, executor
from audio_production.adapter import FfmpegSkill, fmt_db, fmt_seconds
from audio_production.errors import AudioError
from conftest import one_json, request_doc, run_cli

SRC = Path(__file__).resolve().parent.parent / "src" / "audio_production"


def test_no_shell_or_eval_in_source():
    text = "\n".join(p.read_text(encoding="utf-8") for p in SRC.glob("*.py"))
    for pattern in (r"shell\s*=\s*True", r"\bos\.system\(", r"\bos\.popen\(", r"\beval\(", r"\bexec\(", r"subprocess\.getoutput", r"subprocess\.call\("):
        assert not re.search(pattern, text), pattern
    # exactly one place starts a process (adapter._popen) plus the doctor delegating to it
    assert text.count("subprocess.Popen(") == 1


def test_only_allowlisted_ffmpeg_skill_scripts_can_run(tmp_path):
    sk = FfmpegSkill(tmp_path)
    for name in ("render", "batch", "../../bin/sh", "audio; rm -rf /", "verify"):
        with pytest.raises(AudioError) as ei:
            sk.script(name)
        assert ei.value.code == "INTERNAL_ERROR"
    assert sk.script("audio").endswith(os.path.join("scripts", "audio.py"))


def test_argv_formatting_never_passes_strings_through():
    assert fmt_seconds(1) == "1.000" and fmt_seconds(12.34567) == "12.346"
    assert fmt_db(-6) == "-6.000"
    for bad in (float("nan"), float("inf"), -1):
        with pytest.raises(AudioError):
            fmt_seconds(bad)
    with pytest.raises(AudioError):
        fmt_db(500)
    with pytest.raises(AudioError):
        fmt_db("volume=3dB")  # type: ignore[arg-type]


def test_nul_in_argv_is_refused(tmp_path):
    with pytest.raises(AudioError):
        FfmpegSkill(tmp_path)._popen(["python3", "x\x00y"], 1)


@pytest.mark.parametrize("payload", [
    {"gain_db": "3dB,volume=10dB"}, {"gain_db": "$(rm -rf /)"}, {"gain_db": "3; rm -rf /"}, {"gain_db": [3]}, {"gain_db": {"value": 3}},
])
def test_gain_parameter_injection_is_rejected(payload):
    from audio_production.model import parse_request
    with pytest.raises(AudioError) as ei:
        parse_request(request_doc([{"op_id": "g", "type": "GAIN", "inputs": ["track:t1"], "parameters": payload}]))
    assert ei.value.code == "INVALID_REQUEST"


@pytest.mark.parametrize("path", ["../secret.wav", "/etc/passwd", "out/../../x.wav", "CON.wav", "-i.wav", "x\x00.wav", "a|b.wav"])
def test_unsafe_output_paths_through_cli(workspace, path):
    doc = request_doc([], outputs=[{"output_id": "o", "operation": "track:t1", "path": path, "format": "wav"}])
    code, out, err = run_cli(["plan", "-", "--json", "--workspace", str(workspace)], json.dumps(doc))
    d = one_json(out)
    assert d["ok"] is False and d["error"]["code"] in ("PATH_NOT_ALLOWED", "UNSUPPORTED_FORMAT", "INVALID_REQUEST"), d["error"]
    assert code != 0 and not (workspace / "x.wav").exists()


def test_input_outside_allowed_roots_through_cli(workspace, media, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "in.wav").write_bytes((workspace / "tone.wav").read_bytes())
    doc = request_doc([], sources=[{"source_id": "a", "path": str(other / "in.wav")}])
    code, out, _ = run_cli(["plan", "-", "--json", "--workspace", str(workspace), "--allowed-input", str(workspace)], json.dumps(doc))
    d = one_json(out)
    assert d["ok"] is False and d["error"]["code"] == "PATH_NOT_ALLOWED" and d["error"]["details"]["reason"] == "outside_allowed_roots"
    code, out, _ = run_cli(["plan", "-", "--json", "--workspace", str(workspace)], json.dumps(doc))
    assert one_json(out)["ok"] is True   # readable anywhere without roots, like media-analysis-skill


@pytest.mark.skipif(os.name == "nt", reason="symlinks")
def test_symlink_escape_through_cli(workspace, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "escape").symlink_to(outside)
    doc = request_doc([], outputs=[{"output_id": "o", "operation": "track:t1", "path": "escape/out.wav", "format": "wav"}])
    code, out, _ = run_cli(["run", "-", "--json", "--workspace", str(workspace)], json.dumps(doc))
    d = one_json(out)
    assert d["ok"] is False and d["error"]["code"] == "PATH_NOT_ALLOWED"
    assert not (outside / "out.wav").exists()


def test_output_may_not_overwrite_input(workspace):
    doc = request_doc([{"op_id": "g", "type": "GAIN", "inputs": ["track:t1"], "parameters": {"gain_db": -3}}],
                      outputs=[{"output_id": "o", "operation": "op:g", "path": "tone.wav", "format": "wav", "overwrite": True}])
    before = (workspace / "tone.wav").read_bytes()
    code, out, _ = run_cli(["run", "-", "--json"], json.dumps(doc))
    d = one_json(out)
    assert d["ok"] is False and d["error"]["code"] == "OUTPUT_ERROR" and d["error"]["details"]["reason"] == "input_output_collision"
    assert (workspace / "tone.wav").read_bytes() == before


def test_existing_output_is_not_overwritten_by_default(workspace):
    (workspace / "out").mkdir()
    (workspace / "out" / "main.wav").write_bytes(b"precious")
    doc = request_doc([{"op_id": "g", "type": "GAIN", "inputs": ["track:t1"], "parameters": {"gain_db": -3}}])
    code, out, _ = run_cli(["run", "-", "--json"], json.dumps(doc))
    d = one_json(out)
    assert d["ok"] is False and d["error"]["code"] == "OUTPUT_ERROR" and d["error"]["details"]["reason"] == "exists"
    assert (workspace / "out" / "main.wav").read_bytes() == b"precious"


@pytest.mark.parametrize("text", ["", "{", "[1,2]", "null", '{"schema": 1}', "\x00", '{"a": NaN}'])
def test_malformed_documents_yield_one_json_error(workspace, text):
    code, out, err = run_cli(["run", "-", "--json"], text)
    d = one_json(out)
    assert d["ok"] is False and d["error"]["code"] == "INVALID_REQUEST" and code == 2
    assert out.count('"schema"') >= 1 and out.strip().startswith("{") and out.strip().endswith("}")


def test_request_with_command_fields_never_reaches_a_tool(workspace):
    doc = request_doc([{"op_id": "g", "type": "GAIN", "inputs": ["track:t1"], "parameters": {"gain_db": -3}}])
    doc["project"]["operations"][0]["argv"] = ["ffmpeg", "-i", "x"]
    code, out, _ = run_cli(["run", "-", "--json"], json.dumps(doc))
    d = one_json(out)
    assert d["ok"] is False and d["error"]["code"] == "INVALID_REQUEST" and d["error"]["details"]["reason"] == "forbidden_field"
    assert not (workspace / ".audio-production").exists()


def test_ffmpeg_skill_dir_is_not_taken_from_the_request(workspace):
    doc = request_doc([])
    doc["options"] = {"ffmpeg_skill": "/tmp/evil"}
    code, out, _ = run_cli(["plan", "-", "--json"], json.dumps(doc))
    assert one_json(out)["error"]["code"] == "INVALID_REQUEST"


def test_bogus_ffmpeg_skill_dir_is_rejected(workspace, tmp_path):
    fake = tmp_path / "fake-skill"
    (fake / "scripts").mkdir(parents=True)
    for n in ("_contract", "probe", "audio", "cut", "loudness", "join"):
        (fake / "scripts" / f"{n}.py").write_text("import sys; print('{}'); sys.exit(0)\n")
    code, out, _ = run_cli(["plan", "-", "--json", "--ffmpeg-skill", str(fake)], json.dumps(request_doc([])))
    d = one_json(out)
    assert d["ok"] is False and d["error"]["code"] == "TOOL_ERROR" and d["error"]["details"]["reason"] == "ffmpeg_skill_incompatible"
    code, out, _ = run_cli(["plan", "-", "--json", "--ffmpeg-skill", str(tmp_path / "missing")], json.dumps(request_doc([])))
    assert one_json(out)["error"]["details"]["reason"] == "ffmpeg_skill_missing"


def test_child_environment_is_minimal(monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "abc")
    env = adapter._clean_env()
    assert "SECRET_TOKEN" not in env and "PATH" in env and env["PYTHONUTF8"] == "1"


def test_argv_builder_uses_only_numbers_and_resolved_paths(workspace):
    """Every argv element the executor builds is a formatted number, a fixed flag, or an absolute path."""
    from audio_production.executor import Executor, NodeState
    from audio_production.graph import OperationGraph
    from audio_production.model import parse_request
    from audio_production.security import PathPolicy
    ops = [{"op_id": "t", "type": "TRIM", "inputs": ["track:t1"], "parameters": {"start": 0.5, "end": 2.5}},
           {"op_id": "c", "type": "CUT", "inputs": ["op:t"], "parameters": {"remove": [{"start": 0.5, "end": 1.0}]}},
           {"op_id": "n", "type": "NORMALIZE", "inputs": ["op:c"], "parameters": {"target_lufs": -16, "true_peak_db": -1, "loudness_range_lu": 7, "sample_rate": 44100}},
           {"op_id": "d", "type": "NOISE_REDUCTION", "inputs": ["op:n"], "parameters": {"mode": "fft", "strength_db": 20}},
           {"op_id": "y", "type": "DYNAMICS", "inputs": ["op:d"], "parameters": {"gate": {"threshold_db": -40}, "compressor": {"threshold_db": -20, "ratio": 4, "attack_ms": 5, "release_ms": 80, "makeup_db": 2, "knee_db": 3}, "limiter": {"ceiling_db": -1}}},
           {"op_id": "k", "type": "CONCAT", "inputs": ["op:y", "op:d"], "parameters": {"crossfade": 0.5, "sample_rate": 48000, "channels": 2}}]
    req = parse_request(request_doc(ops))
    g = OperationGraph(req.project)
    ex = Executor(PathPolicy(str(workspace)), FfmpegSkill(workspace))
    sources = {"a": {"source_id": "a", "path": str(workspace / "tone.wav"), "sha256": "0" * 64, "size": 1, "duration": 6.0, "channels": 1, "sample_rate": 48000, "codec": "pcm_s16le", "channel_layout": None, "has_video": False}}
    states = {n: NodeState(g.nodes[n]) for n in g.order}
    for n in g.order:
        ex._plan_node(states, states[n], sources)
    states["track:t1"].artifact = executor.Artifact(Path(sources["a"]["path"]), 6.0, 1, 48000, "pcm_s16le", 1, "0" * 64)
    flags = re.compile(r"^(--[a-z-]+|-I|-o|none|fade)$")
    num = re.compile(r"^-?\d+(\.\d{3})?(-\d+\.\d{3})?(,\d+\.\d{3}-\d+\.\d{3})*$")
    for n in g.order[1:]:
        states[n].artifact = executor.Artifact(workspace / f"{n}.wav", 1.0, 1, 48000, "pcm_s16le", 1, "1" * 64)
        for tool, argv in ex._argv(states[n], states, sources, workspace / "o.wav"):
            assert tool in ("audio", "cut", "loudness", "join")
            for a in argv:
                assert flags.match(a) or num.match(a) or os.path.isabs(a), (n, a)
    assert ex._argv(states["op:c"], states, sources, workspace / "o.wav") == [("cut", [str(workspace / "op:t.wav"), "--segments", "0.000-0.500,1.000-2.000", "--accurate", "-o", str(workspace / "o.wav")])]
    assert ex._argv(states["op:t"], states, sources, workspace / "o.wav") == [("cut", [sources["a"]["path"], "--start", "0.500", "--end", "2.500", "--accurate", "-o", str(workspace / "o.wav")])]
    assert ex._argv(states["op:n"], states, sources, workspace / "o.wav")[0][1][1:-2] == ["-I", "-16.000", "--tp", "-1.000", "--lra", "7.000", "--sample-rate", "44100"]
    dyn = ex._argv(states["op:y"], states, sources, workspace / "o.wav")[0][1]
    assert dyn[1:-2] == ["--gate", "--gate-threshold", "-40.000", "--compress", "--comp-attack", "5.000", "--comp-knee", "3.000", "--comp-makeup", "2.000",
                         "--comp-ratio", "4.000", "--comp-release", "80.000", "--comp-threshold", "-20.000", "--limit", "--limit-ceiling", "-1.000"]
    cat = ex._argv(states["op:k"], states, sources, workspace / "o.wav")[0]
    assert cat[0] == "join" and cat[1][2:] == ["--transition", "fade", "--duration", "0.500", "--sample-rate", "48000", "--channels", "2", "-o", str(workspace / "o.wav")]
    # a video-container source is extracted through ffmpeg-skill/audio, never handed to cut / join as a video
    sources["a"]["has_video"] = True
    states["track:t1"].artifact = None
    assert ex._argv(states["track:t1"], states, sources, workspace / "x.wav") == [("audio", [sources["a"]["path"], "-o", str(workspace / "x.wav")])]
