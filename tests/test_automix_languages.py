"""Integration tests, real audio through the real ffmpeg-skill: AUTOMIX (ADR-29), MIX pan and MIX languages (ADR-30).

The AUTOMIX fixtures (tests/fixtures/generate.py, mic1..3.wav) give every microphone its own steady marker sine, so the
gain the mix applied to one input is measured from the mix itself: that marker's band level in the mix minus its band
level in the input (ffmpeg band-pass + volumedetect, test-only). Nothing here reads the skill's own report to decide
whether a gain moved; the report is checked against the measurement."""
import math
import subprocess

from audio_production.adapter import FfmpegSkill
from conftest import request_doc
from test_integration import band_level_dbfs, op, results, run

MICS = {"mic1": 150, "mic2": 400, "mic3": 1000}
SLOTS = {"mic1": (1.5, 3.5), "mic2": (4.5, 6.5), "mic3": (7.5, 9.5)}      # where each one talks (inside 1-4, 4-7, 7-10 s)
PAUSES = ((0.3, 0.9), (10.5, 11.8))
RATE = 400.0                                                             # the stated max_gain_rate_db_per_s


def mic_request(names, params, out="out/am.wav", files=None):
    files = files or {n: f"{n}.wav" for n in names}
    srcs = [{"source_id": n, "path": files[n]} for n in names]
    trks = [{"track_id": n, "source_id": n} for n in names]
    return request_doc([op("am", "AUTOMIX", [f"track:{n}" for n in names], **params)], sources=srcs, tracks=trks,
                       outputs=[{"output_id": "o", "operation": "op:am", "path": out, "format": "wav"}])


def gain_db(mix, src, freq, start, end):
    """The gain the mix gave `src` over [start, end): its marker's band level in the mix minus in the input."""
    return band_level_dbfs(mix, start, end, freq) - band_level_dbfs(src, start, end, freq)


def power_sum_db(gains):
    return 10 * math.log10(sum(10 ** (g / 10) for g in gains))


def mean_volume(path, start, end):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path), "-af", f"atrim={start}:{end},volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    return float(next(l for l in r.stderr.splitlines() if "mean_volume" in l).split(":")[1].strip().split(" ")[0])


def test_automix_three_mics_one_at_a_time(workspace):
    """Done when (ECOSYSTEM_ROADMAP step 10): one person talks at a time on three microphones; the two others stay at
    least 10 dB down, the shared gains sum to about 0 dB, the pauses keep the noise of one microphone, and no gain
    moves faster than the stated rate."""
    code, d = run(mic_request(list(MICS), {"window_ms": 50, "max_gain_rate_db_per_s": RATE}))
    assert d["ok"], d.get("error")
    mix = workspace / "out" / "am.wav"
    for talker, (a, b) in SLOTS.items():
        g = {n: gain_db(mix, workspace / f"{n}.wav", f, a, b) for n, f in MICS.items()}
        assert g[talker] > -1.0, (talker, g)                                   # the talking microphone is open
        for n in MICS:
            if n != talker:
                assert g[n] <= g[talker] - 10.0, (talker, g)                    # the others at least 10 dB down
        assert abs(power_sum_db(g.values())) < 1.0, (talker, g)                # the total gain stays the same
    for a, b in PAUSES:
        g = {n: gain_db(mix, workspace / f"{n}.wav", f, a, b) for n, f in MICS.items()}
        assert all(abs(x + 4.77) < 1.0 for x in g.values()), g                  # three equal shares: -10*log10(3)
        assert abs(power_sum_db(g.values())) < 1.0, g
        # the noise in the mix is that of one microphone, not three (three uncorrelated ones open would be +4.8 dB)
        assert abs(mean_volume(mix, a, b) - mean_volume(workspace / "mic1.wav", a, b)) < 1.5
    # no step faster than the stated rate: a talker's marker level in the mix, 20 ms windows across each hand-over
    for n, f in MICS.items():
        for t in (1.0, 4.0, 7.0, 10.0):
            levels = [band_level_dbfs(mix, t - 0.3 + 0.02 * k, t - 0.28 + 0.02 * k, f) for k in range(30)]
            steps = [abs(y - x) / 0.02 for x, y in zip(levels, levels[1:])]
            assert max(steps) <= RATE * 1.25, (n, t, max(steps))
    m = results(d)["op:am"]["measurements"]["automix"]
    assert m["measured_max_gain_rate_db_per_s"] <= RATE and abs(m["shared_gain_sum_db"]["min"]) < 1e-6 and abs(m["shared_gain_sum_db"]["max"]) < 1e-6, m
    assert m["clipped_samples"] == 0 and m["hop_ms"] == 5.0 and m["samples"] == 12 * 48000
    # open (share above -6 dB): its own 3 s, and the 3 s of pauses shared three ways (-4.8 dB each), of 12 s
    assert all(0.4 < i["open_ratio"] < 0.6 for i in m["inputs"]), m["inputs"]
    assert results(d)["op:am"]["artifact"]["channels"] == 1 and abs(results(d)["op:am"]["artifact"]["duration"] - 12.0) < 0.01


def test_automix_two_at_once_share_equally(workspace):
    """Two people talking at once at the same level: both are open at -3 dB, together 0 dB."""
    code, d = run(mic_request(["duo1", "duo2"], {"window_ms": 50, "max_gain_rate_db_per_s": RATE}))
    assert d["ok"], d.get("error")
    mix = workspace / "out" / "am.wav"
    g = [gain_db(mix, workspace / "duo1.wav", 150, 2.5, 4.5), gain_db(mix, workspace / "duo2.wav", 400, 2.5, 4.5)]
    assert all(abs(x + 3.01) < 1.0 for x in g), g
    assert abs(power_sum_db(g)) < 1.0, g


def test_automix_hold_keeps_the_last_open_mic(workspace):
    """hold_below_dbfs: in the pause after the last talker, that microphone stays the open one instead of the shares
    going back to equal; without it the pause is shared three ways."""
    code, d = run(mic_request(list(MICS), {"window_ms": 50, "max_gain_rate_db_per_s": RATE, "hold_below_dbfs": -35}))
    assert d["ok"], d.get("error")
    mix = workspace / "out" / "am.wav"
    g = {n: gain_db(mix, workspace / f"{n}.wav", f, 10.5, 11.8) for n, f in MICS.items()}
    assert g["mic3"] > -2.0 and g["mic1"] < g["mic3"] - 6.0 and g["mic2"] < g["mic3"] - 6.0, g
    assert abs(power_sum_db(g.values())) < 1.0, g
    m = results(d)["op:am"]["measurements"]["automix"]
    assert m["held_ratio"] > 0.2 and m["hold_below_dbfs"] == -35 and m["measured_max_gain_rate_db_per_s"] <= RATE, m


def test_automix_pinned_and_weighted_inputs(workspace):
    """A pinned input is mixed at its gain_db throughout and takes no share; a noisy input dominates the pauses unless
    its weight lowers its share."""
    params = {"window_ms": 50, "max_gain_rate_db_per_s": RATE,
              "channels": [{"pinned": True, "gain_db": -6}, {}, {}]}
    code, d = run(mic_request(list(MICS), params))
    assert d["ok"], d.get("error")
    mix = workspace / "out" / "am.wav"
    for a, b in list(SLOTS.values()) + list(PAUSES):
        assert abs(gain_db(mix, workspace / "mic1.wav", 150, a, b) + 6.0) < 0.7, (a, b)          # pinned: -6 dB, always
    g = [gain_db(mix, workspace / f"{n}.wav", f, *SLOTS["mic2"]) for n, f in (("mic2", 400), ("mic3", 1000))]
    assert g[0] > -1.0 and g[1] < g[0] - 10.0 and abs(power_sum_db(g)) < 1.0, g                # the two shared ones still share

    files = {"mic1": "mic1.wav", "mic2": "mic2.wav", "mic3": "mic3noisy.wav"}
    pause = {}
    for name, weight in (("plain", 0), ("weighted", -20)):
        params = {"window_ms": 50, "max_gain_rate_db_per_s": RATE, "channels": [{}, {}, {"weight_db": weight}]}
        code, d = run(mic_request(list(MICS), params, out=f"out/{name}.wav", files=files))
        assert d["ok"], d.get("error")
        mix = workspace / "out" / f"{name}.wav"
        (workspace / f"{name}.wav").write_bytes(mix.read_bytes())
        pause[name] = gain_db(workspace / f"{name}.wav", workspace / "mic3noisy.wav", 1000, 10.5, 11.8)
        talk = gain_db(workspace / f"{name}.wav", workspace / "mic3noisy.wav", 1000, *SLOTS["mic1"])
        assert talk < -10.0, (name, talk)                                     # still down while another one talks
    assert pause["plain"] > -1.0, pause                                       # unweighted, the noise takes the pause
    # weighted 20 dB down, its noise counts as much as the others' (20 dB quieter): the pause is shared three ways again
    assert abs(pause["weighted"] + 4.77) < 1.0 and pause["weighted"] < pause["plain"] - 3.5, pause


def test_automix_refusals(workspace):
    base = {"window_ms": 50, "max_gain_rate_db_per_s": RATE}
    code, d = run(mic_request(["mic1", "mic2"], {**base, "channels": [{"pinned": True}, {"pinned": True}]}))
    assert not d["ok"] and d["error"]["code"] == "INVALID_REQUEST" and "pinned" in d["error"]["message"]
    code, d = run(mic_request(["mic1", "mic2"], {**base, "channels": [{}]}))
    assert not d["ok"] and d["error"]["code"] == "INVALID_REQUEST"
    code, d = run(mic_request(["mic1", "mic2"], {"window_ms": 50}))                    # no rate: no default
    assert not d["ok"] and d["error"]["code"] == "INVALID_REQUEST"
    code, d = run(mic_request(["mic1", "tone44"], base, files={"mic1": "mic1.wav", "tone44": "tone44.wav"}))
    assert not d["ok"] and d["error"]["code"] == "INVALID_REQUEST" and d["error"]["details"]["reason"] == "automix_sample_rate_mismatch", d["error"]
    assert not (workspace / "out" / "am.wav").exists()
    code, d = run(mic_request(["surround", "mic1"], base, files={"surround": "surround.wav", "mic1": "mic1.wav"}))
    assert not d["ok"] and d["error"]["code"] == "INVALID_CHANNEL_LAYOUT"


# ---- MIX_TRACKS: pan and languages (ADR-30)

def channel_band(path, channel, start, end, freq, stream=0):
    """Band level of one channel of one audio stream (test-only measurement)."""
    af = f"pan=mono|c0=c{channel},atrim={start}:{end},bandpass=f={freq}:width_type=q:w=8,bandpass=f={freq}:width_type=q:w=8,volumedetect"
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path), "-map", f"0:a:{stream}", "-af", af, "-f", "null", "-"], capture_output=True, text=True)
    return float(next(l for l in r.stderr.splitlines() if "mean_volume" in l).split(":")[1].strip().split(" ")[0])


def test_mix_pan_places_mono_inputs(workspace):
    """tone.wav (1 kHz) hard left, bed.wav (440 Hz) hard right, sfx.wav (2 kHz) centred by default: a stereo mix with
    each where it was put; the centre one -3 dB on each side."""
    srcs = [{"source_id": "a", "path": "tone.wav"}, {"source_id": "b", "path": "bed.wav"}, {"source_id": "c", "path": "sfx.wav"}]
    trks = [{"track_id": "a", "source_id": "a"}, {"track_id": "b", "source_id": "b"}, {"track_id": "c", "source_id": "c"}]
    code, d = run(request_doc([op("m", "MIX_TRACKS", ["track:a", "track:b", "track:c"], levels=[{"pan": -1}, {"pan": 1}, {}])], sources=srcs, tracks=trks,
                              outputs=[{"output_id": "o", "operation": "op:m", "path": "out/pan.wav", "format": "wav", "expect": {"channels": 2, "duration": 6.0}}]))
    assert d["ok"], d.get("error")
    out = workspace / "out" / "pan.wav"
    left_1k, right_1k = channel_band(out, 0, 0.5, 3.5, 1000), channel_band(out, 1, 0.5, 3.5, 1000)
    left_440, right_440 = channel_band(out, 0, 0.5, 3.5, 440), channel_band(out, 1, 0.5, 3.5, 440)
    ref = channel_band(workspace / "tone.wav", 0, 0.5, 3.5, 1000)
    assert abs(left_1k - ref) < 0.5 and right_1k < ref - 30, (left_1k, right_1k, ref)
    assert abs(right_440 - channel_band(workspace / "bed.wav", 0, 0.5, 3.5, 440)) < 0.5 and left_440 < right_440 - 30, (left_440, right_440)
    centre = [channel_band(out, c, 0.5, 3.5, 2000) - channel_band(workspace / "sfx.wav", 0, 0.5, 3.5, 2000) for c in (0, 1)]
    assert all(abs(x + 3.01) < 0.5 for x in centre), centre
    r = results(d)["op:m"]
    assert r["measurements"]["pan"] == [{"input": 0, "pan": -1.0}, {"input": 1, "pan": 1.0}, {"input": 2, "pan": 0.0}]
    assert any(c.startswith("[in-process] audio_production.pcm.pan_mono") for c in r["tool_commands_observed"])
    # pan places a 1-channel input only
    srcs2 = [{"source_id": "a", "path": "tone.wav"}, {"source_id": "s", "path": "stereo.m4a"}]
    trks2 = [{"track_id": "a", "source_id": "a"}, {"track_id": "s", "source_id": "s"}]
    code, d = run(request_doc([op("m", "MIX_TRACKS", ["track:a", "track:s"], levels=[{}, {"pan": 0.5}])], sources=srcs2, tracks=trks2))
    assert not d["ok"] and d["error"]["code"] == "INVALID_CHANNEL_LAYOUT" and d["error"]["details"]["reason"] == "pan_needs_mono"


LANG_SRCS = [{"source_id": "floor", "path": "tone.wav"}, {"source_id": "ja", "path": "bed.wav"}, {"source_id": "en", "path": "longbed.wav"}]
LANG_TRKS = [{"track_id": "floor", "source_id": "floor"}, {"track_id": "ja", "source_id": "ja"}, {"track_id": "en", "source_id": "en"}]
LANGS = [{"language_id": "ja", "language": "jpn", "title": "Japanese interpretation", "inputs": [1]},
         {"language_id": "en", "language": "eng", "title": "English interpretation", "inputs": [2]}]


def lang_request(outputs, **params):
    params.setdefault("levels", [{"gain_db": -12}, {}, {}])
    params.setdefault("languages", LANGS)
    return request_doc([op("m", "MIX_TRACKS", ["track:floor", "track:ja", "track:en"], **params)], sources=LANG_SRCS, tracks=LANG_TRKS, outputs=outputs)


def test_mix_languages_two_tagged_tracks(workspace, skill_dir):
    """Done when (ECOSYSTEM_ROADMAP step 11): floor sound (1 kHz) with a Japanese (440 Hz) and an English (220 Hz)
    interpreter gives two tracks tagged jpn / eng, each the floor 12 dB under its own interpreter and without the other."""
    outputs = [{"output_id": "set", "operation": "op:m", "path": "out/langs.mkv", "format": "mkv", "expect": {"duration": 6.0}},
               {"output_id": "set4", "operation": "op:m", "path": "out/langs.mp4", "format": "mp4"},
               {"output_id": "ja", "operation": "op:m.ja", "path": "out/ja.wav", "format": "wav"}]
    code, d = run(lang_request(outputs))
    if not FfmpegSkill(skill_dir).info().takes_flag("audio", "track"):
        # an ffmpeg-skill without --track (0.12.2) refuses the track set at planning, before anything runs
        assert not d["ok"] and d["error"]["code"] == "TOOL_ERROR" and d["error"]["details"]["reason"] == "ffmpeg_skill_flag_missing", d.get("error")
        assert not (workspace / "out").exists() or not any((workspace / "out").iterdir())
        code, d = run(lang_request([outputs[2]]))                       # one language mix alone needs no --track
        assert d["ok"], d.get("error")
        return
    assert d["ok"], d.get("error")
    for path in (workspace / "out" / "langs.mkv", workspace / "out" / "langs.mp4"):
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_name:stream_tags=language,title,handler_name",
                            "-of", "json", str(path)], capture_output=True, text=True, check=True)
        import json
        streams = json.loads(r.stdout)["streams"]
        assert [s["tags"]["language"] for s in streams] == ["jpn", "eng"], streams
        names = [s["tags"].get("title") or s["tags"].get("handler_name") for s in streams]
        assert names == ["Japanese interpretation", "English interpretation"], streams
        assert {s["codec_name"] for s in streams} == ({"pcm_s24le"} if path.suffix == ".mkv" else {"aac"})
        floor = [channel_band(path, 0, 0.5, 5.5, 1000, stream=k) for k in (0, 1)]
        interp = [channel_band(path, 0, 0.5, 5.5, 440, stream=0), channel_band(path, 0, 0.5, 5.5, 220, stream=1)]
        other = [channel_band(path, 0, 0.5, 5.5, 220, stream=0), channel_band(path, 0, 0.5, 5.5, 440, stream=1)]
        for k in (0, 1):
            assert abs(interp[k] - floor[k] - 12.0) < 1.0, (path.name, k, floor, interp)    # the floor 12 dB under its interpreter
            assert other[k] < interp[k] - 40.0, (path.name, k, other, interp)              # and no other language's interpreter
    summary = d["language_tracks"][0]
    assert [x["language"] for x in summary["languages"]] == ["jpn", "eng"]
    assert summary["languages"][0]["members"] == ["track:floor", "track:ja"] and summary["languages"][1]["members"] == ["track:floor", "track:en"]
    assert [t["language"] for t in summary["outputs"][0]["tracks"]] == ["jpn", "eng"]
    assert summary["languages"][0]["outputs"][0]["output_id"] == "ja"
    assert results(d)["op:m"]["type"] == "LANGUAGE_TRACKS" and len(results(d)["op:m"]["measurements"]["tracks"]) == 2


def test_mix_languages_refusals(workspace):
    wav = [{"output_id": "o", "operation": "op:m", "path": "out/x.wav", "format": "wav"}]
    code, d = run(lang_request(wav))
    assert not d["ok"] and d["error"]["code"] == "UNSUPPORTED_FORMAT" and d["error"]["details"]["reason"] == "track_set_format"
    code, d = run(request_doc([op("g", "GAIN", ["track:t1"], gain_db=-3)], outputs=[{"output_id": "o", "operation": "op:g", "path": "out/x.mkv", "format": "mkv"}]))
    assert not d["ok"] and d["error"]["code"] == "UNSUPPORTED_FORMAT"
    mkv = [{"output_id": "o", "operation": "op:m", "path": "out/x.mkv", "format": "mkv"}]
    code, d = run(lang_request(mkv, duck={"input": 1, "amount_db": 12}))                 # MIX_TRACKS has no duck
    assert not d["ok"] and d["error"]["code"] == "INVALID_REQUEST"
    code, d = run(request_doc([op("m", "MIX", ["track:floor", "track:ja"], levels=[{}, {"pan": 1}])], sources=LANG_SRCS[:2], tracks=LANG_TRKS[:2]))
    assert not d["ok"] and d["error"]["code"] == "INVALID_REQUEST"                         # MIX itself takes no pan: MIX_TRACKS does
    code, d = run(lang_request(mkv, languages=[{**LANGS[0], "language": "ja"}]))
    assert not d["ok"] and d["error"]["code"] == "INVALID_REQUEST" and "ISO 639-2" in d["error"]["message"]
    code, d = run(lang_request(mkv, languages=[LANGS[0], {**LANGS[1], "inputs": [1]}]))
    assert not d["ok"] and d["error"]["code"] == "INVALID_REQUEST" and "at most one language" in d["error"]["message"]
    code, d = run(lang_request(mkv, languages=[{**LANGS[0], "inputs": [0]}]))
    assert not d["ok"] and d["error"]["code"] == "INVALID_REQUEST"
    doc = lang_request(wav)
    doc["project"]["operations"].append(op("g", "GAIN", ["op:m"], gain_db=-3))
    doc["project"]["outputs"] = [{"output_id": "o", "operation": "op:g", "path": "out/x.wav", "format": "wav"}]
    code, d = run(doc)
    assert not d["ok"] and d["error"]["details"]["reason"] == "track_set_reference" and d["error"]["details"]["languages"] == ["op:m.en", "op:m.ja"]
