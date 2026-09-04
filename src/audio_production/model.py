"""Typed Audio Project Model and request validation.

Concepts (docs/architecture.md):
  AudioProject   sources + tracks + operations + outputs
  AudioSource    one media file (path), identified by source_id; fingerprinted (sha256) at execution
  AudioTrack     a generic track over one source (label, expected channel layout, optional source range and gain)
  AudioOperation typed node of the operation graph: op_id, type, inputs (refs), parameters
  AudioOutput    a terminal artifact: output_id, operation ref, path, format, expectations
  AudioSegment   timeline range <-> source range mapping (timeline.py)
  OperationDependency / OperationResult  graph.py / executor.py

Validation is structural and semantic but never touches the file system: the PathPolicy (security.py) and the
executor do that. Unknown fields are rejected everywhere; fields that could carry a command are rejected by name."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import REQUEST_SCHEMA_VERSION, SKILL_ID
from .errors import AudioError

REQUEST_SCHEMA_ID = f"{SKILL_ID}/request@{REQUEST_SCHEMA_VERSION}"
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
REF_RE = re.compile(r"^(track|op):([A-Za-z0-9][A-Za-z0-9._-]{0,63})$")

FORBIDDEN_KEYS = frozenset({"command", "commands", "argv", "args", "cmd", "shell", "exec", "executable", "script",
                            "filter", "filters", "filter_complex", "af", "ffmpeg", "env", "cwd"})

OUTPUT_FORMATS: Dict[str, Dict[str, Any]] = {
    # format -> extension, codec that ffmpeg-skill picks for that extension, capability it needs
    "wav": {"extension": ".wav", "codec": "pcm_s16le", "capability": "encoder:pcm_s16le", "lossless": True},
    "flac": {"extension": ".flac", "codec": "flac", "capability": "encoder:flac", "lossless": True},
    "mp3": {"extension": ".mp3", "codec": "mp3", "capability": "encoder:libmp3lame", "lossless": False},
    "m4a": {"extension": ".m4a", "codec": "aac", "capability": "encoder:aac", "lossless": False},
    "aac": {"extension": ".aac", "codec": "aac", "capability": "encoder:aac", "lossless": False},
    "ogg": {"extension": ".ogg", "codec": "vorbis", "capability": "encoder:libvorbis", "lossless": False},
    "opus": {"extension": ".opus", "codec": "opus", "capability": "encoder:libopus", "lossless": False},
}
INTERMEDIATE_FORMAT = "wav"   # every intermediate artifact is PCM WAV: no generation loss between operations

CHANNEL_LAYOUTS: Dict[str, int] = {"mono": 1, "stereo": 2, "5.1": 6, "7.1": 8}
SAMPLE_RATES = (8000, 11025, 16000, 22050, 24000, 32000, 44100, 48000, 88200, 96000)

MAX_DURATION = 24 * 3600.0
MAX_MIX_INPUTS = 8
MAX_RANGES = 10000
MAX_OPERATIONS = 1000

# ---- parameter schemas: name -> {type, required, min, max, enum, description}
_NUM, _INT, _BOOL, _STR, _RANGES, _LEVELS = "number", "integer", "boolean", "string", "ranges", "levels"

OPERATION_TYPES: Dict[str, Dict[str, Any]] = {
    "GAIN": {"description": "Apply a fixed gain in dB", "inputs": (1, 1), "parameters": {
        "gain_db": {"type": _NUM, "required": True, "min": -60.0, "max": 60.0, "description": "gain in dB (negative attenuates)"}}},
    "TRIM": {"description": "Keep one source time range [start, end)", "inputs": (1, 1), "parameters": {
        "start": {"type": _NUM, "required": True, "min": 0.0, "max": MAX_DURATION, "description": "source time in seconds"},
        "end": {"type": _NUM, "required": True, "min": 0.0, "max": MAX_DURATION, "description": "source time in seconds, > start"}}},
    "CUT": {"description": "Remove explicit source time ranges; the remainder is joined in order", "inputs": (1, 1), "parameters": {
        "remove": {"type": _RANGES, "required": True, "description": "ranges to remove, sorted, non-overlapping"}}},
    "SILENCE_REMOVE": {"description": "Remove explicit silence ranges (decided by the caller) with margin and minimum duration rules", "inputs": (1, 1), "parameters": {
        "ranges": {"type": _RANGES, "required": True, "description": "silence ranges to remove, sorted, non-overlapping (as observed by the caller, e.g. media-analysis-skill)"},
        "margin": {"type": _NUM, "required": False, "min": 0.0, "max": 10.0, "default": 0.0, "description": "seconds of the range kept on each side (the removed part shrinks by margin at both ends)"},
        "min_duration": {"type": _NUM, "required": False, "min": 0.0, "max": MAX_DURATION, "default": 0.0, "description": "ranges shorter than this (after margin) are not removed"},
        "threshold_db": {"type": _NUM, "required": False, "min": -120.0, "max": 0.0, "description": "threshold the caller used to find the ranges; recorded in provenance, not applied"}}},
    "FADE_IN": {"description": "Linear fade in from the start", "inputs": (1, 1), "parameters": {
        "duration": {"type": _NUM, "required": True, "min": 0.001, "max": 3600.0, "description": "seconds"}}},
    "FADE_OUT": {"description": "Linear fade out to the end", "inputs": (1, 1), "parameters": {
        "duration": {"type": _NUM, "required": True, "min": 0.001, "max": 3600.0, "description": "seconds"}}},
    "NORMALIZE": {"description": "Two-pass EBU R128 loudness normalisation (ffmpeg loudnorm, linear mode)", "inputs": (1, 1), "parameters": {
        "target_lufs": {"type": _NUM, "required": True, "min": -70.0, "max": -5.0, "description": "integrated loudness target in LUFS; no default, the caller / profile decides"},
        "true_peak_db": {"type": _NUM, "required": True, "min": -20.0, "max": 0.0, "description": "true-peak ceiling in dBTP; no default"},
        "loudness_range_lu": {"type": _NUM, "required": False, "min": 1.0, "max": 50.0, "description": "loudness range target in LU (omit: ffmpeg-skill default 11)"},
        "tolerance_lufs": {"type": _NUM, "required": False, "min": 0.0, "max": 10.0, "description": "verification: measured integrated loudness must be within this of target_lufs, else VALIDATION_ERROR (omit: measure and report only)"},
        "sample_rate": {"type": _INT, "required": False, "enum": list(SAMPLE_RATES), "description": "output sample rate (omit: keep the input's)"},
        "profile": {"type": _STR, "required": False, "max_length": 64, "description": "label of the loudness profile the caller applied; recorded in provenance only"}}},
    "MIX": {"description": "Sum 2..8 inputs; output duration follows the first input (ffmpeg-skill amix duration=first)", "inputs": (2, MAX_MIX_INPUTS), "parameters": {
        "levels": {"type": _LEVELS, "required": False, "description": "per-input {gain_db, mute}, aligned with inputs (omit: 0 dB, unmuted)"}}},
    "MONO": {"description": "Down-mix to one channel (stereo input: 0.5*L + 0.5*R)", "inputs": (1, 1), "parameters": {}},
    "STEREO": {"description": "Force two channels (mono is duplicated to both sides)", "inputs": (1, 1), "parameters": {}},
    "DOWNMIX": {"description": "Down-mix 5.1 / 7.1 to stereo with standard centre / LFE / surround weights", "inputs": (1, 1), "parameters": {}},
    "NOISE_REDUCTION": {"description": "FFT noise reduction (ffmpeg afftdn, adaptive noise tracking)", "inputs": (1, 1), "parameters": {
        "mode": {"type": _STR, "required": True, "enum": ["fft"], "description": "only 'fft' (afftdn) is implemented"},
        "strength_db": {"type": _NUM, "required": True, "min": 10.0, "max": 60.0, "description": "noise floor to remove in dB"}}},
}

# declared, not implemented: ffmpeg-skill's public contract has no tool for them (docs/ffmpeg-skill.md)
UNSUPPORTED_OPERATIONS: Dict[str, str] = {
    "CONCAT": "ffmpeg-skill/join requires a video stream; no audio-only concatenation tool exists in ffmpeg-skill 0.9",
    "CHANNEL_MAP": "ffmpeg-skill exposes no typed channel mapping (only --mono / --stereo / --downmix, provided as MONO / STEREO / DOWNMIX)",
    "RESAMPLE": "ffmpeg-skill/audio has no sample-rate flag; only ffmpeg-skill/loudness --sample-rate exists (provided as NORMALIZE.sample_rate)",
    "DYNAMICS": "ffmpeg-skill has no typed compressor / limiter / gate parameters (only the fixed --voice chain)",
    "FORMAT_CONVERT": "format conversion is an AudioOutput property (outputs[].format), not an operation",
}


@dataclass
class TimeRange:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> Dict[str, float]:
        return {"start": self.start, "end": self.end}


@dataclass
class AudioSource:
    source_id: str
    path: str


@dataclass
class AudioTrack:
    track_id: str
    source_id: str
    label: Optional[str] = None
    channel_layout: Optional[str] = None      # expected layout; verified against the probed input at execution
    range: Optional[TimeRange] = None         # source range kept (implicit TRIM)
    gain_db: float = 0.0                      # implicit GAIN


@dataclass
class AudioOperation:
    op_id: str
    type: str
    inputs: List[str]
    parameters: Dict[str, Any] = field(default_factory=dict)
    implicit: bool = False                    # derived from a track's range / gain_db


@dataclass
class AudioOutput:
    output_id: str
    operation: str                            # ref "op:<id>" or "track:<id>"
    path: str
    format: str
    overwrite: bool = False
    expect: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AudioProject:
    project_id: str
    sources: List[AudioSource]
    tracks: List[AudioTrack]
    operations: List[AudioOperation]
    outputs: List[AudioOutput]

    def source(self, source_id: str) -> AudioSource:
        return next(s for s in self.sources if s.source_id == source_id)

    def track(self, track_id: str) -> AudioTrack:
        return next(t for t in self.tracks if t.track_id == track_id)


@dataclass
class AudioRequest:
    project: AudioProject
    options: Dict[str, Any]


# ---- validation helpers
def _reject_forbidden(obj: Any, where: str) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise AudioError("INVALID_REQUEST", f"{where}: object keys must be strings")
            if k.lower() in FORBIDDEN_KEYS:
                raise AudioError("INVALID_REQUEST", f"{where}: field {k!r} is not accepted (this skill never takes commands, argv, filters or executables)",
                                 {"field": k, "reason": "forbidden_field"})
            _reject_forbidden(v, f"{where}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _reject_forbidden(v, f"{where}[{i}]")


def _obj(value: Any, where: str, allowed: Tuple[str, ...], required: Tuple[str, ...]) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise AudioError("INVALID_REQUEST", f"{where} must be an object", {"field": where})
    unknown = sorted(k for k in value if k not in allowed)
    if unknown:
        raise AudioError("INVALID_REQUEST", f"{where}: unknown field(s) {unknown}", {"field": where, "unknown": unknown, "allowed": list(allowed)})
    missing = [k for k in required if k not in value]
    if missing:
        raise AudioError("INVALID_REQUEST", f"{where}: missing required field(s) {missing}", {"field": where, "missing": missing})
    return value


def _id(value: Any, where: str) -> str:
    if not isinstance(value, str) or not ID_RE.match(value):
        raise AudioError("INVALID_REQUEST", f"{where} must match {ID_RE.pattern}", {"field": where})
    return value


def _number(value: Any, where: str, lo: Optional[float] = None, hi: Optional[float] = None, integer: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AudioError("INVALID_REQUEST", f"{where} must be a number", {"field": where})
    if isinstance(value, float) and not math.isfinite(value):
        raise AudioError("INVALID_REQUEST", f"{where} must be finite", {"field": where})
    if integer and (isinstance(value, float) and not value.is_integer()):
        raise AudioError("INVALID_REQUEST", f"{where} must be an integer", {"field": where})
    if lo is not None and value < lo or hi is not None and value > hi:
        raise AudioError("INVALID_REQUEST", f"{where} must be within [{lo}, {hi}], got {value}", {"field": where, "min": lo, "max": hi})
    return float(value)


def _time_range(value: Any, where: str) -> TimeRange:
    d = _obj(value, where, ("start", "end"), ("start", "end"))
    start = _number(d["start"], f"{where}.start", 0.0, MAX_DURATION)
    end = _number(d["end"], f"{where}.end", 0.0, MAX_DURATION)
    if end <= start:
        raise AudioError("INVALID_TIME_RANGE", f"{where}: end ({end}) must be greater than start ({start})", {"field": where, "start": start, "end": end})
    return TimeRange(start, end)


def _ranges(value: Any, where: str) -> List[TimeRange]:
    if not isinstance(value, list) or not value:
        raise AudioError("INVALID_REQUEST", f"{where} must be a non-empty array of {{start, end}}", {"field": where})
    if len(value) > MAX_RANGES:
        raise AudioError("INVALID_REQUEST", f"{where}: too many ranges (max {MAX_RANGES})", {"field": where})
    out = [_time_range(r, f"{where}[{i}]") for i, r in enumerate(value)]
    for a, b in zip(out, out[1:]):
        if b.start < a.end:
            raise AudioError("INVALID_TIME_RANGE", f"{where}: ranges must be sorted and non-overlapping ({a.to_dict()} then {b.to_dict()})", {"field": where})
    return out


def _levels(value: Any, where: str, n_inputs: int) -> List[Dict[str, Any]]:
    if not isinstance(value, list) or len(value) != n_inputs:
        raise AudioError("INVALID_REQUEST", f"{where} must be an array with one entry per input ({n_inputs})", {"field": where})
    out = []
    for i, lv in enumerate(value):
        d = _obj(lv, f"{where}[{i}]", ("gain_db", "mute"), ())
        gain = _number(d.get("gain_db", 0.0), f"{where}[{i}].gain_db", -60.0, 60.0)
        mute = d.get("mute", False)
        if not isinstance(mute, bool):
            raise AudioError("INVALID_REQUEST", f"{where}[{i}].mute must be a boolean", {"field": where})
        out.append({"gain_db": gain, "mute": mute})
    return out


def validate_parameters(op_type: str, params: Any, n_inputs: int, where: str) -> Dict[str, Any]:
    """Validate and normalise parameters against the schema of op_type. Returns the effective parameters
    (defaults filled in), with every number as float and nothing unknown."""
    spec = OPERATION_TYPES[op_type]["parameters"]
    d = _obj(params if params is not None else {}, where, tuple(spec), tuple(k for k, v in spec.items() if v["required"]))
    out: Dict[str, Any] = {}
    for name, ps in spec.items():
        w = f"{where}.{name}"
        if name not in d:
            if "default" in ps:
                out[name] = ps["default"]
            continue
        v = d[name]
        t = ps["type"]
        if t == _NUM:
            out[name] = _number(v, w, ps.get("min"), ps.get("max"))
        elif t == _INT:
            iv = int(_number(v, w, ps.get("min"), ps.get("max"), integer=True))
            if "enum" in ps and iv not in ps["enum"]:
                raise AudioError("INVALID_SAMPLE_RATE" if name == "sample_rate" else "INVALID_REQUEST", f"{w} must be one of {ps['enum']}", {"field": w})
            out[name] = iv
        elif t == _STR:
            if not isinstance(v, str) or not v or len(v) > ps.get("max_length", 256) or any(ord(c) < 32 for c in v):
                raise AudioError("INVALID_REQUEST", f"{w} must be a short printable string", {"field": w})
            if "enum" in ps and v not in ps["enum"]:
                raise AudioError("UNSUPPORTED_OPERATION", f"{w}={v!r} is not implemented; supported: {ps['enum']}", {"field": w})
            out[name] = v
        elif t == _BOOL:
            if not isinstance(v, bool):
                raise AudioError("INVALID_REQUEST", f"{w} must be a boolean", {"field": w})
            out[name] = v
        elif t == _RANGES:
            out[name] = [r.to_dict() for r in _ranges(v, w)]
        elif t == _LEVELS:
            out[name] = _levels(v, w, n_inputs)
    if op_type == "TRIM" and out["end"] <= out["start"]:
        raise AudioError("INVALID_TIME_RANGE", f"{where}: end must be greater than start", {"field": where})
    if op_type == "MIX" and "levels" not in out:
        out["levels"] = [{"gain_db": 0.0, "mute": False} for _ in range(n_inputs)]
    return out


def parse_ref(ref: Any, where: str) -> Tuple[str, str]:
    if not isinstance(ref, str):
        raise AudioError("INVALID_REQUEST", f"{where} must be a reference string 'track:<id>' or 'op:<id>'", {"field": where})
    m = REF_RE.match(ref)
    if not m:
        raise AudioError("INVALID_REQUEST", f"{where}: bad reference {ref!r} (expected 'track:<id>' or 'op:<id>')", {"field": where})
    return m.group(1), m.group(2)


def parse_request(doc: Any) -> AudioRequest:
    """Validate a request document (schema audio-production/request@1) into typed objects."""
    if not isinstance(doc, dict):
        raise AudioError("INVALID_REQUEST", "request document must be a JSON object")
    _reject_forbidden(doc, "request")
    d = _obj(doc, "request", ("schema", "project", "options"), ("schema", "project"))
    if d["schema"] != REQUEST_SCHEMA_ID:
        raise AudioError("INVALID_REQUEST", f"unsupported request schema {d['schema']!r}; expected {REQUEST_SCHEMA_ID!r}", {"field": "schema"})
    options = _obj(d.get("options", {}), "options", ("reuse_intermediates", "timeout"), ())
    opts: Dict[str, Any] = {"reuse_intermediates": True, "timeout": None}
    if "reuse_intermediates" in options:
        if not isinstance(options["reuse_intermediates"], bool):
            raise AudioError("INVALID_REQUEST", "options.reuse_intermediates must be a boolean")
        opts["reuse_intermediates"] = options["reuse_intermediates"]
    if "timeout" in options and options["timeout"] is not None:
        opts["timeout"] = _number(options["timeout"], "options.timeout", 1.0, 86400.0)

    p = _obj(d["project"], "project", ("project_id", "sources", "tracks", "operations", "outputs"), ("project_id", "sources", "tracks", "outputs"))
    project_id = _id(p["project_id"], "project.project_id")

    # sources
    if not isinstance(p["sources"], list) or not p["sources"]:
        raise AudioError("INVALID_REQUEST", "project.sources must be a non-empty array")
    sources: List[AudioSource] = []
    for i, s in enumerate(p["sources"]):
        sd = _obj(s, f"project.sources[{i}]", ("source_id", "path"), ("source_id", "path"))
        sid = _id(sd["source_id"], f"project.sources[{i}].source_id")
        if any(x.source_id == sid for x in sources):
            raise AudioError("DEPENDENCY_ERROR", f"duplicate source_id {sid!r}", {"field": f"project.sources[{i}]"})
        if not isinstance(sd["path"], str) or not sd["path"]:
            raise AudioError("INVALID_REQUEST", f"project.sources[{i}].path must be a non-empty string")
        sources.append(AudioSource(sid, sd["path"]))
    source_ids = {s.source_id for s in sources}

    # tracks
    if not isinstance(p["tracks"], list) or not p["tracks"]:
        raise AudioError("INVALID_REQUEST", "project.tracks must be a non-empty array")
    tracks: List[AudioTrack] = []
    for i, t in enumerate(p["tracks"]):
        w = f"project.tracks[{i}]"
        td = _obj(t, w, ("track_id", "source_id", "label", "channel_layout", "range", "gain_db"), ("track_id", "source_id"))
        tid = _id(td["track_id"], f"{w}.track_id")
        if any(x.track_id == tid for x in tracks):
            raise AudioError("DEPENDENCY_ERROR", f"duplicate track_id {tid!r}", {"field": w})
        sid = _id(td["source_id"], f"{w}.source_id")
        if sid not in source_ids:
            raise AudioError("MISSING_INPUT", f"{w}: source {sid!r} is not declared", {"field": w, "source_id": sid})
        label = td.get("label")
        if label is not None and (not isinstance(label, str) or len(label) > 128 or any(ord(c) < 32 for c in label)):
            raise AudioError("INVALID_REQUEST", f"{w}.label must be a short printable string")
        layout = td.get("channel_layout")
        if layout is not None and layout not in CHANNEL_LAYOUTS:
            raise AudioError("INVALID_CHANNEL_LAYOUT", f"{w}.channel_layout {layout!r} is not supported; supported: {sorted(CHANNEL_LAYOUTS)}", {"field": w})
        rng = _time_range(td["range"], f"{w}.range") if td.get("range") is not None else None
        gain = _number(td.get("gain_db", 0.0), f"{w}.gain_db", -60.0, 60.0)
        tracks.append(AudioTrack(tid, sid, label, layout, rng, gain))
    track_ids = {t.track_id for t in tracks}

    # operations
    ops_raw = p.get("operations", [])
    if not isinstance(ops_raw, list):
        raise AudioError("INVALID_REQUEST", "project.operations must be an array")
    if len(ops_raw) > MAX_OPERATIONS:
        raise AudioError("INVALID_REQUEST", f"too many operations (max {MAX_OPERATIONS})")
    operations: List[AudioOperation] = []
    op_ids: set = set()
    for i, o in enumerate(ops_raw):
        w = f"project.operations[{i}]"
        od = _obj(o, w, ("op_id", "type", "inputs", "parameters"), ("op_id", "type", "inputs"))
        oid = _id(od["op_id"], f"{w}.op_id")
        if oid in op_ids:
            raise AudioError("DEPENDENCY_ERROR", f"duplicate op_id {oid!r}", {"field": w})
        op_ids.add(oid)
        typ = od["type"]
        if not isinstance(typ, str):
            raise AudioError("INVALID_REQUEST", f"{w}.type must be a string")
        if typ in UNSUPPORTED_OPERATIONS:
            raise AudioError("UNSUPPORTED_OPERATION", f"{w}: operation type {typ!r} is declared but not implemented: {UNSUPPORTED_OPERATIONS[typ]}",
                             {"field": w, "type": typ, "supported": sorted(OPERATION_TYPES)})
        if typ not in OPERATION_TYPES:
            raise AudioError("UNSUPPORTED_OPERATION", f"{w}: unknown operation type {typ!r}", {"field": w, "type": typ, "supported": sorted(OPERATION_TYPES)})
        inputs = od["inputs"]
        lo, hi = OPERATION_TYPES[typ]["inputs"]
        if not isinstance(inputs, list) or not (lo <= len(inputs) <= hi):
            raise AudioError("INVALID_REQUEST", f"{w}.inputs must be an array of {lo}..{hi} reference(s) for {typ}", {"field": w})
        for j, ref in enumerate(inputs):
            parse_ref(ref, f"{w}.inputs[{j}]")
        if len(set(inputs)) != len(inputs):
            raise AudioError("DEPENDENCY_ERROR", f"{w}.inputs contains a duplicate reference", {"field": w})
        params = validate_parameters(typ, od.get("parameters"), len(inputs), f"{w}.parameters")
        operations.append(AudioOperation(oid, typ, list(inputs), params))

    # reference existence (cycles / ordering are checked by graph.py)
    for op in operations:
        for ref in op.inputs:
            kind, ident = parse_ref(ref, "")
            if kind == "track" and ident not in track_ids or kind == "op" and ident not in op_ids:
                raise AudioError("MISSING_INPUT", f"operation {op.op_id!r} references unknown {kind} {ident!r}", {"op_id": op.op_id, "ref": ref})
            if kind == "op" and ident == op.op_id:
                raise AudioError("DEPENDENCY_ERROR", f"operation {op.op_id!r} references itself", {"op_id": op.op_id})

    # outputs
    if not isinstance(p["outputs"], list) or not p["outputs"]:
        raise AudioError("INVALID_REQUEST", "project.outputs must be a non-empty array")
    outputs: List[AudioOutput] = []
    for i, o in enumerate(p["outputs"]):
        w = f"project.outputs[{i}]"
        od = _obj(o, w, ("output_id", "operation", "path", "format", "overwrite", "expect"), ("output_id", "operation", "path", "format"))
        oid = _id(od["output_id"], f"{w}.output_id")
        if any(x.output_id == oid for x in outputs):
            raise AudioError("DEPENDENCY_ERROR", f"duplicate output_id {oid!r}", {"field": w})
        kind, ident = parse_ref(od["operation"], f"{w}.operation")
        if kind == "track" and ident not in track_ids or kind == "op" and ident not in op_ids:
            raise AudioError("MISSING_INPUT", f"{w}: references unknown {kind} {ident!r}", {"field": w, "ref": od["operation"]})
        if not isinstance(od["path"], str) or not od["path"]:
            raise AudioError("INVALID_REQUEST", f"{w}.path must be a non-empty string")
        fmt = od["format"]
        if fmt not in OUTPUT_FORMATS:
            raise AudioError("UNSUPPORTED_FORMAT", f"{w}.format {fmt!r} is not supported; supported: {sorted(OUTPUT_FORMATS)}", {"field": w, "format": fmt})
        if not od["path"].lower().endswith(OUTPUT_FORMATS[fmt]["extension"]):
            raise AudioError("UNSUPPORTED_FORMAT", f"{w}.path must end with {OUTPUT_FORMATS[fmt]['extension']!r} for format {fmt!r}", {"field": w})
        overwrite = od.get("overwrite", False)
        if not isinstance(overwrite, bool):
            raise AudioError("INVALID_REQUEST", f"{w}.overwrite must be a boolean")
        ex = _obj(od.get("expect", {}), f"{w}.expect", ("sample_rate", "channels", "channel_layout", "duration", "duration_tolerance"), ())
        expect: Dict[str, Any] = {}
        if "sample_rate" in ex:
            sr = int(_number(ex["sample_rate"], f"{w}.expect.sample_rate", integer=True))
            if sr not in SAMPLE_RATES:
                raise AudioError("INVALID_SAMPLE_RATE", f"{w}.expect.sample_rate must be one of {list(SAMPLE_RATES)}", {"field": w})
            expect["sample_rate"] = sr
        if "channels" in ex:
            expect["channels"] = int(_number(ex["channels"], f"{w}.expect.channels", 1, 8, integer=True))
        if "channel_layout" in ex:
            if ex["channel_layout"] not in CHANNEL_LAYOUTS:
                raise AudioError("INVALID_CHANNEL_LAYOUT", f"{w}.expect.channel_layout must be one of {sorted(CHANNEL_LAYOUTS)}", {"field": w})
            expect["channel_layout"] = ex["channel_layout"]
        if "duration" in ex:
            expect["duration"] = _number(ex["duration"], f"{w}.expect.duration", 0.0, MAX_DURATION)
            expect["duration_tolerance"] = _number(ex.get("duration_tolerance", 0.1), f"{w}.expect.duration_tolerance", 0.0, 3600.0)
        elif "duration_tolerance" in ex:
            raise AudioError("INVALID_REQUEST", f"{w}.expect.duration_tolerance requires expect.duration")
        outputs.append(AudioOutput(oid, od["operation"], od["path"], fmt, overwrite, expect))
    if len({o.path for o in outputs}) != len(outputs):
        raise AudioError("OUTPUT_ERROR", "two outputs share the same path", {"reason": "duplicate_output_path"})

    return AudioRequest(AudioProject(project_id, sources, tracks, operations, outputs), opts)
