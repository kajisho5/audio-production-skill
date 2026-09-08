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


# ---- contract --check: implementation consistency, pinned snapshot, drift classification
import copy
import os
from pathlib import Path

from audio_production.contract_check import PINNED_BLOCKS, contract_drift, run_check, verify_implementation

PINNED = Path(__file__).resolve().parent / "contract" / "contract.json"


def test_live_contract_is_consistent_with_implementation_and_docs():
    assert verify_implementation() == []
    rep = run_check()
    assert rep["status"] == "ok" and rep["exit_code"] == 0 and rep["compared_with_saved"] is False


def test_pinned_contract_matches_live():
    saved = json.load(open(PINNED, encoding="utf-8"))
    rep = run_check(saved)
    assert rep["status"] == "ok", rep["drift"]
    assert rep["drift"] == {"breaking": [], "additive": []}


def test_drift_classification():
    live = skill_contract()
    # breaking: a pinned block, an operation parameter, a removed operation, the version, a removed key anywhere
    m = copy.deepcopy(live); m["version"] = live["version"] + "-test"        # any value different from the live version
    assert any(x.startswith("version") for x in contract_drift(m)["breaking"])
    m = copy.deepcopy(live); m["operations"][0]["parameters"]["extra"] = {"type": "number", "required": False, "description": "x"}
    assert any(x.startswith("operations/") for x in contract_drift(m)["breaking"])
    m = copy.deepcopy(live); m["operations"] = m["operations"][1:]
    d = contract_drift(m)["breaking"]
    assert any("added" in x for x in d)                      # live has an operation the saved copy lacks: agents must re-pin
    m = copy.deepcopy(live); m["errors"]["codes"] = m["errors"]["codes"][:-1]
    assert any(x.startswith("errors") for x in contract_drift(m)["breaking"])
    m = copy.deepcopy(live); m["only_in_saved"] = 1
    assert contract_drift(m)["breaking"] == ["only_in_saved: removed"]
    # additive: a key the live contract gained outside the pinned blocks
    m = copy.deepcopy(live); del m["loudness"]
    d = contract_drift(m)
    assert d["breaking"] == [] and d["additive"] == ["loudness: added"]
    assert run_check(m)["status"] == "additive" and run_check(m)["exit_code"] == 0
    m = copy.deepcopy(live); m["provides"] = m["provides"][:-1]
    assert any(x.startswith("provides") for x in contract_drift(m)["breaking"])
    assert "provides" in PINNED_BLOCKS and "operations" in PINNED_BLOCKS


def test_verify_implementation_detects_inconsistency():
    live = skill_contract()
    m = copy.deepcopy(live); m["execution"]["shell"] = True
    assert any("execution.shell" in x for x in verify_implementation(m))
    m = copy.deepcopy(live); m["operations"][0]["tool"] = "ffmpeg-skill/render"
    assert any("tool" in x for x in verify_implementation(m))
    m = copy.deepcopy(live); m["provides"] = m["provides"][:-1]
    assert any("provides" in x for x in verify_implementation(m))
    m = copy.deepcopy(live); m["tools"] = []
    assert any("tools" in x for x in verify_implementation(m))
    assert run_check(m)["status"] == "breaking"      # run_check verifies the live contract; a mutated saved copy is drift


def test_contract_check_cli_exit_codes(tmp_path):
    code, out, _ = run_cli(["contract", "--check", str(PINNED), "--json"])
    d = one_json(out)
    assert code == 0 and d["status"] == "ok" and d["schema"] == "audio-production/contract-check@1"
    code, out, _ = run_cli(["skill", "--check", "--json"])
    assert code == 0 and one_json(out)["status"] == "ok"
    stale = json.load(open(PINNED, encoding="utf-8"))
    stale["version"] = "0.0.9"
    (tmp_path / "stale.json").write_text(json.dumps(stale), encoding="utf-8")
    code, out, _ = run_cli(["contract", "--check", str(tmp_path / "stale.json"), "--json"])
    d = one_json(out)
    assert code == 1 and d["status"] == "breaking" and d["drift"]["breaking"]
    code, out, _ = run_cli(["contract", "--check", "-", "--json"], "{not json")
    assert code == 1 and one_json(out)["status"] == "fail"
    code, out, _ = run_cli(["contract", "--check", str(PINNED)])
    assert code == 0 and out.strip() == "contract check: ok"
    assert os.path.getsize(PINNED) > 1000
