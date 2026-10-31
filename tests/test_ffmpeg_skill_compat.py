"""ffmpeg-skill compatibility is decided by capability detection, not by a version window (ADR-15).

- Fake checkouts whose `_contract.py` prints a hand-built contract pin the detection rules: any version string is
  accepted when the required tools / flags are declared, a missing required flag is refused, the optional flags
  (`audio --overwrite`, `loudness --measure-only`) are reported as detected or not.
- Real-checkout tests (whatever ffmpeg-skill the run is pointed at -- CI runs both the old pinned commit and main)
  prove the detected flags match the tool's own `--help`, that replacing an existing output with `overwrite: true`
  works on that version, and that NORMALIZE is verified through `--measure-only` when a release reports no `result`.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from audio_production.adapter import FLAGS_USED, OPTIONAL_FLAGS_USED, TOOLS_USED, FfmpegSkill
from audio_production.doctor import doctor_report
from conftest import one_json, request_doc, run_cli


def _contract_doc(version="2.10.3", contract_version="1.0", optional=True, drop=None, voice=None):
    tools = []
    for tool in TOOLS_USED:
        props = {f: {"type": "string"} for f in FLAGS_USED[tool]}
        if tool == "audio" and voice is not None:
            props["voice"] = voice
        if optional:
            props.update({f: {"type": "boolean"} for f in OPTIONAL_FLAGS_USED.get(tool, ())})
        if drop and drop[0] == tool:
            props.pop(drop[1], None)
        tools.append({"name": tool, "audio_only": True, "input_schema": {"properties": props}})
    return {"contract_version": contract_version, "skill": {"version": version}, "tools": tools}


def _fake_skill(root: Path, doc) -> Path:
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "_contract.py").write_text(f"print({json.dumps(doc)!r})\n", encoding="utf-8")
    for t in TOOLS_USED:
        (scripts / f"{t}.py").write_text("import sys; sys.exit(2)\n", encoding="utf-8")
    return root


@pytest.mark.parametrize("version", ["0.12.2", "1.9.0", "1.10.0", "2.10.3", "3.0.0", "99.1.0", "not-a-version"])
def test_any_version_with_the_required_flags_is_supported(tmp_path, version):
    info = FfmpegSkill(_fake_skill(tmp_path / "s", _contract_doc(version=version))).info()
    assert info.supported, info.problems
    assert info.version == version
    assert not any("window" in p for p in info.problems)


def test_optional_flags_are_detected_not_assumed(tmp_path):
    old = FfmpegSkill(_fake_skill(tmp_path / "old", _contract_doc("0.12.2", optional=False))).info()
    new = FfmpegSkill(_fake_skill(tmp_path / "new", _contract_doc("2.10.3", optional=True))).info()
    assert old.supported and new.supported
    assert old.optional_flags() == {tool: {f: False for f in flags} for tool, flags in OPTIONAL_FLAGS_USED.items()}
    assert new.optional_flags() == {tool: {f: True for f in flags} for tool, flags in OPTIONAL_FLAGS_USED.items()}
    assert set(OPTIONAL_FLAGS_USED["audio"]) == {"overwrite", "duck", "duck_amount", "duck_threshold", "duck_attack", "duck_release"}
    assert OPTIONAL_FLAGS_USED["cut"] == ("declick_ms",) and OPTIONAL_FLAGS_USED["loudness"] == ("measure_only",)
    assert not old.takes_flag("audio", "overwrite") and new.takes_flag("audio", "overwrite")


def test_missing_required_flag_is_refused_whatever_the_version(tmp_path):
    info = FfmpegSkill(_fake_skill(tmp_path / "s", _contract_doc("2.10.3", drop=("audio", "audio_stream")))).info()
    assert not info.supported
    assert any("audio_stream" in p for p in info.problems)


def test_other_contract_version_is_a_warning_not_a_refusal(tmp_path):
    info = FfmpegSkill(_fake_skill(tmp_path / "s", _contract_doc("3.0.0", contract_version="2.0"))).info()
    assert info.supported, info.problems
    assert any("contract_version 2.0" in w for w in info.warnings)


def test_doctor_reports_what_was_detected(tmp_path, skill_dir):
    rep = doctor_report(str(skill_dir))
    fs = rep["checks"]["ffmpeg_skill"]
    assert fs["status"] == "ok", rep["problems"]
    assert "no version window" in fs["compatibility"]
    assert set(fs["tools_used"]) == set(TOOLS_USED)
    info = FfmpegSkill(skill_dir).info()
    assert fs["optional_flags"] == info.optional_flags()


def _help(skill_dir: Path, tool: str) -> str:
    r = subprocess.run([sys.executable, str(skill_dir / "scripts" / f"{tool}.py"), "--help"], capture_output=True, text=True, cwd=str(skill_dir))
    return r.stdout + r.stderr


def test_detected_optional_flags_match_the_real_tools(skill_dir):
    """The contract-based detection agrees with what the installed tool actually accepts."""
    info = FfmpegSkill(skill_dir).info()
    assert info.takes_flag("audio", "overwrite") == ("--overwrite" in _help(skill_dir, "audio"))
    assert info.takes_flag("loudness", "measure_only") == ("--measure-only" in _help(skill_dir, "loudness"))


def _run(doc, *extra):
    code, out, _ = run_cli(["run", "-", "--json", *extra], json.dumps(doc))
    return code, one_json(out)


def _gain(path="out/main.wav", overwrite=None):
    out = {"output_id": "main", "operation": "op:g", "path": path, "format": "wav"}
    if overwrite is not None:
        out["overwrite"] = overwrite
    return request_doc([{"op_id": "g", "type": "GAIN", "inputs": ["track:t1"], "parameters": {"gain_db": -3}}], outputs=[out])


def test_existing_output_is_replaced_with_overwrite_true(workspace):
    """ffmpeg-skill >= 1.10 refuses to replace an existing file unless --overwrite is passed; older releases reject
    the flag. Either way a request with overwrite: true must replace the file."""
    code, d1 = _run(_gain(overwrite=True))
    assert d1["ok"], d1.get("error")
    target = workspace / "out" / "main.wav"
    before = target.read_bytes()
    target.write_bytes(b"stale")
    code, d2 = _run(_gain(overwrite=True), "--no-reuse")
    assert d2["ok"], d2.get("error")
    assert target.read_bytes() == before
    code, d3 = _run(_gain(overwrite=False))
    assert d3["ok"] is False and d3["error"]["code"] == "OUTPUT_ERROR" and d3["error"]["details"]["reason"] == "exists"


_STRIP_RESULT = '''import json, subprocess, sys
REAL = {real!r}
r = subprocess.run([sys.executable, REAL] + sys.argv[1:], capture_output=True, text=True)
sys.stderr.write(r.stderr)
out = r.stdout
if "--measure-only" not in sys.argv:
    start = out.find("{{")
    if start >= 0:
        doc = json.loads(out[start:])
        doc.pop("result", None)
        out = json.dumps(doc)
sys.stdout.write(out)
sys.exit(r.returncode)
'''

_PASS = '''import subprocess, sys
r = subprocess.run([sys.executable, {real!r}] + sys.argv[1:])
sys.exit(r.returncode)
'''

_CONTRACT = '''import json, subprocess, sys
r = subprocess.run([sys.executable, {real!r}] + sys.argv[1:], capture_output=True, text=True)
out = r.stdout
if "--json" in sys.argv and "doctor" not in sys.argv:
    doc = json.loads(out)
    for t in doc.get("tools", []):
        if t.get("name") == "loudness" and not {keep_measure_only!r}:
            t.get("input_schema", {{}}).get("properties", {{}}).pop("measure_only", None)
    out = json.dumps(doc)
sys.stdout.write(out)
sys.exit(r.returncode)
'''


def _no_result_skill(root: Path, real: Path, keep_measure_only: bool) -> Path:
    """A checkout that behaves like the real one except that loudness.py's NORMALIZE --json has no `result`
    (the pre-0.12.0 shape), and optionally whose contract hides --measure-only."""
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    rs = real / "scripts"
    (scripts / "_contract.py").write_text(_CONTRACT.format(real=str(rs / "_contract.py"), keep_measure_only=keep_measure_only), encoding="utf-8")
    (scripts / "loudness.py").write_text(_STRIP_RESULT.format(real=str(rs / "loudness.py")), encoding="utf-8")
    for t in TOOLS_USED:
        if t != "loudness":
            (scripts / f"{t}.py").write_text(_PASS.format(real=str(rs / f"{t}.py")), encoding="utf-8")
    return root


def _normalize_doc():
    return request_doc([{"op_id": "n", "type": "NORMALIZE", "inputs": ["track:t1"], "parameters": {"target_lufs": -16, "true_peak_db": -1.5, "tolerance_lufs": 1.0}}])


def test_normalize_without_result_is_verified_with_measure_only(workspace, skill_dir, tmp_path):
    fake = _no_result_skill(tmp_path / "pre-0.12", skill_dir, keep_measure_only=True)
    code, d = _run(_normalize_doc(), "--ffmpeg-skill", str(fake))
    assert d["ok"], d.get("error")
    m = next(r for r in d["results"] if r["node_id"] == "op:n")["measurements"]["loudness"]
    assert "--measure-only" in m["measured_by"]
    assert abs(m["integrated_lufs"] - (-16.0)) <= 1.0


def test_normalize_without_result_or_measure_only_is_a_tool_error(workspace, skill_dir, tmp_path):
    fake = _no_result_skill(tmp_path / "neither", skill_dir, keep_measure_only=False)
    code, d = _run(_normalize_doc(), "--ffmpeg-skill", str(fake))
    assert d["ok"] is False and d["error"]["code"] == "TOOL_ERROR" and d["error"]["details"]["reason"] == "loudness_result_missing"
    assert not (workspace / "out" / "main.wav").exists()


# ---- VOICE_CLEANUP: --voice levels are detected from the contract (ADR-16)
_VOICE_BOOL = {"cli": ["--voice"], "type": "boolean"}                                     # ffmpeg-skill < 1.13
_VOICE_ENUM = {"cli": ["--voice"], "type": "string", "enum": ["light", "medium", "strong"]}   # ffmpeg-skill >= 1.13


def test_voice_levels_are_detected_from_the_contract(tmp_path):
    old = FfmpegSkill(_fake_skill(tmp_path / "old", _contract_doc("0.12.2", voice=_VOICE_BOOL))).info()
    new = FfmpegSkill(_fake_skill(tmp_path / "new", _contract_doc("2.21.0", voice=_VOICE_ENUM))).info()
    assert old.supported and new.supported
    assert old.flag_choices("audio", "voice") is None and old.voice_levels() == ["medium"]
    assert new.flag_choices("audio", "voice") == ["light", "medium", "strong"] and new.voice_levels() == ["light", "medium", "strong"]
    gone = FfmpegSkill(_fake_skill(tmp_path / "gone", _contract_doc("2.21.0", drop=("audio", "voice")))).info()
    assert not gone.supported and any("voice" in p for p in gone.problems) and gone.voice_levels() == []


def _voice_argv(skill_root: Path, workspace: Path, level: str):
    from audio_production import executor
    from audio_production.executor import Executor, NodeState
    from audio_production.graph import OperationGraph
    from audio_production.model import parse_request
    from audio_production.security import PathPolicy
    ex = Executor(PathPolicy(str(workspace)), FfmpegSkill(skill_root))
    sources = {"a": {"source_id": "a", "path": str(workspace / "tone.wav"), "sha256": "0" * 64, "size": 1, "duration": 6.0,
                     "channels": 1, "sample_rate": 48000, "codec": "pcm_s16le", "channel_layout": None, "has_video": False}}
    req = parse_request(request_doc([{"op_id": "v", "type": "VOICE_CLEANUP", "inputs": ["track:t1"], "parameters": {"level": level, "audio_stream": 1}}]))
    g = OperationGraph(req.project)
    states = {n: NodeState(g.nodes[n]) for n in g.order}
    for n in g.order:
        ex._plan_node(states, states[n], sources)
    states["track:t1"].artifact = executor.Artifact(Path(sources["a"]["path"]), 6.0, 1, 48000, "pcm_s16le", 1, "0" * 64)
    return states["op:v"], ex._argv(states["op:v"], states, sources, workspace / "o.wav")


def test_voice_argv_follows_the_detected_form(tmp_path):
    from audio_production.errors import AudioError
    ws = tmp_path / "ws"
    ws.mkdir()
    new = _fake_skill(tmp_path / "new", _contract_doc("2.21.0", voice=_VOICE_ENUM))
    old = _fake_skill(tmp_path / "old", _contract_doc("0.12.2", voice=_VOICE_BOOL))
    for level in ("light", "medium", "strong"):
        st, calls = _voice_argv(new, ws, level)
        assert calls == [("audio", [str(ws / "tone.wav"), "--voice", level, "--audio-stream", "1", "-o", str(ws / "o.wav")])]
        assert st.measurements["voice_cleanup"] == {"level": level, "ffmpeg_skill_flag": f"--voice {level}"}
    st, calls = _voice_argv(old, ws, "medium")                    # the boolean switch is the medium chain
    assert calls == [("audio", [str(ws / "tone.wav"), "--voice", "--audio-stream", "1", "-o", str(ws / "o.wav")])]
    for level in ("light", "strong"):                            # never silently run the medium chain instead
        with pytest.raises(AudioError) as ei:
            _voice_argv(old, ws, level)
        assert ei.value.code == "TOOL_ERROR" and ei.value.details["reason"] == "voice_level_unsupported"
        assert ei.value.details["available_levels"] == ["medium"] and ei.value.retryable is False


def test_detected_voice_levels_match_the_real_tool(skill_dir):
    """The contract's --voice declaration agrees with the installed audio.py's own --help."""
    info = FfmpegSkill(skill_dir).info()
    help_text = _help(skill_dir, "audio")
    assert "--voice" in help_text
    if info.flag_choices("audio", "voice") is None:
        assert info.voice_levels() == ["medium"] and "{light,medium,strong}" not in help_text
    else:
        assert "{" + ",".join(info.voice_levels()) + "}" in help_text
    assert doctor_report(str(skill_dir))["checks"]["ffmpeg_skill"]["voice_levels"] == info.voice_levels()


# ---- MIX.duck and FILLER_REMOVE.declick_ms: optional flags, detected per run (ADR-18, ADR-19)
def _contract_without(*flags, version="0.12.2"):
    """A contract like 0.12.2's: the optional flags present except `flags` (tool.flag)."""
    doc = _contract_doc(version)
    for tf in flags:
        tool, flag = tf.split(".")
        for t in doc["tools"]:
            if t["name"] == tool:
                t["input_schema"]["properties"].pop(flag, None)
    return doc


def _plan_and_argv(skill_root: Path, workspace: Path, ops, n_sources=1):
    from audio_production import executor
    from audio_production.executor import Executor, NodeState
    from audio_production.graph import OperationGraph
    from audio_production.model import parse_request
    from audio_production.security import PathPolicy
    ex = Executor(PathPolicy(str(workspace)), FfmpegSkill(skill_root))
    ids = "abcdefgh"[:n_sources]
    sources = {k: {"source_id": k, "path": str(workspace / f"{k}.wav"), "sha256": "0" * 64, "size": 1, "duration": 6.0, "channels": 1, "sample_rate": 48000,
                   "codec": "pcm_s16le", "channel_layout": None, "has_video": False} for k in ids}
    req = parse_request(request_doc(ops, sources=[{"source_id": k, "path": f"{k}.wav"} for k in ids],
                                    tracks=[{"track_id": f"t{i + 1}", "source_id": k} for i, k in enumerate(ids)]))
    g = OperationGraph(req.project)
    states = {n: NodeState(g.nodes[n]) for n in g.order}
    for n in g.order:
        ex._plan_node(states, states[n], sources)
    for i, k in enumerate(ids):
        states[f"track:t{i + 1}"].artifact = executor.Artifact(Path(sources[k]["path"]), 6.0, 1, 48000, "pcm_s16le", 1, "0" * 64)
    last = f"op:{ops[-1]['op_id']}"
    return states[last], ex._argv(states[last], states, sources, workspace / "o.wav")


def _mix(duck, levels=None):
    params = {"duck": duck}
    if levels:
        params["levels"] = levels
    return [{"op_id": "m", "type": "MIX", "inputs": ["track:t1", "track:t2", "track:t3"], "parameters": params}]


def test_duck_argv_follows_the_detected_flags(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    new = _fake_skill(tmp_path / "new", _contract_doc("2.22.4"))
    st, calls = _plan_and_argv(new, ws, _mix({"input": 2, "amount_db": 18, "threshold_db": -30, "attack_ms": 10, "release_ms": 250},
                                             levels=[{"gain_db": -2}, {"gain_db": -14}, {"gain_db": -20}]), 3)
    a, b, c = (str(ws / f"{k}.wav") for k in "abc")
    # the ducked bed (input 2) is folded first, keyed by input 0 alone; input 1 is summed afterwards, un-ducked
    assert calls == [("audio", [a, "--music", c, "--music-volume", "-20.000", "--duck", "--duck-amount", "18.000", "--duck-threshold", "-30.000",
                                "--duck-attack", "10.000", "--duck-release", "250.000", "--gain", "-2.000", "-o", str(ws / "o.mix1.wav")]),
                     ("audio", [str(ws / "o.mix1.wav"), "--music", b, "--music-volume", "-14.000", "-o", str(ws / "o.wav")])]
    assert st.measurements["duck"]["input"] == 2 and st.measurements["duck"]["key_input"] == 0 and "--duck-amount 18.000" in st.measurements["duck"]["ffmpeg_skill_flags"]
    # 0.12.2 declares --duck / --duck-amount only: amount alone runs, a threshold is refused at planning
    old = _fake_skill(tmp_path / "old", _contract_without("audio.duck_threshold", "audio.duck_attack", "audio.duck_release", "cut.declick_ms"))
    st, calls = _plan_and_argv(old, ws, _mix({"input": 1, "amount_db": 12}), 3)
    assert calls[0] == ("audio", [a, "--music", b, "--music-volume", "0.000", "--duck", "--duck-amount", "12.000", "-o", str(ws / "o.mix1.wav")])
    from audio_production.errors import AudioError
    with pytest.raises(AudioError) as ei:
        _plan_and_argv(old, ws, _mix({"input": 1, "amount_db": 12, "release_ms": 200}), 3)
    assert ei.value.code == "TOOL_ERROR" and ei.value.details["reason"] == "ffmpeg_skill_flag_missing" and ei.value.details["missing_flags"] == ["duck_release"]
    assert ei.value.retryable is False
    gone = _fake_skill(tmp_path / "gone", _contract_without("audio.duck", "audio.duck_amount"))
    with pytest.raises(AudioError) as ei:
        _plan_and_argv(gone, ws, _mix({"input": 1, "amount_db": 12}), 3)
    assert ei.value.details["missing_flags"] == ["duck", "duck_amount"]
    # a MIX without duck needs none of the duck flags
    st, calls = _plan_and_argv(gone, ws, [{"op_id": "m", "type": "MIX", "inputs": ["track:t1", "track:t2"], "parameters": {}}], 2)
    assert "--duck" not in calls[0][1]


_WORDS = [{"start": 0.0, "end": 1.0, "text": "本日は"}, {"start": 1.0, "end": 1.5, "text": "えー"}, {"start": 1.5, "end": 3.0, "text": "症例を"},
          {"start": 3.0, "end": 3.4, "text": "um"}, {"start": 3.4, "end": 6.0, "text": "報告します"}]


def _fillers(**extra):
    return [{"op_id": "f", "type": "FILLER_REMOVE", "inputs": ["track:t1"], "parameters": {"words": _WORDS, "lexicon": {"presets": ["ja", "en"]}, **extra}}]


def test_filler_remove_argv_and_declick_detection(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    new = _fake_skill(tmp_path / "new", _contract_doc("2.22.4"))
    old = _fake_skill(tmp_path / "old", _contract_without("cut.declick_ms"))
    a = str(ws / "a.wav")
    st, calls = _plan_and_argv(new, ws, _fillers())
    assert calls == [("cut", [a, "--segments", "0.000-1.000,1.500-3.000,3.400-6.000", "--accurate", "-o", str(ws / "o.wav")])]
    assert st.measurements["effective_ranges"] == [{"start": 1.0, "end": 1.5}, {"start": 3.0, "end": 3.4}]
    assert st.measurements["filler_remove"]["matched_count"] == 2 and st.measurements["filler_remove"]["declick_ms"] is None
    st, calls = _plan_and_argv(new, ws, _fillers(declick_ms=30))
    assert calls[0][1][-4:] == ["--declick-ms", "30.000", "-o", str(ws / "o.wav")] and st.measurements["filler_remove"]["declick_ms"] == 30.0
    st, calls = _plan_and_argv(old, ws, _fillers(declick_ms=0))               # 0 = butt joins: what an old cut does anyway
    assert "--declick-ms" not in calls[0][1]
    from audio_production.errors import AudioError
    with pytest.raises(AudioError) as ei:
        _plan_and_argv(old, ws, _fillers(declick_ms=20))
    assert ei.value.code == "TOOL_ERROR" and ei.value.details["missing_flags"] == ["declick_ms"] and ei.value.details["tool"] == "ffmpeg-skill/cut"
