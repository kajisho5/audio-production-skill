"""Machine-readable Skill / Capability / Tool contract (`audio-production skill --json`, alias `contract --json`).
Derived from the same tables the code runs on (model.OPERATION_TYPES, executor.TOOL_FOR, errors.ERROR_TABLE);
nothing is hand-maintained beside the implementation."""
from __future__ import annotations

from typing import Any, Dict, List

from . import CONTRACT_SCHEMA_VERSION, DOCTOR_SCHEMA_VERSION, PACKAGE_NAME, REQUEST_SCHEMA_VERSION, RESPONSE_SCHEMA_VERSION, SKILL_ID, VERSION
from .adapter import FLAGS_USED, SUPPORTED_CONTRACT_VERSION, SUPPORTED_MAX_EXCLUSIVE, SUPPORTED_MIN, TOOLS_USED
from .errors import ERROR_CODES, ERROR_TABLE, EXIT_CODES
from .executor import DURATION_TOLERANCE, TOOL_FOR, WORK_DIR_NAME
from .model import (CHANNEL_LAYOUTS, FORBIDDEN_KEYS, ID_RE, INTERMEDIATE_FORMAT, MAX_MIX_INPUTS, OPERATION_TYPES, OUTPUT_FORMATS, REF_RE,
                    REQUEST_SCHEMA_ID, SAMPLE_RATES, UNSUPPORTED_OPERATIONS)

CONTRACT_SCHEMA_ID = f"{SKILL_ID}/contract@{CONTRACT_SCHEMA_VERSION}"


def _param_schema(ps: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in ps.items() if k in ("type", "required", "min", "max", "enum", "default", "description", "max_length")}
    if ps["type"] == "ranges":
        out["type"] = "array"
        out["items"] = {"type": "object", "properties": {"start": {"type": "number"}, "end": {"type": "number"}}, "required": ["start", "end"], "additionalProperties": False}
    if ps["type"] == "levels":
        out["type"] = "array"
        out["items"] = {"type": "object", "properties": {"gain_db": {"type": "number", "min": -60, "max": 60}, "mute": {"type": "boolean"}}, "additionalProperties": False}
    return out


def operation_specs() -> List[Dict[str, Any]]:
    out = []
    for typ, spec in OPERATION_TYPES.items():
        tool, extra = TOOL_FOR[typ]
        out.append({"type": typ, "description": spec["description"], "inputs": {"min": spec["inputs"][0], "max": spec["inputs"][1]},
                    "parameters": {k: _param_schema(v) for k, v in spec["parameters"].items()},
                    "tool": f"ffmpeg-skill/{tool}", "required_capabilities": ["ffmpeg-skill", "ffmpeg", "ffprobe", "encoder:pcm_s16le", *extra],
                    "keeps_timeline": typ not in ("TRIM", "CUT", "SILENCE_REMOVE", "MIX"), "deterministic": "content_equivalent"})
    return out


def skill_contract() -> Dict[str, Any]:
    tools = [{"tool_id": f"{SKILL_ID}/run", "skill_id": SKILL_ID, "version": VERSION, "role": "execution",
              "description": "Execute a typed AudioProject (operation graph) and write validated audio artifacts with provenance",
              "inputs": ["request document (stdin)"], "input_type": REQUEST_SCHEMA_ID, "produces_output": True, "writes_media": True, "deterministic": True,
              "idempotency_hint": "content_equivalent; intermediates reused by deterministic operation id",
              "operations": sorted(OPERATION_TYPES), "supports": {"dry_run": True, "timeout": True, "cancel": True, "validate": True},
              "verification": "every artifact is probed (audio stream, duration, channels, sample rate, codec, sha256); NORMALIZE is re-measured",
              "provenance": "OBSERVED", "mutates_input": False, "delegates_to": [f"ffmpeg-skill/{t}" for t in TOOLS_USED]}]
    return {
        "schema": CONTRACT_SCHEMA_ID, "skill_id": SKILL_ID, "id": SKILL_ID, "name": PACKAGE_NAME, "package": PACKAGE_NAME, "version": VERSION,
        "kind": "execution", "role": "audio production (processing); not measurement, not decision",
        "description": "Deterministic audio production execution: gain, trim, cut, silence removal (explicit ranges), fades, EBU R128 loudness normalisation, "
                       "mix, mono/stereo/downmix, FFT noise reduction, format conversion; typed operation graph in, validated artifacts with provenance out. Not an AI agent.",
        "repository": "https://github.com/kajisho5/audio-production-skill",
        "not_provided": ["AI reasoning", "decisions", "production plans", "loudness or silence measurement for decisions (media-analysis-skill)", "speech recognition",
                         "video editing", "arbitrary ffmpeg filters", "shell execution", "network access"],
        "tools": tools,
        "operations": operation_specs(),
        "unsupported_operations": [{"type": t, "status": "not_implemented", "reason": r} for t, r in UNSUPPORTED_OPERATIONS.items()],
        "output_formats": {f: {"extension": s["extension"], "codec": s["codec"], "required_capability": s["capability"], "lossless": s["lossless"]} for f, s in OUTPUT_FORMATS.items()},
        "intermediate_format": {"format": INTERMEDIATE_FORMAT, "codec": OUTPUT_FORMATS[INTERMEDIATE_FORMAT]["codec"], "work_dir": f"<workspace>/{WORK_DIR_NAME}/<project_id>/"},
        "channel_layouts": sorted(CHANNEL_LAYOUTS), "sample_rates": list(SAMPLE_RATES), "max_mix_inputs": MAX_MIX_INPUTS,
        "timeline": {"unit": "seconds (float)", "ranges": "half-open [start, end)", "mapping": "every artifact carries segments: timeline range <- source_id + source range",
                     "precision": f"ffmpeg-skill/cut lands on packet boundaries; artifact duration is validated within {DURATION_TOLERANCE}s"},
        "loudness": {"standard": "EBU R128 / ITU-R BS.1770 via ffmpeg loudnorm (two-pass, linear)", "parameters": ["target_lufs", "true_peak_db", "loudness_range_lu", "tolerance_lufs", "sample_rate", "profile"],
                     "defaults": "none for target / true peak: the caller or profile decides", "measurement_for_decisions": "media-analysis-skill (loudness kind); this skill only verifies its own output"},
        "execution": {"mode": "local", "canonical_invocation": ["audio-production", "run", "-", "--json"], "stdin": REQUEST_SCHEMA_ID,
                      "stdout": f"exactly one {SKILL_ID}/response@{RESPONSE_SCHEMA_VERSION} document", "stderr": "diagnostics only",
                      "executables": ["python3 <ffmpeg-skill>/scripts/{probe,audio,cut,loudness}.py (argv lists)"], "executable_resolution": "ffmpeg-skill directory: --ffmpeg-skill, AUDIO_PRODUCTION_FFMPEG_SKILL_DIR, VIDEO_AGENT_FFMPEG_SKILL_DIR, ~/.claude/skills/ffmpeg-skill, ./vendor/ffmpeg-skill, ../ffmpeg-skill; ffmpeg/ffprobe: PATH lookup by ffmpeg-skill",
                      "shell": False, "arbitrary_executables": False, "arbitrary_filters": False, "network": False, "input_mutation": False, "ai": False},
        "ffmpeg_skill": {"contract_version": SUPPORTED_CONTRACT_VERSION, "version_window": {"min": ".".join(map(str, SUPPORTED_MIN)), "max_exclusive": ".".join(map(str, SUPPORTED_MAX_EXCLUSIVE))},
                         "tools_used": list(TOOLS_USED), "flags_used": {k: list(v) for k, v in FLAGS_USED.items()}},
        "request": {"schema": REQUEST_SCHEMA_ID, "id_pattern": ID_RE.pattern, "reference_pattern": REF_RE.pattern, "forbidden_fields": sorted(FORBIDDEN_KEYS),
                    "shape": {"schema": REQUEST_SCHEMA_ID, "project": {"project_id": "id", "sources": [{"source_id": "id", "path": "file"}],
                              "tracks": [{"track_id": "id", "source_id": "id", "label?": "str", "channel_layout?": "mono|stereo|5.1|7.1", "range?": {"start": 0, "end": 1}, "gain_db?": 0}],
                              "operations": [{"op_id": "id", "type": "GAIN|...", "inputs": ["track:<id>|op:<id>"], "parameters": {}}],
                              "outputs": [{"output_id": "id", "operation": "op:<id>|track:<id>", "path": "file", "format": "wav|flac|mp3|m4a|aac|ogg|opus", "overwrite?": False,
                                           "expect?": {"sample_rate?": 48000, "channels?": 2, "channel_layout?": "stereo", "duration?": 0, "duration_tolerance?": 0.1}}]},
                              "options?": {"reuse_intermediates?": True, "timeout?": 600}}},
        "response": {"schema": f"{SKILL_ID}/response@{RESPONSE_SCHEMA_VERSION}", "success": {"ok": True, "status": "ok", "dry_run": "bool", "plan": "...", "results": "[OperationResult]", "outputs": "[output artifact + provenance]"},
                     "failure": {"ok": False, "status": "error|cancelled", "error": {"code": "one of errors.codes", "message": "str", "retryable": "bool", "details": {}}}},
        "provenance": {"per_operation": ["operation_id", "type", "tool", "tool_versions", "parameters", "input_hashes", "output_hash", "segments", "status", "measurements", "tool_commands_observed"],
                       "per_output": ["skill", "skill_version", "tool", "tool_versions", "output_hash", "operations (chain)", "sources (sha256)"],
                       "identity": "sha256 over canonical JSON of {type, parameters, input identities, tool versions}; sources by file sha256; no timestamps, no UUIDs"},
        "schema_versions": {"contract": str(CONTRACT_SCHEMA_VERSION), "request": str(REQUEST_SCHEMA_VERSION), "response": str(RESPONSE_SCHEMA_VERSION), "doctor": str(DOCTOR_SCHEMA_VERSION)},
        "errors": {"codes": list(ERROR_CODES), "exit_codes": dict(EXIT_CODES), "retryable": {c: ERROR_TABLE[c][1] for c in ERROR_CODES}, "success_exit_code": 0},
    }
