"""Contract tests: the printed contract matches the implementation, doctor is honest, stdout is one document."""
import json

from audio_production import VERSION
from audio_production.contract import skill_contract
from audio_production.doctor import capability_status, doctor_report
from audio_production.errors import ERROR_CODES, EXIT_CODES
from audio_production.executor import TOOL_FOR
from audio_production.model import OPERATION_TYPES, OUTPUT_FORMATS, UNSUPPORTED_OPERATIONS
from conftest import one_json, run_cli


def test_contract_matches_implementation():
    c = skill_contract()
    assert c["schema"] == "audio-production/contract@1" and c["skill_id"] == "audio-production" and c["version"] == VERSION
    assert {o["type"] for o in c["operations"]} == set(OPERATION_TYPES)
    assert {o["type"] for o in c["unsupported_operations"]} == set(UNSUPPORTED_OPERATIONS)
    assert not ({o["type"] for o in c["operations"]} & set(UNSUPPORTED_OPERATIONS))
    for o in c["operations"]:
        assert o["tool"] == f"ffmpeg-skill/{TOOL_FOR[o['type']][0]}"
        for name, ps in o["parameters"].items():
            assert "type" in ps and "required" in ps and "description" in ps, (o["type"], name)
    assert set(c["output_formats"]) == set(OUTPUT_FORMATS)
    assert c["errors"]["codes"] == list(ERROR_CODES) and c["errors"]["exit_codes"] == EXIT_CODES
    assert c["execution"]["shell"] is False and c["execution"]["arbitrary_filters"] is False and c["execution"]["ai"] is False
    assert c["loudness"]["defaults"].startswith("none")
    text = json.dumps(c, sort_keys=True)
    assert text == json.dumps(skill_contract(), sort_keys=True)     # deterministic
    assert "conference" not in text.lower() and "speaker" not in text.lower() and "lecture" not in text.lower()   # generic core


def test_contract_and_skill_cli_are_identical():
    c1, o1, _ = run_cli(["skill", "--json"])
    c2, o2, _ = run_cli(["contract", "--json"])
    assert c1 == c2 == 0 and one_json(o1) == one_json(o2)


def test_doctor_reports_supported_unsupported_unknown(skill_dir):
    d = doctor_report(str(skill_dir))
    assert d["schema"] == "audio-production/doctor@1" and d["status"] in ("ok", "degraded") and d["secrets_shown"] is False
    assert d["checks"]["ffmpeg_skill"]["status"] == "ok" and d["checks"]["ffmpeg"]["version"]
    ops = d["checks"]["operations"]
    assert set(ops) == set(OPERATION_TYPES)
    assert all(o["status"] in ("supported", "unsupported", "unknown") for o in ops.values())
    # FFmpeg >= 8.0 defeats ffmpeg-skill's filter parser: filters are then unknown, never unsupported
    assert ops["NORMALIZE"]["status"] in ("supported", "unknown") and ops["TRIM"]["status"] == "supported"
    assert d["checks"]["filter_detection"]["status"] in ("ok", "unknown")
    assert d["checks"]["unsupported_operations"] == UNSUPPORTED_OPERATIONS
    caps = d["checks"]["capabilities"]
    assert caps["ffmpeg"] == "supported" and caps["filter:loudnorm"] != "unsupported"
    assert all(v in ("supported", "unsupported", "unknown") for v in caps.values())
    code, out, _ = run_cli(["doctor", "--json", "--ffmpeg-skill", str(skill_dir)])
    assert code == 0 and one_json(out)["status"] == d["status"]


def test_doctor_without_ffmpeg_skill_is_a_failure_not_a_guess(tmp_path, monkeypatch):
    monkeypatch.delenv("AUDIO_PRODUCTION_FFMPEG_SKILL_DIR", raising=False)
    monkeypatch.delenv("VIDEO_AGENT_FFMPEG_SKILL_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    d = doctor_report(str(tmp_path / "nowhere"))
    assert d["status"] == "fail" and d["checks"]["ffmpeg_skill"]["status"] == "missing"
    assert all(o["status"] == "unsupported" for o in d["checks"]["operations"].values())
    assert d["checks"]["ffmpeg"]["status"] == "unknown"


def test_capability_status_table():
    s = capability_status(None, {})
    assert s["ffmpeg"] == "unsupported" and s["filter:loudnorm"] == "unsupported"

    class Info:
        supported = True
    s = capability_status(Info(), {"ffmpeg": "6.1", "ffprobe": "6.1", "available": ["filter:loudnorm", "encoder:aac"], "missing_optional": ["encoder:libopus"]})
    assert s["filter:loudnorm"] == "supported" and s["encoder:libopus"] == "unsupported" and s["filter:volume"] == "unknown" and s["encoder:pcm_s16le"] == "supported"
    s = capability_status(Info(), {"ffmpeg": "6.1", "ffprobe": "6.1", "available": ["encoder:aac"], "missing": ["filter:loudnorm"], "missing_optional": ["filter:afftdn", "encoder:libopus"]})
    assert s["filter:loudnorm"] == "unknown"     # zero filters detected: parser failure, not absence
    assert s["filter:afftdn"] == "unknown" and s["encoder:libopus"] == "unsupported"
    s = capability_status(Info(), {"ffmpeg": "6.1", "ffprobe": "6.1", "available": ["filter:silencedetect"], "missing": ["filter:loudnorm"], "missing_optional": []})
    assert s["filter:loudnorm"] == "unsupported"                                                     # some filters detected: a missing one is really missing
