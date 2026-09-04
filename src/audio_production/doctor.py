"""Environment diagnosis against the contract. Reports only what was detected; every capability carries one of
supported | unsupported | unknown, never a guess. Detection goes through ffmpeg-skill (`_contract.py doctor --json`
and `_contract.py --json --static`): this skill runs no ffmpeg of its own."""
from __future__ import annotations

import json
import platform
import sys
from typing import Any, Dict, List, Optional

from . import DOCTOR_SCHEMA_VERSION, SKILL_ID, VERSION
from .adapter import FfmpegSkill
from .errors import AudioError
from .executor import TOOL_FOR
from .model import CHANNEL_LAYOUTS, OPERATION_TYPES, OUTPUT_FORMATS, SAMPLE_RATES, UNSUPPORTED_OPERATIONS
from .security import PathPolicy

DOCTOR_SCHEMA_ID = f"{SKILL_ID}/doctor@{DOCTOR_SCHEMA_VERSION}"
# ffmpeg core filters this skill relies on through ffmpeg-skill; ffmpeg-skill's doctor does not probe them, so
# they are reported as unknown unless ffmpeg itself is missing (then unsupported).
CORE_FILTERS = ("filter:volume", "filter:afade", "filter:amix", "filter:pan", "filter:aformat")


def ffmpeg_skill_doctor(skill: FfmpegSkill, timeout: float = 120.0) -> Dict[str, Any]:
    argv = [sys.executable, str(skill.directory / "scripts" / "_contract.py"), "doctor", "--json"]
    code, out, err, _ = skill._popen(argv, timeout)
    try:
        doc = json.loads(out or "{}")
    except ValueError:
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    doc["_exit_code"] = code
    return doc


def capability_status(skill_info: Optional[Any], ffdoc: Dict[str, Any]) -> Dict[str, str]:
    """capability name -> supported | unsupported | unknown."""
    status: Dict[str, str] = {}
    have_skill = skill_info is not None and skill_info.supported
    status["ffmpeg-skill"] = "supported" if have_skill else "unsupported"
    ff_ok = bool(ffdoc.get("ffmpeg")) and have_skill
    fp_ok = bool(ffdoc.get("ffprobe")) and have_skill
    status["ffmpeg"] = "supported" if ff_ok else "unsupported"
    status["ffprobe"] = "supported" if fp_ok else "unsupported"
    available = set(ffdoc.get("available") or [])
    missing = set(ffdoc.get("missing") or []) | set(ffdoc.get("missing_optional") or [])
    for cap in sorted({c for _, extra in TOOL_FOR.values() for c in extra} | {f["capability"] for f in OUTPUT_FORMATS.values()} | set(CORE_FILTERS)):
        if not ff_ok:
            status[cap] = "unsupported"
        elif cap in available:
            status[cap] = "supported"
        elif cap in missing:
            status[cap] = "unsupported"
        elif cap == "encoder:pcm_s16le":
            status[cap] = "supported" if ff_ok else "unsupported"   # native encoder of every ffmpeg build; needed for probe-verified WAV intermediates
        else:
            status[cap] = "unknown"
    return status


def doctor_report(ffmpeg_skill_dir: Optional[str] = None, workspace: Optional[str] = None, allowed_input: Optional[List[str]] = None) -> Dict[str, Any]:
    checks: Dict[str, Any] = {}
    problems: List[str] = []
    checks["python"] = {"status": "ok", "version": platform.python_version(), "implementation": platform.python_implementation(), "platform": platform.system()}
    skill: Optional[FfmpegSkill] = None
    info = None
    ffdoc: Dict[str, Any] = {}
    try:
        skill = FfmpegSkill.locate(ffmpeg_skill_dir)
        info = skill.info()
        checks["ffmpeg_skill"] = {"status": "ok" if info.supported else "fail", "directory": str(info.directory), "version": info.version,
                                  "contract_version": info.contract_version, "tools_used": list(sorted(t for t in info.tools if t in ("probe", "audio", "cut", "loudness"))),
                                  "problems": info.problems}
        problems += ["ffmpeg-skill: " + p for p in info.problems]
        ffdoc = ffmpeg_skill_doctor(skill)
        checks["ffmpeg"] = {"status": "ok" if ffdoc.get("ffmpeg") else "missing", "version": ffdoc.get("ffmpeg"), "detected_by": "ffmpeg-skill doctor"}
        checks["ffprobe"] = {"status": "ok" if ffdoc.get("ffprobe") else "missing", "version": ffdoc.get("ffprobe"), "detected_by": "ffmpeg-skill doctor"}
        if not ffdoc.get("ffmpeg") or not ffdoc.get("ffprobe"):
            problems.append("ffmpeg / ffprobe not detected by ffmpeg-skill doctor")
    except AudioError as e:
        checks["ffmpeg_skill"] = {"status": "missing", "detail": e.message, "tried": e.details.get("tried")}
        checks["ffmpeg"] = {"status": "unknown", "detail": "not checked: ffmpeg-skill missing"}
        checks["ffprobe"] = {"status": "unknown", "detail": "not checked: ffmpeg-skill missing"}
        problems.append("ffmpeg-skill: " + e.message)
    caps = capability_status(info, ffdoc)
    checks["capabilities"] = caps
    ops: Dict[str, Any] = {}
    for typ, spec in OPERATION_TYPES.items():
        tool, extra = TOOL_FOR[typ]
        need = ["ffmpeg-skill", "ffmpeg", "ffprobe", "encoder:pcm_s16le", *extra]
        st = "unsupported" if any(caps.get(c) == "unsupported" for c in need) else ("unknown" if any(caps.get(c) == "unknown" for c in need) else "supported")
        ops[typ] = {"status": st, "tool": f"ffmpeg-skill/{tool}", "required_capabilities": need, "missing": [c for c in need if caps.get(c) == "unsupported"],
                    "unknown": [c for c in need if caps.get(c) == "unknown"]}
    checks["operations"] = ops
    checks["unsupported_operations"] = dict(UNSUPPORTED_OPERATIONS)
    checks["output_formats"] = {f: {"status": caps.get(spec["capability"], "unknown"), "capability": spec["capability"], "codec": spec["codec"]} for f, spec in OUTPUT_FORMATS.items()}
    checks["channel_layouts"] = {"declared": sorted(CHANNEL_LAYOUTS), "operations": {"MONO": "2ch -> 1ch", "STEREO": "1ch/2ch -> 2ch", "DOWNMIX": "5.1/7.1 -> 2ch"},
                                 "detected": "not detectable through ffmpeg-skill; verified per run by probing the output"}
    checks["sample_rates"] = {"accepted": list(SAMPLE_RATES), "conversion": "NORMALIZE.sample_rate only (ffmpeg-skill/loudness --sample-rate)", "detected": "verified per run by probing the output"}
    try:
        policy = PathPolicy(workspace, allowed_input)
        checks["path_policy"] = {"status": "ok", "workspace": str(policy.workspace), "allowed_input_roots": [str(r) for r in policy.allowed_input_roots] if policy.allowed_input_roots else None,
                                 "work_dir": str(policy.workspace / ".audio-production"), "input_rule": "regular files (symlinks resolved)" + (" under allowed roots" if policy.allowed_input_roots else ""),
                                 "output_rule": "inside the workspace, never an input, never an existing file unless overwrite"}
    except AudioError as e:
        checks["path_policy"] = {"status": "fail", "detail": e.message}
        problems.append("path policy: " + e.message)
    unsupported = sorted(t for t, o in ops.items() if o["status"] == "unsupported")
    status = "fail" if problems else ("degraded" if unsupported else "ok")
    return {"schema": DOCTOR_SCHEMA_ID, "skill": {"id": SKILL_ID, "version": VERSION}, "status": status, "checks": checks,
            "unavailable_operations": unsupported, "problems": problems, "secrets_shown": False}


def runtime_context(ffmpeg_skill_dir: Optional[str], timeout: float) -> Any:
    """What the executor needs: the located skill, tool versions, and capability statuses. Raises AudioError."""
    skill = FfmpegSkill.locate(ffmpeg_skill_dir, timeout)
    info = skill.info()
    if not info.supported:
        raise AudioError("TOOL_ERROR", "ffmpeg-skill at " + str(info.directory) + " is not usable: " + "; ".join(info.problems), {"reason": "ffmpeg_skill_incompatible", "problems": info.problems}, retryable=False)
    ffdoc = ffmpeg_skill_doctor(skill)
    versions = {"ffmpeg-skill": info.version, "ffmpeg-skill_contract": info.contract_version, "ffmpeg": ffdoc.get("ffmpeg") or "unknown", "ffprobe": ffdoc.get("ffprobe") or "unknown"}
    return skill, versions, capability_status(info, ffdoc)
