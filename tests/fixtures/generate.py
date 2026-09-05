"""Synthetic audio fixtures generated with ffmpeg at test time (nothing binary is committed). Only the tests call
ffmpeg directly; the skill under test never does. Every fixture has a known construction:

  tone.wav     6 s mono PCM 16-bit 48 kHz, 1 kHz sine amplitude 0.1 (-23.0 LUFS, -20 dBTP)
  stereo.m4a   4 s stereo AAC 48 kHz, 440 Hz sine amplitude 0.1 both channels
  gated.wav    6 s mono PCM 48 kHz, tone only between 2..5 s (silence 0-2 and 5-6)
  surround.wav 2 s 5.1 PCM 48 kHz
  silence.wav  2 s mono digital silence
  video.mp4    3 s 160x90 H.264 + mono AAC tone (audio inside a video container)
  noaudio.mp4  2 s 160x90 H.264, no audio stream
  text.txt     not media"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Dict

FF = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostdin"]
TONE = "0.1*sin(2*PI*1000*t)"
TONE_GATED = "0.1*sin(2*PI*1000*t)*between(t\\,2\\,5)"


def available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _run(args):
    subprocess.run(FF + args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def build_all(directory: Path) -> Dict[str, Path]:
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    f = {k: d / v for k, v in {"tone": "tone.wav", "stereo": "stereo.m4a", "gated": "gated.wav", "surround": "surround.wav", "silence": "silence.wav",
                               "video": "video.mp4", "noaudio": "noaudio.mp4", "text": "text.txt"}.items()}
    _run(["-f", "lavfi", "-i", f"aevalsrc='{TONE}':s=48000:c=mono", "-t", "6", "-c:a", "pcm_s16le", str(f["tone"])])
    _run(["-f", "lavfi", "-i", "aevalsrc='0.1*sin(2*PI*440*t)|0.1*sin(2*PI*440*t)':s=48000:c=stereo", "-t", "4", "-c:a", "aac", str(f["stereo"])])
    _run(["-f", "lavfi", "-i", f"aevalsrc='{TONE_GATED}':s=48000:c=mono", "-t", "6", "-c:a", "pcm_s16le", str(f["gated"])])
    _run(["-f", "lavfi", "-i", "aevalsrc='0.1*sin(2*PI*440*t)|0.1*sin(2*PI*440*t)|0.1*sin(2*PI*440*t)|0|0.05*sin(2*PI*440*t)|0.05*sin(2*PI*440*t)':s=48000:c=5.1",
          "-t", "2", "-c:a", "pcm_s16le", str(f["surround"])])
    _run(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "2", "-c:a", "pcm_s16le", str(f["silence"])])
    _run(["-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25", "-f", "lavfi", "-i", f"aevalsrc='{TONE}':s=48000", "-t", "3",
          "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(f["video"])])
    _run(["-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25", "-t", "2", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(f["noaudio"])])
    f["text"].write_text("not media\n", encoding="utf-8")
    return f
