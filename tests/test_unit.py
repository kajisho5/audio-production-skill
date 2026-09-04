"""Unit tests: schema validation, timeline arithmetic, operation graph, deterministic ids, serialisation, path policy.
No ffmpeg, no ffmpeg-skill, no media."""
import os

import pytest

from audio_production import VERSION
from audio_production.canonical import canonical_json, stable_hash
from audio_production.errors import ERROR_CODES, EXIT_CODES, AudioError
from audio_production.graph import OperationGraph
from audio_production.model import OPERATION_TYPES, UNSUPPORTED_OPERATIONS, TimeRange, parse_request, validate_parameters
from audio_production.security import PathPolicy, check_filename
from audio_production.timeline import apply_silence_rules, base_segments, complement, cut, fade_ranges, mix, total_duration, trim
from conftest import request_doc


def err(fn, code, *args, **kw):
    with pytest.raises(AudioError) as ei:
        fn(*args, **kw)
    assert ei.value.code == code, ei.value
    return ei.value


# ---- request schema
def test_minimal_request_parses():
    req = parse_request(request_doc([{"op_id": "g", "type": "GAIN", "inputs": ["track:t1"], "parameters": {"gain_db": -3}}]))
    assert req.project.operations[0].parameters == {"gain_db": -3.0}
    assert req.options == {"reuse_intermediates": True, "timeout": None}


def test_request_rejects_wrong_schema_and_shape():
    err(parse_request, "INVALID_REQUEST", [])
    err(parse_request, "INVALID_REQUEST", {"schema": "audio-production/request@2", "project": {}})
    err(parse_request, "INVALID_REQUEST", {"schema": "audio-production/request@1"})
    d = request_doc([])
    d["extra"] = 1
    err(parse_request, "INVALID_REQUEST", d)


@pytest.mark.parametrize("key", ["command", "argv", "args", "cmd", "shell", "exec", "executable", "script", "filter", "filter_complex", "af", "env"])
def test_request_rejects_command_like_fields_anywhere(key):
    d = request_doc([{"op_id": "g", "type": "GAIN", "inputs": ["track:t1"], "parameters": {"gain_db": -3}}])
    d["project"]["operations"][0]["parameters"][key] = "rm -rf /"
    e = err(parse_request, "INVALID_REQUEST", d)
    assert e.details.get("reason") == "forbidden_field"
    d2 = request_doc([])
    d2["project"]["sources"][0][key] = "x"
    err(parse_request, "INVALID_REQUEST", d2)


def test_unknown_and_unsupported_operation_types():
    e = err(parse_request, "UNSUPPORTED_OPERATION", request_doc([{"op_id": "x", "type": "REVERB", "inputs": ["track:t1"]}]))
    assert "supported" in e.details
    for typ in UNSUPPORTED_OPERATIONS:
        e = err(parse_request, "UNSUPPORTED_OPERATION", request_doc([{"op_id": "x", "type": typ, "inputs": ["track:t1"]}]))
        assert "not implemented" in e.message
        assert typ not in OPERATION_TYPES


def test_parameter_validation_per_type():
    v = validate_parameters
    assert v("GAIN", {"gain_db": 3}, 1, "p") == {"gain_db": 3.0}
    err(v, "INVALID_REQUEST", "GAIN", {}, 1, "p")                       # required
    err(v, "INVALID_REQUEST", "GAIN", {"gain_db": 100}, 1, "p")         # range
    err(v, "INVALID_REQUEST", "GAIN", {"gain_db": "3dB"}, 1, "p")       # type (a string could carry a filter)
    err(v, "INVALID_REQUEST", "GAIN", {"gain_db": True}, 1, "p")        # bool is not a number
    err(v, "INVALID_REQUEST", "GAIN", {"gain_db": 3, "curve": "x"}, 1, "p")   # unknown
    err(v, "INVALID_REQUEST", "GAIN", {"gain_db": float("nan")}, 1, "p")
    assert v("TRIM", {"start": 1, "end": 2.5}, 1, "p") == {"start": 1.0, "end": 2.5}
    err(v, "INVALID_TIME_RANGE", "TRIM", {"start": 2, "end": 2}, 1, "p")
    err(v, "INVALID_REQUEST", "TRIM", {"start": -1, "end": 2}, 1, "p")
    assert v("CUT", {"remove": [{"start": 0, "end": 1}, {"start": 1, "end": 2}]}, 1, "p")["remove"][1] == {"start": 1.0, "end": 2.0}
    err(v, "INVALID_TIME_RANGE", "CUT", {"remove": [{"start": 0, "end": 2}, {"start": 1, "end": 3}]}, 1, "p")   # overlap
    err(v, "INVALID_TIME_RANGE", "CUT", {"remove": [{"start": 2, "end": 3}, {"start": 0, "end": 1}]}, 1, "p")   # unsorted
    err(v, "INVALID_REQUEST", "CUT", {"remove": []}, 1, "p")
    err(v, "INVALID_REQUEST", "CUT", {"remove": [{"start": 0, "end": 1, "fade": 1}]}, 1, "p")
    s = v("SILENCE_REMOVE", {"ranges": [{"start": 0, "end": 1}], "margin": 0.1, "threshold_db": -40}, 1, "p")
    assert s["min_duration"] == 0.0 and s["threshold_db"] == -40.0
    err(v, "INVALID_REQUEST", "SILENCE_REMOVE", {"ranges": [{"start": 0, "end": 1}], "margin": -1}, 1, "p")
    err(v, "INVALID_REQUEST", "FADE_IN", {"duration": 0}, 1, "p")
    n = v("NORMALIZE", {"target_lufs": -16, "true_peak_db": -1, "sample_rate": 48000, "profile": "podcast"}, 1, "p")
    assert n["sample_rate"] == 48000 and "loudness_range_lu" not in n
    err(v, "INVALID_REQUEST", "NORMALIZE", {"target_lufs": -16}, 1, "p")                   # true_peak_db required, no default
    err(v, "INVALID_REQUEST", "NORMALIZE", {"true_peak_db": -1}, 1, "p")                   # target required, no default
    err(v, "INVALID_SAMPLE_RATE", "NORMALIZE", {"target_lufs": -16, "true_peak_db": -1, "sample_rate": 12345}, 1, "p")
    err(v, "INVALID_REQUEST", "NORMALIZE", {"target_lufs": -16, "true_peak_db": 1}, 1, "p")
    err(v, "INVALID_REQUEST", "NORMALIZE", {"target_lufs": -16, "true_peak_db": -1, "profile": "a\nb"}, 1, "p")
    m = v("MIX", {}, 3, "p")
    assert m["levels"] == [{"gain_db": 0.0, "mute": False}] * 3
    err(v, "INVALID_REQUEST", "MIX", {"levels": [{"gain_db": 0}]}, 2, "p")
    err(v, "INVALID_REQUEST", "MIX", {"levels": [{"gain_db": 0, "pan": 0.5}, {}]}, 2, "p")
    err(v, "UNSUPPORTED_OPERATION", "NOISE_REDUCTION", {"mode": "ai", "strength_db": 20}, 1, "p")
    err(v, "INVALID_REQUEST", "NOISE_REDUCTION", {"mode": "fft", "strength_db": 5}, 1, "p")
    assert v("MONO", None, 1, "p") == {}
    err(v, "INVALID_REQUEST", "MONO", {"weights": [1, 0]}, 1, "p")


def test_input_arity_and_references():
    err(parse_request, "INVALID_REQUEST", request_doc([{"op_id": "g", "type": "GAIN", "inputs": [], "parameters": {"gain_db": 1}}]))
    err(parse_request, "INVALID_REQUEST", request_doc([{"op_id": "m", "type": "MIX", "inputs": ["track:t1"]}]))
    err(parse_request, "INVALID_REQUEST", request_doc([{"op_id": "g", "type": "GAIN", "inputs": ["t1"], "parameters": {"gain_db": 1}}]))
    err(parse_request, "INVALID_REQUEST", request_doc([{"op_id": "g", "type": "GAIN", "inputs": ["file:/etc/passwd"], "parameters": {"gain_db": 1}}]))
    err(parse_request, "MISSING_INPUT", request_doc([{"op_id": "g", "type": "GAIN", "inputs": ["track:nope"], "parameters": {"gain_db": 1}}]))
    err(parse_request, "MISSING_INPUT", request_doc([{"op_id": "g", "type": "GAIN", "inputs": ["op:nope"], "parameters": {"gain_db": 1}}]))
    err(parse_request, "DEPENDENCY_ERROR", request_doc([{"op_id": "g", "type": "GAIN", "inputs": ["op:g"], "parameters": {"gain_db": 1}}]))
    err(parse_request, "DEPENDENCY_ERROR", request_doc([{"op_id": "g", "type": "GAIN", "inputs": ["track:t1"], "parameters": {"gain_db": 1}},
                                                         {"op_id": "g", "type": "GAIN", "inputs": ["track:t1"], "parameters": {"gain_db": 2}}]))
    err(parse_request, "MISSING_INPUT", request_doc([], tracks=[{"track_id": "t1", "source_id": "zzz"}]))
    err(parse_request, "MISSING_INPUT", request_doc([], outputs=[{"output_id": "o", "operation": "op:none", "path": "o.wav", "format": "wav"}]))
    err(parse_request, "INVALID_REQUEST", request_doc([{"op_id": "bad id", "type": "GAIN", "inputs": ["track:t1"], "parameters": {"gain_db": 1}}]))


def test_track_and_output_validation():
    err(parse_request, "INVALID_CHANNEL_LAYOUT", request_doc([], tracks=[{"track_id": "t1", "source_id": "a", "channel_layout": "quad"}]))
    err(parse_request, "INVALID_TIME_RANGE", request_doc([], tracks=[{"track_id": "t1", "source_id": "a", "range": {"start": 3, "end": 1}}]))
    err(parse_request, "UNSUPPORTED_FORMAT", request_doc([], outputs=[{"output_id": "o", "operation": "track:t1", "path": "o.wma", "format": "wma"}]))
    err(parse_request, "UNSUPPORTED_FORMAT", request_doc([], outputs=[{"output_id": "o", "operation": "track:t1", "path": "o.mp3", "format": "wav"}]))
    err(parse_request, "INVALID_SAMPLE_RATE", request_doc([], outputs=[{"output_id": "o", "operation": "track:t1", "path": "o.wav", "format": "wav", "expect": {"sample_rate": 47000}}]))
    err(parse_request, "INVALID_CHANNEL_LAYOUT", request_doc([], outputs=[{"output_id": "o", "operation": "track:t1", "path": "o.wav", "format": "wav", "expect": {"channel_layout": "3.0"}}]))
    err(parse_request, "OUTPUT_ERROR", request_doc([], outputs=[{"output_id": "o1", "operation": "track:t1", "path": "o.wav", "format": "wav"},
                                                                 {"output_id": "o2", "operation": "track:t1", "path": "o.wav", "format": "wav"}]))
    err(parse_request, "INVALID_REQUEST", request_doc([], outputs=[{"output_id": "o", "operation": "track:t1", "path": "o.wav", "format": "wav", "overwrite": "yes"}]))
    req = parse_request(request_doc([], outputs=[{"output_id": "o", "operation": "track:t1", "path": "o.flac", "format": "flac", "expect": {"duration": 6, "channels": 1}}]))
    assert req.project.outputs[0].expect == {"duration": 6.0, "duration_tolerance": 0.1, "channels": 1}


# ---- timeline
def test_timeline_trim_cut_mapping():
    segs = base_segments("a", 6.0)
    t = trim(segs, TimeRange(1.0, 4.0))
    assert [(s.timeline_start, s.timeline_end, s.source_start, s.source_end) for s in t] == [(0.0, 3.0, 1.0, 4.0)]
    c = cut(t, [TimeRange(1.0, 2.0)])
    assert [(s.timeline_start, s.timeline_end, s.source_start, s.source_end) for s in c] == [(0.0, 1.0, 1.0, 2.0), (1.0, 2.0, 3.0, 4.0)]
    assert total_duration(c) == 2.0
    assert c[0].to_dict()["source"] == {"start": 1.0, "end": 2.0}
    err(trim, "INVALID_TIME_RANGE", segs, TimeRange(5.0, 7.0))
    err(cut, "INVALID_TIME_RANGE", segs, [TimeRange(0.0, 6.0)])
    err(cut, "INVALID_TIME_RANGE", segs, [TimeRange(0.0, 6.5)])
    assert [r.to_dict() for r in complement([TimeRange(0.0, 1.0), TimeRange(5.0, 6.0)], 6.0)] == [{"start": 1.0, "end": 5.0}]
    assert [r.to_dict() for r in complement([TimeRange(2.0, 3.0)], 6.0)] == [{"start": 0.0, "end": 2.0}, {"start": 3.0, "end": 6.0}]


def test_silence_rules_are_pure_arithmetic():
    r = apply_silence_rules([TimeRange(0.0, 2.0), TimeRange(5.0, 5.2), TimeRange(5.5, 6.0)], margin=0.15, min_duration=0.4)
    assert [x.to_dict() for x in r] == [{"start": 0.15, "end": 1.85}]
    assert apply_silence_rules([TimeRange(0.0, 0.2)], 0.1, 0.0) == []


def test_mix_and_fade_rules():
    m = mix([base_segments("a", 4.0), base_segments("b", 6.0)])
    assert [(s.source_id, s.timeline_end, s.input_index) for s in m] == [("a", 4.0, 0), ("b", 4.0, 1)]
    fade_ranges(base_segments("a", 4.0), 1.0, 1.0)
    err(fade_ranges, "INVALID_TIME_RANGE", base_segments("a", 4.0), 5.0, None)


# ---- graph
def test_graph_order_implicit_nodes_and_outputs():
    req = parse_request(request_doc([{"op_id": "g", "type": "GAIN", "inputs": ["track:t1"], "parameters": {"gain_db": -3}}],
                                    tracks=[{"track_id": "t1", "source_id": "a", "range": {"start": 1, "end": 2}, "gain_db": -6}]))
    g = OperationGraph(req.project)
    assert g.order == ["track:t1", "op:t1.range", "op:t1.gain", "op:g"]
    assert g.nodes["op:t1.range"].implicit and g.nodes["op:t1.range"].type == "TRIM"
    assert g.nodes["op:g"].inputs == ["op:t1.gain"]
    assert g.output_nodes == {"main": "op:g"}


def test_graph_rejects_cycles_and_unreachable():
    ops = [{"op_id": "a", "type": "GAIN", "inputs": ["op:b"], "parameters": {"gain_db": 1}}, {"op_id": "b", "type": "GAIN", "inputs": ["op:a"], "parameters": {"gain_db": 1}}]
    err(OperationGraph, "DEPENDENCY_ERROR", parse_request(request_doc(ops, outputs=[{"output_id": "o", "operation": "op:a", "path": "o.wav", "format": "wav"}])).project)
    ops = [{"op_id": "a", "type": "GAIN", "inputs": ["track:t1"], "parameters": {"gain_db": 1}}, {"op_id": "b", "type": "GAIN", "inputs": ["track:t1"], "parameters": {"gain_db": 2}}]
    e = err(OperationGraph, "DEPENDENCY_ERROR", parse_request(request_doc(ops, outputs=[{"output_id": "o", "operation": "op:a", "path": "o.wav", "format": "wav"}])).project)
    assert e.details["unreachable"] == ["op:b"]
    err(OperationGraph, "DEPENDENCY_ERROR", parse_request(request_doc([{"op_id": "t1.gain", "type": "GAIN", "inputs": ["track:t1"], "parameters": {"gain_db": 1}}],
                                                                       tracks=[{"track_id": "t1", "source_id": "a", "gain_db": 2}])).project)


def test_graph_order_is_deterministic_for_parallel_branches():
    ops = [{"op_id": "z", "type": "GAIN", "inputs": ["track:t1"], "parameters": {"gain_db": 1}}, {"op_id": "a", "type": "GAIN", "inputs": ["track:t1"], "parameters": {"gain_db": 2}},
           {"op_id": "m", "type": "MIX", "inputs": ["op:z", "op:a"]}]
    g = OperationGraph(parse_request(request_doc(ops)).project)
    assert g.order == ["track:t1", "op:a", "op:z", "op:m"]


def test_identities_are_deterministic_and_parameter_sensitive():
    ops = [{"op_id": "g", "type": "GAIN", "inputs": ["track:t1"], "parameters": {"gain_db": -3}}]
    g1 = OperationGraph(parse_request(request_doc(ops)).project)
    ids1 = g1.identities({"a": "f" * 64}, {"ffmpeg-skill": "0.9.0"})
    ids2 = OperationGraph(parse_request(request_doc(ops)).project).identities({"a": "f" * 64}, {"ffmpeg-skill": "0.9.0"})
    assert ids1 == ids2 and set(ids1) == {"track:t1", "op:g"} and len(ids1["op:g"]) == 64
    assert g1.identities({"a": "e" * 64}, {"ffmpeg-skill": "0.9.0"})["op:g"] != ids1["op:g"]              # input content
    assert g1.identities({"a": "f" * 64}, {"ffmpeg-skill": "0.9.1"})["op:g"] != ids1["op:g"]              # tool version
    ops2 = [{"op_id": "g", "type": "GAIN", "inputs": ["track:t1"], "parameters": {"gain_db": -4}}]
    assert OperationGraph(parse_request(request_doc(ops2)).project).identities({"a": "f" * 64}, {"ffmpeg-skill": "0.9.0"})["op:g"] != ids1["op:g"]   # parameters
    ops3 = [{"op_id": "other", "type": "GAIN", "inputs": ["track:t1"], "parameters": {"gain_db": -3}}]
    assert OperationGraph(parse_request(request_doc(ops3)).project).identities({"a": "f" * 64}, {"ffmpeg-skill": "0.9.0"})["op:other"] == ids1["op:g"]  # op_id is a label, not identity


def test_canonical_json_is_stable():
    assert canonical_json({"b": 1, "a": [1.5, {"z": None, "y": "é"}]}) == '{"a":[1.5,{"y":"é","z":null}],"b":1}'
    assert stable_hash({"a": 1, "b": 2}) == stable_hash({"b": 2, "a": 1})
    with pytest.raises(ValueError):
        canonical_json({"a": float("nan")})


def test_error_table():
    assert len(ERROR_CODES) == 15 and len(set(EXIT_CODES.values())) == 15 and min(EXIT_CODES.values()) == 2
    e = AudioError("TOOL_ERROR", "x")
    assert e.retryable is True and e.to_dict()["retryable"] is True and e.exit_code == EXIT_CODES["TOOL_ERROR"]
    assert AudioError("TOOL_ERROR", "x", retryable=False).retryable is False
    with pytest.raises(ValueError):
        AudioError("NOPE", "x")


# ---- path policy
def test_filename_rules():
    for bad in ["CON", "con.wav", "LPT1.mp3", "a<b.wav", "a:b.wav", "x\x00.wav", "trailing.", "trailing ", "-o.wav", "..", "", "a" * 300 + ".wav", "tab\t.wav"]:
        err(check_filename, "PATH_NOT_ALLOWED", bad)
    for ok in ["main.wav", "コンサート.flac", "a.b.c.m4a", "console.wav", ".hidden.wav", "CONCERT.wav"]:
        check_filename(ok)


def test_path_policy_inputs_and_outputs(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "in.wav").write_bytes(b"x")
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"y")
    pol = PathPolicy(str(ws), [str(ws)])
    assert pol.resolve_input("in.wav") == (ws / "in.wav").resolve()
    err(pol.resolve_input, "INVALID_INPUT", "missing.wav")
    err(pol.resolve_input, "PATH_NOT_ALLOWED", str(outside))
    err(pol.resolve_input, "PATH_NOT_ALLOWED", "../outside.wav")
    err(pol.resolve_input, "INVALID_INPUT", str(ws))                 # a directory
    err(pol.resolve_input, "PATH_NOT_ALLOWED", "in\x00.wav")
    err(pol.resolve_input, "INVALID_REQUEST", 5)
    assert pol.resolve_write_path("out/x.wav") == ws / "out" / "x.wav"
    err(pol.resolve_write_path, "PATH_NOT_ALLOWED", "../x.wav")
    err(pol.resolve_write_path, "PATH_NOT_ALLOWED", str(tmp_path / "x.wav"))
    err(pol.resolve_write_path, "PATH_NOT_ALLOWED", "out/../../x.wav")
    err(pol.resolve_write_path, "PATH_NOT_ALLOWED", "out/CON.wav")
    err(pol.resolve_write_path, "PATH_NOT_ALLOWED", "-flag.wav")
    err(pol.resolve_write_path, "OUTPUT_ERROR", str(ws))               # an existing directory as an output file
    err(PathPolicy, "PATH_NOT_ALLOWED", str(tmp_path / "nope"))
    err(PathPolicy, "PATH_NOT_ALLOWED", str(ws), [str(tmp_path / "nope")])
    # a workspace named like a prefix of another directory must not match by string prefix
    (tmp_path / "ws_evil").mkdir()
    err(pol.resolve_write_path, "PATH_NOT_ALLOWED", str(tmp_path / "ws_evil" / "x.wav"))


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_symlink_escape_is_refused(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    secret = tmp_path / "secret.wav"
    secret.write_bytes(b"s")
    (ws / "link.wav").symlink_to(secret)
    (ws / "linkdir").symlink_to(tmp_path)
    pol = PathPolicy(str(ws), [str(ws)])
    err(pol.resolve_input, "PATH_NOT_ALLOWED", "link.wav")
    err(pol.resolve_input, "PATH_NOT_ALLOWED", "linkdir/secret.wav")
    err(pol.resolve_write_path, "PATH_NOT_ALLOWED", "linkdir/out.wav")
    err(pol.resolve_write_path, "PATH_NOT_ALLOWED", "linkdir/newdir/out.wav")
    # without allowed roots an input symlink is readable but still resolved: the collision check sees the real path
    assert PathPolicy(str(ws)).resolve_input("link.wav") == secret.resolve()


def test_windows_style_paths_are_handled():
    err(check_filename, "PATH_NOT_ALLOWED", "aux.wav")
    err(check_filename, "PATH_NOT_ALLOWED", "Nul")
    err(check_filename, "PATH_NOT_ALLOWED", "x?.wav")
    err(check_filename, "PATH_NOT_ALLOWED", "x|y.wav")
    check_filename("aux_track.wav")


def test_version_is_semver():
    assert len(VERSION.split(".")) == 3
