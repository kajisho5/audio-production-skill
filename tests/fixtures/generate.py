"""Synthetic audio fixtures generated with ffmpeg at test time (nothing binary is committed). Only the tests call
ffmpeg directly; the skill under test never does. Every fixture has a known construction:

  tone.wav     6 s mono PCM 16-bit 48 kHz, 1 kHz sine amplitude 0.1 (-23.0 LUFS, -20 dBTP)
  stereo.m4a   4 s stereo AAC 48 kHz, 440 Hz sine amplitude 0.1 both channels
  gated.wav    6 s mono PCM 48 kHz, tone only between 2..5 s (silence 0-2 and 5-6)
  surround.wav 2 s 5.1 PCM 48 kHz
  silence.wav  2 s mono digital silence
  video.mp4    3 s 160x90 H.264 + mono AAC tone (audio inside a video container)
  noaudio.mp4  2 s 160x90 H.264, no audio stream
  multitrack.mka  3 s Matroska audio container, two mono PCM streams muxed together (audio.py --audio-stream N
                  selects between them): stream 0 a quiet 300 Hz tone (amplitude 0.05, measured mean_volume -29 dB),
                  stream 1 a loud 900 Hz tone (amplitude 0.5, measured mean_volume -9 dB) -- a 20 dB / distinct-tone
                  difference objective enough that picking the wrong stream cannot be mistaken for picking the right one
  fillers.wav  6 s mono PCM 48 kHz, 1 kHz sine: amplitude 0.1 ("words") except 1.0-1.5 s and 3.0-3.4 s, where it is 0.5
               ("fillers", 14 dB louder): removing exactly those ranges leaves 5.1 s = 244800 samples peaking at -20 dBFS
  ambiguous.wav 8.5 s mono PCM 48 kHz, 1 kHz sine in 17 half-second "words" (tests.test_integration.AMBIGUOUS_WORDS):
               amplitude 0.5 where a word equal to an opt-in list entry is expected to be cut (0-0.5, 1-2.5, 3-3.5,
               4-4.5, 5-6.5 s), 0.1 elsewhere: removing exactly those leaves 4.0 s = 192000 samples peaking at -20 dBFS
  keyed.wav    6 s mono PCM 48 kHz, loud 1 kHz sine (amplitude 0.5) only between 2..5 s: the key a bed ducks under
  bed.wav      6 s mono PCM 48 kHz, steady 440 Hz sine amplitude 0.1: a bed whose level is measured around 440 Hz
  programme.wav 6 s mono PCM 48 kHz, 1 kHz sine amplitude 0.1 with a 50 ms burst to 0.4 every second (measured
               -20.5 LUFS, -8.0 dBTP): -14 LUFS needs about -1.4 dBTP, so a -1 dBTP ceiling is close but holds
  peaky.wav    6 s mono PCM 48 kHz, 1 kHz sine amplitude 0.05 with a 50 ms burst to 0.65 every second (crest about
               20 dB): -14 LUFS under -1 dBTP is out of reach of a linear gain
  sfx.wav      4 s mono PCM 48 kHz, 2 kHz sine amplitude 0.2 (an effects track shorter than the programme)
  longbed.wav  8 s mono PCM 48 kHz, 220 Hz sine amplitude 0.1 (a music bed longer than the programme)
  wide.wav     24 s mono PCM 48 kHz, 1 kHz sine alternating every 4 s between amplitude 0.2 and 18 dB below it (measured
               LRA 18.0 LU, -17.3 LUFS, -14.0 dBTP, FFmpeg 6.1): above loudnorm's default LRA target of 11 LU, so loudnorm
               runs its second pass in dynamic mode; with an LRA target of 20 LU it stays linear
  mic1.wav, mic2.wav, mic3.wav
               12 s mono PCM 48 kHz each, three microphones of one discussion (AUTOMIX, ADR-29). Each carries its own
               steady self-noise, a marker sine at 150 / 400 / 1000 Hz amplitude 0.003 (about -53 dBFS), so the gain the
               mix gave an input is that marker's level in the mix minus its level in the input. One person talks at a
               time: a 4000 / 4500 / 5000 Hz sine amplitude 0.2, amplitude-modulated at 3..5 Hz, on mic1 at 1-4 s, mic2 at
               4-7 s, mic3 at 7-10 s; each talker also reaches the two other microphones 15.4 dB lower (x0.17, bleed).
               0-1 s and 10-12 s: nobody talks
  duo1.wav, duo2.wav
               6 s mono PCM 48 kHz: markers 150 / 400 Hz as above, and the same talker level on both at 2-5 s (two people
               talking at once)
  mic3noisy.wav mic3.wav with its self-noise 20 dB louder (marker amplitude 0.03): a noisy microphone
  tone44.wav   1 s mono PCM 44.1 kHz, 1 kHz sine amplitude 0.1 (an input at another sample rate)
  text.txt     not media"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Dict

FF = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostdin"]
TONE = "0.1*sin(2*PI*1000*t)"
TONE_GATED = "0.1*sin(2*PI*1000*t)*between(t\\,2\\,5)"
TONE_FILLERS = "(0.1+0.4*(between(t\\,1\\,1.5)+between(t\\,3\\,3.4)))*sin(2*PI*1000*t)"
TONE_AMBIGUOUS = ("(0.1+0.4*(between(t\\,0\\,0.5)+between(t\\,1\\,2.5)+between(t\\,3\\,3.5)+between(t\\,4\\,4.5)+between(t\\,5\\,6.5)))"
                  "*sin(2*PI*1000*t)")
TONE_KEYED = "0.5*sin(2*PI*1000*t)*between(t\\,2\\,5)"
TONE_PROGRAMME = "(0.1+0.3*between(mod(t\\,1)\\,0.5\\,0.55))*sin(2*PI*1000*t)"
TONE_PEAKY = "(0.05+0.6*between(mod(t\\,1)\\,0\\,0.05))*sin(2*PI*1000*t)"
TALK = {1: "0.2*sin(2*PI*4000*t)*(0.6+0.4*sin(2*PI*4*t))*between(t\\,1\\,4)",
        2: "0.2*sin(2*PI*4500*t)*(0.6+0.4*sin(2*PI*5*t))*between(t\\,4\\,7)",
        3: "0.2*sin(2*PI*5000*t)*(0.6+0.4*sin(2*PI*3*t))*between(t\\,7\\,10)"}
MARKER = {1: 150, 2: 400, 3: 1000}


def mic_expr(k: int, noise: float = 0.003) -> str:
    others = "+".join(TALK[j] for j in TALK if j != k)
    return f"{noise}*sin(2*PI*{MARKER[k]}*t)+{TALK[k]}+0.17*({others})"


DUO_TALK = "0.2*sin(2*PI*4000*t)*(0.6+0.4*sin(2*PI*4*t))*between(t\\,2\\,5)"
TONE_WIDE = "(0.2-0.2*(1-pow(10\\,-18/20))*lt(mod(t\\,8)\\,4))*sin(2*PI*1000*t)"


def available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _run(args):
    subprocess.run(FF + args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def build_all(directory: Path) -> Dict[str, Path]:
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    f = {k: d / v for k, v in {"tone": "tone.wav", "stereo": "stereo.m4a", "gated": "gated.wav", "surround": "surround.wav", "silence": "silence.wav",
                               "video": "video.mp4", "noaudio": "noaudio.mp4", "multitrack": "multitrack.mka", "fillers": "fillers.wav",
                               "ambiguous": "ambiguous.wav",
                               "keyed": "keyed.wav", "bed": "bed.wav", "programme": "programme.wav", "peaky": "peaky.wav", "sfx": "sfx.wav", "longbed": "longbed.wav", "wide": "wide.wav",
                               "mic1": "mic1.wav", "mic2": "mic2.wav", "mic3": "mic3.wav", "mic3noisy": "mic3noisy.wav", "duo1": "duo1.wav", "duo2": "duo2.wav",
                               "tone44": "tone44.wav", "text": "text.txt"}.items()}
    _run(["-f", "lavfi", "-i", f"aevalsrc='{TONE}':s=48000:c=mono", "-t", "6", "-c:a", "pcm_s16le", str(f["tone"])])
    _run(["-f", "lavfi", "-i", "aevalsrc='0.1*sin(2*PI*440*t)|0.1*sin(2*PI*440*t)':s=48000:c=stereo", "-t", "4", "-c:a", "aac", str(f["stereo"])])
    _run(["-f", "lavfi", "-i", f"aevalsrc='{TONE_GATED}':s=48000:c=mono", "-t", "6", "-c:a", "pcm_s16le", str(f["gated"])])
    _run(["-f", "lavfi", "-i", "aevalsrc='0.1*sin(2*PI*440*t)|0.1*sin(2*PI*440*t)|0.1*sin(2*PI*440*t)|0|0.05*sin(2*PI*440*t)|0.05*sin(2*PI*440*t)':s=48000:c=5.1",
          "-t", "2", "-c:a", "pcm_s16le", str(f["surround"])])
    _run(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "2", "-c:a", "pcm_s16le", str(f["silence"])])
    _run(["-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25", "-f", "lavfi", "-i", f"aevalsrc='{TONE}':s=48000", "-t", "3",
          "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(f["video"])])
    _run(["-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25", "-t", "2", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(f["noaudio"])])
    _run(["-f", "lavfi", "-i", "aevalsrc='0.05*sin(2*PI*300*t)':s=48000:c=mono", "-f", "lavfi", "-i", "aevalsrc='0.5*sin(2*PI*900*t)':s=48000:c=mono",
          "-t", "3", "-map", "0:a", "-map", "1:a", "-c:a", "pcm_s16le", str(f["multitrack"])])
    for key, expr in (("fillers", TONE_FILLERS), ("keyed", TONE_KEYED), ("bed", "0.1*sin(2*PI*440*t)"), ("programme", TONE_PROGRAMME), ("peaky", TONE_PEAKY)):
        _run(["-f", "lavfi", "-i", f"aevalsrc='{expr}':s=48000:c=mono", "-t", "6", "-c:a", "pcm_s16le", str(f[key])])
    _run(["-f", "lavfi", "-i", f"aevalsrc='{TONE_AMBIGUOUS}':s=48000:c=mono", "-t", "8.5", "-c:a", "pcm_s16le", str(f["ambiguous"])])
    _run(["-f", "lavfi", "-i", "aevalsrc='0.2*sin(2*PI*2000*t)':s=48000:c=mono", "-t", "4", "-c:a", "pcm_s16le", str(f["sfx"])])
    _run(["-f", "lavfi", "-i", "aevalsrc='0.1*sin(2*PI*220*t)':s=48000:c=mono", "-t", "8", "-c:a", "pcm_s16le", str(f["longbed"])])
    _run(["-f", "lavfi", "-i", f"aevalsrc='{TONE_WIDE}':s=48000:c=mono", "-t", "24", "-c:a", "pcm_s16le", str(f["wide"])])
    for k in (1, 2, 3):
        _run(["-f", "lavfi", "-i", f"aevalsrc='{mic_expr(k)}':s=48000:c=mono", "-t", "12", "-c:a", "pcm_s16le", str(f[f"mic{k}"])])
    _run(["-f", "lavfi", "-i", f"aevalsrc='{mic_expr(3, 0.03)}':s=48000:c=mono", "-t", "12", "-c:a", "pcm_s16le", str(f["mic3noisy"])])
    for k in (1, 2):
        _run(["-f", "lavfi", "-i", f"aevalsrc='0.003*sin(2*PI*{MARKER[k]}*t)+{DUO_TALK}':s=48000:c=mono", "-t", "6", "-c:a", "pcm_s16le", str(f[f"duo{k}"])])
    _run(["-f", "lavfi", "-i", f"aevalsrc='{TONE}':s=44100:c=mono", "-t", "1", "-c:a", "pcm_s16le", str(f["tone44"])])
    f["text"].write_text("not media\n", encoding="utf-8")
    return f
