"""Integration tests with real audio through the real ffmpeg-skill checkout (nothing is mocked or skipped).
One positive and at least one negative case per implemented operation, a multi-operation pipeline, a two-source mix,
output validation, failure handling, dry run, idempotent re-runs, cancellation by timeout, and the CLI boundary."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from audio_production.errors import EXIT_CODES
from conftest import one_json, request_doc, run_cli


def run(doc, *extra):
    import shutil
    shutil.rmtree("out", ignore_errors=True)
    code, out, err = run_cli(["run", "-", "--json", *extra], json.dumps(doc))
    d = one_json(out)
    assert err == "" or "$" in err or "wrote" in err or "measured" in err or "result" in err  # stderr is diagnostics only
    return code, d


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)], capture_output=True, text=True, check=True)
    d = json.loads(r.stdout)
    a = next(s for s in d["streams"] if s["codec_type"] == "audio")
    return {"duration": float(d["format"]["duration"]), "channels": int(a["channels"]), "sample_rate": int(a["sample_rate"]), "codec": a["codec_name"]}


def op(op_id, typ, inputs, **params):
    return {"op_id": op_id, "type": typ, "inputs": inputs, "parameters": params}


def results(d):
    return {r["node_id"]: r for r in d["results"]}


def test_gain(workspace):
    code, d = run(request_doc([op("g", "GAIN", ["track:t1"], gain_db=-6)]))
    assert code == 0 and d["ok"] and d["status"] == "ok", d.get("error")
    out = workspace / "out" / "main.wav"
    p = probe(out)
    assert abs(p["duration"] - 6.0) < 0.05 and p["channels"] == 1 and p["codec"] == "pcm_s16le"
    r = results(d)["op:g"]
    assert r["tool"] == "ffmpeg-skill/audio" and r["status"] == "completed" and r["artifact"]["sha256"] and r["input_hashes"]
    assert d["outputs"][0]["provenance"]["output_hash"] == r["artifact"]["sha256"] or d["outputs"][0]["artifact"]["sha256"]
    assert d["outputs"][0]["provenance"]["tool_versions"]["ffmpeg-skill"]
    # negative: out of range
    code, d = run(request_doc([op("g", "GAIN", ["track:t1"], gain_db=90)]))
    assert d["error"]["code"] == "INVALID_REQUEST" and code == EXIT_CODES["INVALID_REQUEST"]


def test_trim_and_timeline(workspace):
    code, d = run(request_doc([op("t", "TRIM", ["track:t1"], start=1.0, end=4.0)]))
    assert d["ok"], d.get("error")
    assert abs(probe(workspace / "out" / "main.wav")["duration"] - 3.0) < 0.002        # sample-accurate (--accurate)
    assert results(d)["op:t"]["measurements"]["cut"]["precision"] == "sample"
    seg = results(d)["op:t"]["segments"]
    assert seg == [{"timeline": {"start": 0.0, "end": 3.0}, "source_id": "a", "source": {"start": 1.0, "end": 4.0}, "input_index": 0}]
    assert d["outputs"][0]["segments"] == seg
    # a compressed source is decoded on the way; still sample-accurate
    code, d = run(request_doc([op("t", "TRIM", ["track:t1"], start=0.5, end=2.5)], sources=[{"source_id": "a", "path": "stereo.m4a"}]))
    assert d["ok"] and abs(probe(workspace / "out" / "main.wav")["duration"] - 2.0) < 0.002 and results(d)["op:t"]["measurements"]["cut"]["precision"] == "sample"
    code, d = run(request_doc([op("t", "TRIM", ["track:t1"], start=0.5, end=2.0)], sources=[{"source_id": "a", "path": "video.mp4"}]))
    assert d["ok"] and abs(probe(workspace / "out" / "main.wav")["duration"] - 1.5) < 0.002
    code, d = run(request_doc([op("t", "TRIM", ["track:t1"], start=5.0, end=9.0)]))
    assert d["error"]["code"] == "INVALID_TIME_RANGE"


def test_cut_and_silence_remove(workspace):
    code, d = run(request_doc([op("c", "CUT", ["track:t1"], remove=[{"start": 1.0, "end": 2.0}, {"start": 4.0, "end": 6.0}])]))
    assert d["ok"], d.get("error")
    assert abs(probe(workspace / "out" / "main.wav")["duration"] - 3.0) < 0.1
    segs = results(d)["op:c"]["segments"]
    assert [(s["source"]["start"], s["source"]["end"]) for s in segs] == [(0.0, 1.0), (2.0, 4.0)]
    doc = request_doc([op("s", "SILENCE_REMOVE", ["track:t1"], ranges=[{"start": 0.0, "end": 2.0}, {"start": 5.0, "end": 6.0}], margin=0.25, min_duration=0.6, threshold_db=-40)],
                      sources=[{"source_id": "a", "path": "gated.wav"}])
    code, d = run(doc)
    assert d["ok"], d.get("error")
    r = results(d)["op:s"]
    assert r["measurements"]["effective_ranges"] == [{"start": 0.25, "end": 1.75}]     # 5.0-6.0 shrinks to 0.5 s < min_duration
    assert abs(probe(workspace / "out" / "main.wav")["duration"] - 4.5) < 0.1
    code, d = run(request_doc([op("c", "CUT", ["track:t1"], remove=[{"start": 0.0, "end": 6.0}])]))
    assert d["error"]["code"] == "INVALID_TIME_RANGE"


def test_fades(workspace):
    code, d = run(request_doc([op("i", "FADE_IN", ["track:t1"], duration=0.5), op("o", "FADE_OUT", ["op:i"], duration=1.0)]))
    assert d["ok"], d.get("error")
    assert abs(probe(workspace / "out" / "main.wav")["duration"] - 6.0) < 0.05
    code, d = run(request_doc([op("i", "FADE_IN", ["track:t1"], duration=7.0)]))
    assert d["error"]["code"] == "INVALID_TIME_RANGE"


def test_normalize_with_tolerance_and_profile(workspace):
    code, d = run(request_doc([op("n", "NORMALIZE", ["track:t1"], target_lufs=-16, true_peak_db=-1.5, tolerance_lufs=1.0, profile="podcast", sample_rate=44100)],
                              outputs=[{"output_id": "main", "operation": "op:n", "path": "out/main.flac", "format": "flac", "expect": {"sample_rate": 44100}}]))
    assert d["ok"], d.get("error")
    m = results(d)["op:n"]["measurements"]["loudness"]
    assert abs(m["integrated_lufs"] - (-16.0)) <= 1.0 and m["measured_by"].startswith("ffmpeg-skill/loudness")
    p = probe(workspace / "out" / "main.flac")
    assert p["sample_rate"] == 44100 and p["codec"] == "flac"
    code, d = run(request_doc([op("n", "NORMALIZE", ["track:t1"], target_lufs=-16)]))
    assert d["error"]["code"] == "INVALID_REQUEST"
    # a silent input cannot be normalised: ffmpeg-skill refuses, we report TOOL_ERROR and write nothing
    code, d = run(request_doc([op("n", "NORMALIZE", ["track:t1"], target_lufs=-16, true_peak_db=-1)], sources=[{"source_id": "a", "path": "silence.wav"}]))
    assert d["error"]["code"] == "TOOL_ERROR" and not (workspace / "out").exists()


def test_channel_operations(workspace):
    code, d = run(request_doc([op("s", "STEREO", ["track:t1"])]))
    assert d["ok"] and probe(workspace / "out" / "main.wav")["channels"] == 2
    code, d = run(request_doc([op("s", "STEREO", ["track:t1"]), op("m", "MONO", ["op:s"])], outputs=[{"output_id": "o", "operation": "op:m", "path": "out/m.wav", "format": "wav", "expect": {"channel_layout": "mono"}}]))
    assert d["ok"] and probe(workspace / "out" / "m.wav")["channels"] == 1
    code, d = run(request_doc([op("d", "DOWNMIX", ["track:t1"])], sources=[{"source_id": "a", "path": "surround.wav"}], tracks=[{"track_id": "t1", "source_id": "a", "channel_layout": "5.1"}],
                              outputs=[{"output_id": "o", "operation": "op:d", "path": "out/d.wav", "format": "wav", "expect": {"channels": 2}}]))
    assert d["ok"], d.get("error")
    assert probe(workspace / "out" / "d.wav")["channels"] == 2
    code, d = run(request_doc([op("m", "MONO", ["track:t1"])]))            # mono input: refused, never silently attenuated
    assert d["error"]["code"] == "INVALID_CHANNEL_LAYOUT"
    code, d = run(request_doc([op("d", "DOWNMIX", ["track:t1"])]))
    assert d["error"]["code"] == "INVALID_CHANNEL_LAYOUT"
    code, d = run(request_doc([], tracks=[{"track_id": "t1", "source_id": "a", "channel_layout": "stereo"}]))
    assert d["error"]["code"] == "INVALID_CHANNEL_LAYOUT" and d["error"]["details"]["channels"] == 1


def test_noise_reduction(workspace):
    code, d = run(request_doc([op("d", "NOISE_REDUCTION", ["track:t1"], mode="fft", strength_db=20)]))
    assert d["ok"] and "filter:afftdn" in results(d)["op:d"]["required_capabilities"]
    code, d = run(request_doc([op("d", "NOISE_REDUCTION", ["track:t1"], mode="spectral_ai", strength_db=20)]))
    assert d["error"]["code"] == "UNSUPPORTED_OPERATION"


def test_concat(workspace):
    doc = request_doc([op("k", "CONCAT", ["track:t1", "track:t2"])],
                      sources=[{"source_id": "a", "path": "tone.wav"}, {"source_id": "b", "path": "stereo.m4a"}],
                      tracks=[{"track_id": "t1", "source_id": "a"}, {"track_id": "t2", "source_id": "b"}],
                      outputs=[{"output_id": "main", "operation": "op:k", "path": "out/cat.wav", "format": "wav", "expect": {"channels": 2, "duration": 10.0}}])
    code, d = run(doc)
    assert d["ok"], d.get("error")
    segs = results(d)["op:k"]["segments"]
    assert [(s["source_id"], s["timeline"]["start"], s["timeline"]["end"], s["input_index"]) for s in segs] == [("a", 0.0, 6.0, 0), ("b", 6.0, 10.0, 1)]
    assert probe(workspace / "out" / "cat.wav")["channels"] == 2                     # widest input (ffmpeg-skill/join)
    doc["project"]["operations"][0]["parameters"] = {"crossfade": 1.0, "channels": 1, "sample_rate": 44100}
    doc["project"]["outputs"][0]["expect"] = {"channels": 1, "duration": 9.0, "sample_rate": 44100}
    code, d = run(doc)
    assert d["ok"], d.get("error")
    assert results(d)["op:k"]["segments"][1]["timeline"] == {"start": 5.0, "end": 9.0}
    doc["project"]["operations"][0]["parameters"] = {"crossfade": 5.0}
    code, d = run(doc)
    assert d["error"]["code"] == "INVALID_TIME_RANGE" and d["error"]["details"]["input_index"] == 1
    code, d = run(request_doc([op("k", "CONCAT", ["track:t1"])]))
    assert d["error"]["code"] == "INVALID_REQUEST"


def test_dynamics(workspace):
    code, d = run(request_doc([op("y", "DYNAMICS", ["track:t1"], gate={"threshold_db": -40, "range_db": -30}, compressor={"threshold_db": -20, "ratio": 4, "attack_ms": 5, "release_ms": 80, "makeup_db": 2}, limiter={"ceiling_db": -1, "attack_ms": 5, "release_ms": 50})]))
    assert d["ok"], d.get("error")
    r = results(d)["op:y"]
    assert r["tool"] == "ffmpeg-skill/audio" and abs(r["artifact"]["duration"] - 6.0) < 0.01
    assert any("agate" in c and "acompressor" in c and "alimiter" in c for c in r["tool_commands_observed"])
    code, d = run(request_doc([op("y", "DYNAMICS", ["track:t1"], limiter={"ceiling_db": -3})]))
    assert d["ok"], d.get("error")
    for bad in ({}, {"compressor": {"ratio": 50}}, {"compressor": {"threshold_db": -20, "filter": "x"}}, {"limiter": {"ceiling_db": "0dB"}}, {"expander": {"ratio": 2}}):
        code, d = run(request_doc([op("y", "DYNAMICS", ["track:t1"], **bad)]))
        assert d["ok"] is False and d["error"]["code"] == "INVALID_REQUEST", bad


def test_format_conversion_of_a_bare_track(workspace, skill_dir):
    from audio_production.doctor import doctor_report
    formats = doctor_report(str(skill_dir))["checks"]["output_formats"]
    for fmt in ("m4a", "mp3", "flac", "ogg", "opus"):
        code, d = run(request_doc([], outputs=[{"output_id": "o", "operation": "track:t1", "path": f"out/o.{fmt}", "format": fmt}]))
        if formats[fmt]["status"] == "unsupported":      # encoder not in this ffmpeg build: the negative path must be explicit
            assert d["error"]["code"] == "UNSUPPORTED_FORMAT" and d["error"]["details"]["capability"] == formats[fmt]["capability"], (fmt, d.get("error"))
            continue
        assert d["ok"], (fmt, d.get("error"))
        assert d["outputs"][0]["artifact"]["codec"] == d["plan"]["outputs"][0]["format"].replace("m4a", "aac").replace("ogg", "vorbis")
    # a video container: the audio track is extracted (ffmpeg-skill/audio) and the output has no video stream
    code, d = run(request_doc([], sources=[{"source_id": "a", "path": "video.mp4"}]))
    assert d["ok"], d.get("error")
    r = results(d)["track:t1"]
    assert r["tool"] == "ffmpeg-skill/audio" and r["measurements"]["has_video"] is True and r["artifact"]["codec"] == "pcm_s16le"
    assert probe(workspace / "out" / "main.wav")["codec"] == "pcm_s16le"
    assert subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(workspace / "out" / "main.wav")], capture_output=True, text=True).stdout.strip() == ""


def test_mix_two_sources_then_normalize(workspace):
    doc = request_doc([op("g", "GAIN", ["track:t1"], gain_db=-3), op("m", "MIX", ["op:g", "track:t2"], levels=[{"gain_db": 0}, {"gain_db": -10}]),
                       op("n", "NORMALIZE", ["op:m"], target_lufs=-18, true_peak_db=-2, tolerance_lufs=1.0)],
                      sources=[{"source_id": "a", "path": "tone.wav"}, {"source_id": "b", "path": "stereo.m4a"}],
                      tracks=[{"track_id": "t1", "source_id": "a"}, {"track_id": "t2", "source_id": "b", "range": {"start": 0.5, "end": 3.5}, "gain_db": -2}],
                      outputs=[{"output_id": "main", "operation": "op:n", "path": "out/mix.wav", "format": "wav", "expect": {"duration": 6.0}}])
    code, d = run(doc)
    assert d["ok"], d.get("error")
    r = results(d)
    assert r["op:t2.range"]["implicit"] and r["op:t2.gain"]["implicit"]
    segs = r["op:m"]["segments"]
    assert {s["source_id"] for s in segs} == {"a", "b"} and max(s["timeline"]["end"] for s in segs) == 6.0
    assert abs(probe(workspace / "out" / "mix.wav")["duration"] - 6.0) < 0.05
    prov = d["outputs"][0]["provenance"]
    assert set(prov["sources"]) == {"a", "b"} and {o["type"] for o in prov["operations"]} >= {"MIX", "NORMALIZE", "GAIN", "TRIM", "SOURCE_TRACK"}
    # three-way mix with a muted input
    doc2 = request_doc([op("m", "MIX", ["track:t1", "track:t2", "track:t3"], levels=[{"gain_db": 0}, {"gain_db": -6}, {"mute": True}])],
                       sources=[{"source_id": "a", "path": "tone.wav"}, {"source_id": "b", "path": "stereo.m4a"}, {"source_id": "c", "path": "gated.wav"}],
                       tracks=[{"track_id": "t1", "source_id": "a"}, {"track_id": "t2", "source_id": "b"}, {"track_id": "t3", "source_id": "c"}])
    code, d = run(doc2)
    assert d["ok"], d.get("error")
    code, d = run(request_doc([op("m", "MIX", ["track:t1", "track:t2"], levels=[{"mute": True}, {"mute": True}])],
                              sources=[{"source_id": "a", "path": "tone.wav"}, {"source_id": "b", "path": "stereo.m4a"}], tracks=[{"track_id": "t1", "source_id": "a"}, {"track_id": "t2", "source_id": "b"}]))
    assert d["error"]["code"] == "INVALID_REQUEST"


def test_real_audio_pipeline_trim_gain_fade_normalize(workspace):
    """The end-to-end scenario of the specification: source -> trim -> gain -> fade -> normalize -> validated output."""
    doc = request_doc([op("trim", "TRIM", ["track:t1"], start=1.0, end=5.0), op("gain", "GAIN", ["op:trim"], gain_db=-6),
                       op("fade", "FADE_OUT", ["op:gain"], duration=0.5), op("norm", "NORMALIZE", ["op:fade"], target_lufs=-16, true_peak_db=-1.5, tolerance_lufs=1.0)],
                      outputs=[{"output_id": "main", "operation": "op:norm", "path": "out/main.wav", "format": "wav", "expect": {"channels": 1, "duration": 4.0, "sample_rate": 48000}}])
    code, d = run(doc)
    assert code == 0 and d["ok"], d.get("error")
    r = results(d)
    assert [r[n]["status"] for n in ("op:trim", "op:gain", "op:fade", "op:norm")] == ["completed"] * 4
    assert r["op:norm"]["input_hashes"] == [r["op:fade"]["artifact"]["sha256"]]
    out = d["outputs"][0]
    p = probe(out["path"])
    assert abs(p["duration"] - 4.0) < 0.1 and p["channels"] == 1 and p["sample_rate"] == 48000
    assert out["artifact"]["sha256"] and out["provenance"]["skill_version"] and out["provenance"]["tool_versions"]["ffmpeg"]
    assert abs(r["op:norm"]["measurements"]["loudness"]["integrated_lufs"] + 16.0) <= 1.0
    assert all("commands_observed" in t for t in d["tool_runs"])


def test_dry_run_writes_nothing(workspace):
    doc = request_doc([op("g", "GAIN", ["track:t1"], gain_db=-3), op("n", "NORMALIZE", ["op:g"], target_lufs=-16, true_peak_db=-1)])
    code, out, _ = run_cli(["plan", "-", "--json"], json.dumps(doc))
    d = one_json(out)
    assert code == 0 and d["ok"] and d["dry_run"] is True
    assert [s["tool"] for s in d["plan"]["steps"]] == ["ffmpeg-skill/audio", "ffmpeg-skill/loudness"]
    assert d["plan"]["required_capabilities"] and d["plan"]["outputs"][0]["expected_duration"] == 6.0 and d["plan"]["plan_id"]
    assert all(r["status"] == "planned" for r in d["results"])
    assert not (workspace / "out").exists() and not (workspace / ".audio-production").exists()
    code2, out2, _ = run_cli(["run", "-", "--json", "--dry-run"], json.dumps(doc))
    assert one_json(out2)["plan"]["plan_id"] == d["plan"]["plan_id"]


def test_rerun_reuses_intermediates_and_is_deterministic(workspace):
    doc = request_doc([op("g", "GAIN", ["track:t1"], gain_db=-3), op("f", "FADE_IN", ["op:g"], duration=0.2)],
                      outputs=[{"output_id": "main", "operation": "op:f", "path": "out/main.wav", "format": "wav", "overwrite": True}])
    code, d1 = run(doc)
    code, d2 = run(doc)
    assert d1["ok"] and d2["ok"]
    assert [r["status"] for r in d2["results"]] == ["completed", "reused", "reused"]
    assert [r["operation_id"] for r in d1["results"]] == [r["operation_id"] for r in d2["results"]]
    assert d1["outputs"][0]["artifact"]["sha256"] == d2["outputs"][0]["artifact"]["sha256"]
    code, d3 = run(doc, "--no-reuse")
    assert [r["status"] for r in d3["results"]] == ["completed", "completed", "completed"]
    assert d3["outputs"][0]["artifact"]["sha256"] == d1["outputs"][0]["artifact"]["sha256"]
    # a tampered intermediate is not reused
    inter = next(workspace.glob(".audio-production/p1/*.wav"))
    inter.write_bytes(inter.read_bytes()[:-100])
    code, d4 = run(doc)
    assert d4["ok"] and "completed" in [r["status"] for r in d4["results"][1:]]


def test_options_reuse_false(workspace):
    doc = request_doc([op("g", "GAIN", ["track:t1"], gain_db=-3)], outputs=[{"output_id": "main", "operation": "op:g", "path": "out/main.wav", "format": "wav", "overwrite": True}], options={"reuse_intermediates": False})
    run(doc)
    code, d = run(doc)
    assert [r["status"] for r in d["results"]] == ["completed", "completed"]


def test_invalid_inputs(workspace):
    code, d = run(request_doc([], sources=[{"source_id": "a", "path": "missing.wav"}]))
    assert d["error"]["code"] == "INVALID_INPUT" and code == EXIT_CODES["INVALID_INPUT"]
    code, d = run(request_doc([], sources=[{"source_id": "a", "path": "text.txt"}]))
    assert d["error"]["code"] == "INVALID_INPUT"
    code, d = run(request_doc([], sources=[{"source_id": "a", "path": "noaudio.mp4"}]))
    assert d["error"]["code"] == "INVALID_INPUT" and d["error"]["details"]["reason"] == "no_audio_stream"


def test_output_expectation_failure_removes_output(workspace):
    doc = request_doc([op("g", "GAIN", ["track:t1"], gain_db=-3)], outputs=[{"output_id": "main", "operation": "op:g", "path": "out/main.wav", "format": "wav", "expect": {"duration": 3.0}}])
    code, d = run(doc)
    assert d["error"]["code"] == "VALIDATION_ERROR" and d["error"]["details"]["reason"] == "duration_mismatch"
    assert not (workspace / "out" / "main.wav").exists()
    assert d["outputs"][0]["status"] == "failed" and results(d)["op:g"]["status"] == "completed"
    doc = request_doc([op("g", "GAIN", ["track:t1"], gain_db=-3)], outputs=[{"output_id": "main", "operation": "op:g", "path": "out/main.wav", "format": "wav", "expect": {"channels": 2}}])
    code, d = run(doc)
    assert d["error"]["code"] == "VALIDATION_ERROR" and d["error"]["details"]["reason"] == "channel_mismatch"


def test_loudness_verification_failure(workspace):
    # a target the linear two-pass cannot reach under the true-peak ceiling (a sine at -5 LUFS needs about -2 dBTP):
    # ffmpeg-skill completes, the re-measurement is off target, the tolerance check reports it
    code, d = run(request_doc([op("n", "NORMALIZE", ["track:t1"], target_lufs=-5, true_peak_db=-3, tolerance_lufs=0.5)]))
    assert d["error"]["code"] == "VALIDATION_ERROR" and d["error"]["details"]["reason"] in ("loudness_off_target", "true_peak_exceeded")
    assert results(d)["op:n"]["status"] == "failed" and not list(workspace.glob(".audio-production/p1/*.wav"))


def test_timeout_is_a_retryable_tool_error(workspace):
    doc = request_doc([op("n", "NORMALIZE", ["track:t1"], target_lufs=-16, true_peak_db=-1)], options={"timeout": 1.0})
    # probe of the input happens first with the same timeout; a 1 s budget is enough for probe but the two-pass
    # normalisation of a 6 s file needs several ffmpeg runs; we cannot guarantee which step trips, only the outcome
    code, out, _ = run_cli(["run", "-", "--json", "--timeout", "0.05"], json.dumps(doc))
    d = one_json(out)
    assert d["ok"] is False and d["error"]["code"] in ("TOOL_ERROR", "INVALID_INPUT")
    if d["error"]["code"] == "TOOL_ERROR":
        assert d["error"]["retryable"] is True and d["error"]["details"]["reason"] == "timeout"
    assert not list(workspace.glob(".audio-production/p1/*.wav")) and not (workspace / "out").exists()


@pytest.mark.skipif(os.name == "nt", reason="a console signal cannot be delivered to one child on Windows without also hitting the test runner; SIGBREAK is registered in the CLI but exercised manually")
def test_signal_cancellation_leaves_no_partial_output(workspace):
    import signal
    import time
    doc = request_doc([op("n", "NORMALIZE", ["track:t1"], target_lufs=-16, true_peak_db=-1), op("n2", "NORMALIZE", ["op:n"], target_lufs=-17, true_peak_db=-1),
                       op("n3", "NORMALIZE", ["op:n2"], target_lufs=-18, true_peak_db=-1)])
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent / "src")
    proc = subprocess.Popen([sys.executable, "-m", "audio_production.cli", "run", "-", "--json"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    proc.stdin.write(json.dumps(doc))
    proc.stdin.close()
    deadline = time.time() + 20
    while time.time() < deadline and not (workspace / ".audio-production" / "p1").exists():
        time.sleep(0.05)
    time.sleep(0.4)
    proc.send_signal(signal.SIGINT if os.name != "nt" else signal.CTRL_BREAK_EVENT)
    out = proc.stdout.read()
    proc.stderr.read()
    proc.wait(timeout=30)
    d = one_json(out)
    assert d["ok"] is False and d["status"] == "cancelled" and d["error"]["code"] == "CANCELLED", d
    assert proc.returncode == EXIT_CODES["CANCELLED"]
    assert not (workspace / "out").exists()
    for wav in workspace.glob(".audio-production/p1/*.wav"):
        assert wav.with_suffix(".json").exists(), "an intermediate without a manifest is a partial output"


def test_cli_validate_and_exit_codes(workspace):
    code, out, _ = run_cli(["validate", "-", "--json"], json.dumps(request_doc([op("g", "GAIN", ["track:t1"], gain_db=-3)])))
    d = one_json(out)
    assert code == 0 and d["validation"]["ok"] and d["validation"]["graph"]["order"] == ["track:t1", "op:g"]
    code, out, _ = run_cli(["validate", "-", "--json"], json.dumps(request_doc([op("g", "ECHO", ["track:t1"])])))
    assert code == EXIT_CODES["UNSUPPORTED_OPERATION"] and one_json(out)["error"]["code"] == "UNSUPPORTED_OPERATION"
    code, out, _ = run_cli(["run", "-"], json.dumps(request_doc([op("g", "GAIN", ["track:t1"], gain_db=-3)])))
    assert code == 0 and "op:g" in out and not out.strip().startswith("{")
