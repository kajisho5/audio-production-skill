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
from audio_production.timeline import apply_silence_rules, base_segments, complement, concat, cut, fade_ranges, mix, total_duration, trim
from conftest import request_doc


def err(fn, code, *args, **kw):
    with pytest.raises(AudioError) as ei:
        fn(*args, **kw)
    assert ei.value.code == code, ei.value
    return ei.value


# ---- request schema
def test_noise_reduction_strength_floor_is_20():
    # ADR-17: strength_db becomes afftdn nf=-strength_db and afftdn accepts only -80..-20, so 10..19.99 is refused here
    # (it used to validate and then fail at run time with TOOL_ERROR)
    v = validate_parameters
    assert OPERATION_TYPES["NOISE_REDUCTION"]["parameters"]["strength_db"]["min"] == 20.0
    for bad in (10, 19, 19.99):
        e = err(v, "INVALID_REQUEST", "NOISE_REDUCTION", {"mode": "fft", "strength_db": bad}, 1, "p")
        assert "afftdn" in e.message and "-80..-20" in e.message and e.details == {"field": "p.strength_db", "min": 20.0, "max": 60.0}
    assert v("NOISE_REDUCTION", {"mode": "fft", "strength_db": 20}, 1, "p")["strength_db"] == 20.0
    assert v("NOISE_REDUCTION", {"mode": "fft", "strength_db": 60}, 1, "p")["strength_db"] == 60.0
    err(v, "INVALID_REQUEST", "NOISE_REDUCTION", {"mode": "fft", "strength_db": 60.5}, 1, "p")
    err(v, "INVALID_REQUEST", "NOISE_REDUCTION", {"mode": "fft", "strength_db": True}, 1, "p")   # not a number: generic message


def test_minimal_request_parses():
    req = parse_request(request_doc([{"op_id": "g", "type": "GAIN", "inputs": ["track:t1"], "parameters": {"gain_db": -3}}]))
    assert req.project.operations[0].parameters == {"gain_db": -3.0, "audio_stream": 0}
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
    assert v("GAIN", {"gain_db": 3}, 1, "p") == {"gain_db": 3.0, "audio_stream": 0}
    err(v, "INVALID_REQUEST", "GAIN", {}, 1, "p")                       # required
    err(v, "INVALID_REQUEST", "GAIN", {"gain_db": 100}, 1, "p")         # range
    err(v, "INVALID_REQUEST", "GAIN", {"gain_db": "3dB"}, 1, "p")       # type (a string could carry a filter)
    err(v, "INVALID_REQUEST", "GAIN", {"gain_db": True}, 1, "p")        # bool is not a number
    err(v, "INVALID_REQUEST", "GAIN", {"gain_db": 3, "curve": "x"}, 1, "p")   # unknown
    err(v, "INVALID_REQUEST", "GAIN", {"gain_db": float("nan")}, 1, "p")
    assert v("GAIN", {"gain_db": 3, "audio_stream": 1}, 1, "p") == {"gain_db": 3.0, "audio_stream": 1}
    err(v, "INVALID_REQUEST", "GAIN", {"gain_db": 3, "audio_stream": -1}, 1, "p")     # negative
    err(v, "INVALID_REQUEST", "GAIN", {"gain_db": 3, "audio_stream": 1.5}, 1, "p")    # not an integer
    err(v, "INVALID_REQUEST", "GAIN", {"gain_db": 3, "audio_stream": "1"}, 1, "p")    # not a number at all
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
    assert v("MONO", None, 1, "p") == {"audio_stream": 0}
    assert v("MONO", {"audio_stream": 2}, 1, "p") == {"audio_stream": 2}
    dy = v("DYNAMICS", {"compressor": {"threshold_db": -18, "ratio": 3}, "limiter": {"ceiling_db": -1}}, 1, "p")
    assert dy == {"compressor": {"threshold_db": -18.0, "ratio": 3.0}, "limiter": {"ceiling_db": -1.0}, "audio_stream": 0}
    err(v, "INVALID_REQUEST", "DYNAMICS", {}, 1, "p")                                         # at least one stage
    err(v, "INVALID_REQUEST", "DYNAMICS", {"compressor": {"ratio": 0.5}}, 1, "p")             # range
    err(v, "INVALID_REQUEST", "DYNAMICS", {"gate": {"threshold_db": -20, "hold_ms": 5}}, 1, "p")   # unknown field
    err(v, "INVALID_REQUEST", "DYNAMICS", {"compressor": "fast"}, 1, "p")
    assert v("VOICE_CLEANUP", {"level": "strong"}, 1, "p") == {"level": "strong", "audio_stream": 0}
    err(v, "INVALID_REQUEST", "VOICE_CLEANUP", {}, 1, "p")                                    # level required, no default
    err(v, "INVALID_REQUEST", "VOICE_CLEANUP", None, 1, "p")
    err(v, "UNSUPPORTED_OPERATION", "VOICE_CLEANUP", {"level": "extreme"}, 1, "p")            # not one of the ffmpeg-skill levels
    err(v, "INVALID_REQUEST", "VOICE_CLEANUP", {"level": True}, 1, "p")                       # the 1.12 boolean is not a level
    err(v, "INVALID_REQUEST", "VOICE_CLEANUP", {"level": "medium", "strength_db": 30}, 1, "p")   # no tunable denoise strength
    err(v, "INVALID_REQUEST", "VOICE_CLEANUP", {"level": "medium", "audio_stream": -1}, 1, "p")
    c = v("CONCAT", {"crossfade": 0.5, "channels": 2}, 3, "p")
    assert c == {"crossfade": 0.5, "channels": 2}
    assert v("CONCAT", None, 2, "p") == {"crossfade": 0.0}
    err(v, "INVALID_REQUEST", "CONCAT", {"channels": 3}, 2, "p")
    err(v, "INVALID_SAMPLE_RATE", "CONCAT", {"sample_rate": 12345}, 2, "p")
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


def test_concat_mapping():
    c = concat([base_segments("a", 6.0), base_segments("b", 4.0)])
    assert [(s.source_id, s.timeline_start, s.timeline_end, s.input_index) for s in c] == [("a", 0.0, 6.0, 0), ("b", 6.0, 10.0, 1)]
    c = concat([base_segments("a", 6.0), base_segments("b", 4.0), base_segments("c", 2.0)], crossfade=0.5)
    assert [(s.timeline_start, s.timeline_end) for s in c] == [(0.0, 6.0), (5.5, 9.5), (9.0, 11.0)] and total_duration(c) == 11.0
    err(concat, "INVALID_TIME_RANGE", [base_segments("a", 6.0), base_segments("b", 0.4)], 0.5)


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


def test_identity_is_sensitive_to_audio_stream():
    """Two operations that differ only in audio_stream must not collide in the intermediate cache."""
    base = {"a": "f" * 64}
    versions = {"ffmpeg-skill": "0.12.0"}

    def ids_for(typ, params):
        ops = [{"op_id": "x", "type": typ, "inputs": ["track:t1"], "parameters": params}]
        return OperationGraph(parse_request(request_doc(ops)).project).identities(base, versions)["op:x"]

    for typ, params in (
        ("GAIN", {"gain_db": -3}),
        ("FADE_IN", {"duration": 0.5}),
        ("FADE_OUT", {"duration": 0.5}),
        ("MONO", {}),
        ("NOISE_REDUCTION", {"mode": "fft", "strength_db": 20}),
        ("DYNAMICS", {"limiter": {"ceiling_db": -1}}),
        ("VOICE_CLEANUP", {"level": "medium"}),
    ):
        default_id = ids_for(typ, params)                                          # audio_stream omitted -> default 0
        explicit_zero_id = ids_for(typ, {**params, "audio_stream": 0})
        stream_one_id = ids_for(typ, {**params, "audio_stream": 1})
        assert default_id == explicit_zero_id                                      # the default and an explicit 0 are the same identity
        assert stream_one_id != default_id, (typ, "audio_stream must change the cache identity")
    # STEREO / DOWNMIX need a matching channel input to plan (not exercised here), but identity is computed
    # from parameters alone and does not depend on planning, so they follow the same rule via validate_parameters:
    from audio_production.model import validate_parameters
    for typ in ("STEREO", "DOWNMIX"):
        assert validate_parameters(typ, {}, 1, "p")["audio_stream"] == 0
        assert validate_parameters(typ, {"audio_stream": 3}, 1, "p")["audio_stream"] == 3


def test_identity_is_sensitive_to_voice_level():
    base, versions = {"a": "f" * 64}, {"ffmpeg-skill": "2.21.0"}

    def ident(level):
        ops = [{"op_id": "x", "type": "VOICE_CLEANUP", "inputs": ["track:t1"], "parameters": {"level": level}}]
        return OperationGraph(parse_request(request_doc(ops)).project).identities(base, versions)["op:x"]

    assert len({ident(level) for level in ("light", "medium", "strong")}) == 3
    assert ident("medium") == ident("medium")


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


def test_partial_output_removal_waits_until_the_file_is_deletable(tmp_path, monkeypatch):
    """A killed tool's ffmpeg can hold its output open for a moment (Windows: unlink raises PermissionError until the
    handle is released). _remove_partial must retry until the file is really gone, not swallow the first failure."""
    from pathlib import Path
    from audio_production.executor import Executor
    target = tmp_path / "partial.wav"
    target.write_bytes(b"x")
    real_unlink = Path.unlink
    calls = {"n": 0}

    def busy_unlink(self, *a, **kw):
        calls["n"] += 1
        if self == target and calls["n"] <= 3:
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", busy_unlink)
    Executor._remove_partial(target)
    assert not target.exists() and calls["n"] == 4
    Executor._remove_partial(target)                            # already gone: returns at once
    assert calls["n"] == 4


# ---- FILLER_REMOVE (ADR-18): word timings + an explicit lexicon -> removal ranges, pure arithmetic
def _w(start, end, text):
    return {"start": start, "end": end, "text": text}


def test_filler_normalisation_and_presets():
    from audio_production.fillers import LEXICON_PRESETS, effective_lexicon, normalise
    assert normalise("えーー、") == normalise("えー") == normalise("え〜") == "えー"
    assert normalise(" Um, ") == normalise("UM") == "um" and normalise("ｴｰ") == normalise("エー")
    assert normalise("...") == ""
    assert set(LEXICON_PRESETS) == {"ja", "en"}
    for ambiguous in ("あの", "その", "まあ", "なんか", "like"):          # ordinary words in running speech are never preset fillers
        assert normalise(ambiguous) not in effective_lexicon(["ja", "en"], [], [])
    lex = effective_lexicon(["ja"], ["あの"], ["えっと"])
    assert "あの" in lex and "えっと" not in lex and "えー" in lex and "um" not in lex


def test_filler_matching_runs_pad_and_merge():
    from audio_production.fillers import effective_lexicon, match_fillers, removal_ranges
    lex = effective_lexicon(["ja", "en"], [], [])
    words = [_w(0.0, 0.8, "本日は"), _w(0.9, 1.1, "えー"), _w(1.1, 1.3, "と"), _w(1.6, 2.0, "症例"), _w(2.4, 2.6, "uh"), _w(2.7, 2.9, "Um,"), _w(3.0, 4.0, "です")]
    runs = match_fillers(words, lex)
    assert runs == [(1, 3), (4, 5), (5, 6)]                             # "えー" + "と" is one run ("えーと"): the longest run wins
    assert removal_ranges(words, runs, 0.0, 0.0, 4.0) == [(0.9, 1.3), (2.4, 2.6), (2.7, 2.9)]
    # pad grows each side but never into a neighbouring word; merge_gap joins fillers with no word between them
    # (padded ranges that meet -- "uh" and "Um," padded towards each other -- become one range)
    assert removal_ranges(words, runs, 0.2, 0.0, 4.0) == [(0.8, 1.5), (2.2, 3.0)]
    assert removal_ranges(words, runs, 0.0, 0.1, 4.0) == [(0.9, 1.3), (2.4, 2.9)]
    assert removal_ranges(words, runs, 0.0, 2.0, 4.0)[0] == (0.9, 1.3)  # a real word ("症例") between: never merged
    # touching tokens only: a separate "えー" and "と" a second apart are two words, "と" is kept
    apart = [_w(0.0, 0.2, "えー"), _w(1.2, 1.4, "と")]
    assert match_fillers(apart, lex) == [(0, 1)]
    # clipped to the input: a filler running past the end is cut at the end; at the start, never below 0
    assert removal_ranges([_w(0.0, 0.3, "um"), _w(0.3, 5.2, "end")], [(0, 1)], 0.5, 0.0, 5.0) == [(0.0, 0.3)]
    assert removal_ranges([_w(1.0, 2.0, "x"), _w(4.8, 5.4, "um")], [(1, 2)], 0.1, 0.0, 5.0) == [(4.7, 5.0)]


def test_filler_remove_parameters():
    v = validate_parameters
    words = [_w(0.0, 1.0, "本日は"), _w(1.0, 1.5, "えー")]
    out = v("FILLER_REMOVE", {"words": list(reversed(words)), "lexicon": {"presets": ["ja"], "words": ["あの", "あの"]}}, 1, "p")
    assert out == {"words": [{**w, "start": float(w["start"]), "end": float(w["end"])} for w in words], "lexicon": {"presets": ["ja"], "words": ["あの"], "keep": []},
                   "pad": 0.0, "merge_gap": 0.0}                       # sorted by time, lexicon de-duplicated, no declick unless asked
    e = err(v, "INVALID_REQUEST", "FILLER_REMOVE", {"lexicon": {"presets": ["ja"]}}, 1, "p")
    assert e.details["reason"] == "word_timestamps_missing"           # no ASR here: timings are required
    e = err(v, "INVALID_REQUEST", "FILLER_REMOVE", {"words": [{"text": "えー"}], "lexicon": {"presets": ["ja"]}}, 1, "p")
    assert e.details["reason"] == "word_timestamps_missing"
    e = err(v, "INVALID_REQUEST", "FILLER_REMOVE", {"words": [{"start": None, "end": None, "text": "えー"}], "lexicon": {"presets": ["ja"]}}, 1, "p")
    assert e.details["reason"] == "word_timestamps_missing"
    err(v, "INVALID_REQUEST", "FILLER_REMOVE", {"words": words}, 1, "p")                               # lexicon required, no default
    err(v, "INVALID_REQUEST", "FILLER_REMOVE", {"words": words, "lexicon": {}}, 1, "p")                # empty lexicon
    err(v, "INVALID_REQUEST", "FILLER_REMOVE", {"words": words, "lexicon": {"presets": ["ja"], "keep": list(__import__("audio_production.fillers", fromlist=["x"]).LEXICON_PRESETS["ja"])}}, 1, "p")
    err(v, "INVALID_REQUEST", "FILLER_REMOVE", {"words": words, "lexicon": {"presets": ["fr"]}}, 1, "p")
    err(v, "INVALID_REQUEST", "FILLER_REMOVE", {"words": words, "lexicon": {"presets": ["ja"]}, "pad": 0.6}, 1, "p")
    err(v, "INVALID_REQUEST", "FILLER_REMOVE", {"words": words, "lexicon": {"presets": ["ja"]}, "declick_ms": -1}, 1, "p")
    err(v, "INVALID_TIME_RANGE", "FILLER_REMOVE", {"words": [_w(2.0, 1.0, "えー")], "lexicon": {"presets": ["ja"]}}, 1, "p")
    err(v, "INVALID_REQUEST", "FILLER_REMOVE", {"words": [{**words[0], "speaker_id": "x"}], "lexicon": {"presets": ["ja"]}}, 1, "p")
    # a transcription-skill transcript: its segments' words are read; a segment without word timestamps is refused
    tr = {"schema": "transcription-skill/transcript/0.1", "id": "tr_1", "asset_id": "asset_1", "language": "ja", "duration": 6.0,
          "segments": [{"id": "seg_0001", "start": 0.0, "end": 1.5, "text": "本日はえー", "words": [{**w, "confidence": 0.9} for w in words], "speaker_id": None}]}
    out = v("FILLER_REMOVE", {"transcript": tr, "lexicon": {"presets": ["ja"]}}, 1, "p")
    assert [w["text"] for w in out["words"]] == ["本日は", "えー"] and out["words"][0]["confidence"] == 0.9
    assert out["transcript"] == {"schema": "transcription-skill/transcript/0.1", "id": "tr_1", "asset_id": "asset_1", "language": "ja"}
    tr2 = {**tr, "segments": tr["segments"] + [{"id": "seg_0002", "start": 2.0, "end": 3.0, "text": "x", "words": None, "speaker_id": None}]}
    e = err(v, "INVALID_REQUEST", "FILLER_REMOVE", {"transcript": tr2, "lexicon": {"presets": ["ja"]}}, 1, "p")
    assert e.details["reason"] == "word_timestamps_missing" and e.details["segments"] == ["seg_0002"]
    err(v, "INVALID_REQUEST", "FILLER_REMOVE", {"transcript": {**tr, "schema": "other/1"}, "lexicon": {"presets": ["ja"]}}, 1, "p")
    e = err(v, "INVALID_REQUEST", "FILLER_REMOVE", {"words": words, "transcript": tr, "lexicon": {"presets": ["ja"]}}, 1, "p")
    assert e.details["reason"] == "ambiguous_word_source"


def test_mix_duck_parameters():
    v = validate_parameters
    m = v("MIX", {"duck": {"input": 1, "amount_db": 12}}, 2, "p")
    assert m["duck"] == {"input": 1, "amount_db": 12.0} and m["levels"] == [{"gain_db": 0.0, "mute": False}] * 2
    m = v("MIX", {"duck": {"input": 2, "amount_db": 20, "threshold_db": -30, "attack_ms": 5, "release_ms": 300}}, 3, "p")
    assert m["duck"] == {"input": 2, "amount_db": 20.0, "threshold_db": -30.0, "attack_ms": 5.0, "release_ms": 300.0}
    err(v, "INVALID_REQUEST", "MIX", {"duck": {"input": 1}}, 2, "p")                                  # amount required, no default
    err(v, "INVALID_REQUEST", "MIX", {"duck": {"input": 0, "amount_db": 12}}, 2, "p")                 # input 0 is the key
    err(v, "INVALID_REQUEST", "MIX", {"duck": {"input": 2, "amount_db": 12}}, 2, "p")                 # no such input
    err(v, "INVALID_REQUEST", "MIX", {"duck": {"input": 1.5, "amount_db": 12}}, 2, "p")
    err(v, "INVALID_REQUEST", "MIX", {"duck": {"input": 1, "amount_db": 61}}, 2, "p")                 # sidechaincompress range
    err(v, "INVALID_REQUEST", "MIX", {"duck": {"input": 1, "amount_db": 12, "ratio": 4}}, 2, "p")     # unknown field
    err(v, "INVALID_REQUEST", "MIX", {"duck": {"input": 1, "amount_db": 12}, "levels": [{"mute": True}, {}]}, 2, "p")
    err(v, "INVALID_REQUEST", "MIX", {"duck": {"input": 1, "amount_db": 12}, "levels": [{}, {"mute": True}]}, 2, "p")
