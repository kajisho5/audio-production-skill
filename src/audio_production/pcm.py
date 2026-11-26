"""Sample arithmetic on 16-bit PCM WAV, in this process (standard library only): AUTOMIX's gain sharing and MIX's
per-input pan (ADR-32). ffmpeg-skill has no gain envelope and no pan flag, so the executor has it write each input as
16-bit PCM WAV first (`audio.py ... -o x.wav`), then reads and writes the samples here. Nothing here starts a process,
reads a request, or touches any path the executor did not give it.

AUTOMIX (gain sharing):
  Every input's level is measured in hops of HOP_SECONDS: the mean square of the hop, times the input's weight,
  smoothed with a one-pole average of time constant `window_ms`, in dB. Those levels move at most half of
  `max_gain_rate_db_per_s` (slew limit). An input's gain is its level minus the power sum of every shared input's
  level (in dB), so the shared gains, as powers, always sum to one (0 dB in total): one input that carries all the
  sound is at 0 dB and the others are as far down as their level is below it; two equal inputs are at -3 dB each;
  N inputs with only their own noise are at -10*log10(N) dB each, so the noise in the mix stays that of one input
  (the number-of-open-inputs attenuation is part of the rule, not a separate stage). The power sum of the levels
  moves by no more than the fastest of them, so no gain moves faster than `max_gain_rate_db_per_s`. Between hop
  values the gain is interpolated geometrically, sample by sample.
  `hold_below_dbfs` (optional): while every shared input's measured level (unweighted) is below it, the levels and
  so the gains hold their last values: the last input that was open stays open through the pause.
  A pinned input is not shared: it is mixed at its own gain_db throughout. gain_db of a shared input is added to its
  share. weight_db of a shared input scales its measured level only (a noisy input with a negative weight takes a
  smaller share of the pauses)."""
from __future__ import annotations

import math
import sys
import wave
from array import array
from itertools import accumulate, repeat
from operator import add, mul
from typing import Any, Dict, List, Optional

from .errors import AudioError

HOP_SECONDS = 0.005           # one gain value per 5 ms, interpolated between
_FLOOR_POWER = 1e-12          # -120 dBFS: the level of digital silence
_FULL = 32768.0


def _read(path: str) -> "tuple[array, int, int]":
    try:
        with wave.open(path, "rb") as w:
            ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
    except (wave.Error, EOFError, OSError) as e:
        raise AudioError("VALIDATION_ERROR", f"{path}: not a readable PCM WAV ({e})", {"reason": "pcm_unreadable", "path": path})
    if width != 2:
        raise AudioError("VALIDATION_ERROR", f"{path}: {8 * width}-bit samples, expected 16-bit PCM", {"reason": "pcm_width", "path": path})
    a = array("h")
    a.frombytes(raw[: len(raw) - len(raw) % 2])
    if sys.byteorder == "big":
        a.byteswap()
    return a, ch, rate


def _write(path: str, samples: array, channels: int, rate: int) -> None:
    if sys.byteorder == "big":
        samples = array("h", samples)
        samples.byteswap()
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())


def _to_int16(values: List[float]) -> "tuple[array, int]":
    clipped = 0
    out = array("h", bytes(2 * len(values)))
    for i, v in enumerate(values):
        r = int(round(v))
        if r > 32767:
            r, clipped = 32767, clipped + 1
        elif r < -32768:
            r, clipped = -32768, clipped + 1
        out[i] = r
    return out, clipped


def db(power: float) -> float:
    return 10.0 * math.log10(max(power, _FLOOR_POWER))


def pan_gains(pan: float) -> "tuple[float, float]":
    """Constant-power pan law: -1 left, 0 centre (-3.01 dB per side, as ffmpeg-skill's --stereo of a mono track), 1 right."""
    theta = (pan + 1.0) * math.pi / 4.0
    return math.cos(theta), math.sin(theta)


def pan_mono(src: str, dst: str, pan: float) -> Dict[str, Any]:
    """A 1-channel 16-bit WAV to a 2-channel one, placed by `pan` (constant power)."""
    a, ch, rate = _read(src)
    if ch != 1:
        raise AudioError("INVALID_CHANNEL_LAYOUT", f"pan needs a 1-channel input, got {ch} channels", {"channels": ch, "reason": "pan_needs_mono"})
    gl, gr = pan_gains(pan)
    out = array("h", bytes(4 * len(a)))
    out[0::2] = array("h", map(round, map(mul, a, repeat(gl))))
    out[1::2] = array("h", map(round, map(mul, a, repeat(gr))))
    _write(dst, out, 2, rate)
    return {"pan": pan, "left_gain_db": round(20 * math.log10(max(gl, 1e-9)), 3), "right_gain_db": round(20 * math.log10(max(gr, 1e-9)), 3), "samples": len(a)}


def automix(srcs: List[str], dst: str, channels: List[Dict[str, Any]], window_ms: float, max_rate_db_per_s: float,
            hold_below_dbfs: Optional[float] = None) -> Dict[str, Any]:
    """Gain-sharing mix of 1-channel 16-bit WAVs (module docstring). The output has input 0's length; a shorter
    input is padded with silence, a longer one is cut there. Returns the measurements the executor reports."""
    data: List[array] = []
    rate = 0
    for k, src in enumerate(srcs):
        a, ch, r = _read(src)
        if ch != 1:
            raise AudioError("INTERNAL_ERROR", f"AUTOMIX input {k} was not conformed to one channel ({ch})")
        if k == 0:
            rate = r
        elif r != rate:
            raise AudioError("INVALID_REQUEST", f"AUTOMIX inputs must share one sample rate: input 0 is {rate} Hz, input {k} is {r} Hz",
                             {"reason": "automix_sample_rate_mismatch", "input": k, "sample_rate": r, "expected": rate})
        data.append(a)
    total = len(data[0])
    hop = max(1, int(round(rate * HOP_SECONDS)))
    hop_s = hop / rate
    alpha = 1.0 - math.exp(-hop_s / (window_ms / 1000.0))
    step = max_rate_db_per_s / 2.0 * hop_s          # per-hop limit on every level; the gains then move at most twice that
    shared = [k for k, c in enumerate(channels) if not c["pinned"]]
    weights = [10.0 ** (c["weight_db"] / 10.0) for c in channels]
    trims = [10.0 ** (c["gain_db"] / 20.0) for c in channels]
    smooth = [0.0] * len(srcs)
    level: List[Optional[float]] = [None] * len(srcs)
    prev_gain = [trims[k] if k not in shared else 0.0 for k in range(len(srcs))]
    prev_db: List[Optional[float]] = [None] * len(srcs)
    max_rate = 0.0
    sums_db: List[float] = []
    open_hops = [0] * len(srcs)
    held = 0
    hops = 0
    sum_db_gain = [0.0] * len(srcs)
    out: List[float] = []
    for start in range(0, total, hop):
        end = min(total, start + hop)
        n = end - start
        blocks = []
        for a in data:
            b = a[start:end] if start < len(a) else array("h")
            if len(b) < n:
                b = b + array("h", bytes(2 * (n - len(b))))
            blocks.append(b)
        raw = []
        for k in shared:
            ms = sum(map(mul, blocks[k], blocks[k])) / (n * _FULL * _FULL)
            raw.append(ms)
            smooth[k] += alpha * (ms * weights[k] - smooth[k]) if level[k] is not None else ms * weights[k] - smooth[k]
        hold = hold_below_dbfs is not None and all(level[k] is not None for k in shared) and \
            max(db(smooth[k] / weights[k]) for k in shared) < hold_below_dbfs
        if hold:
            held += 1
        else:
            for k in shared:
                want = db(smooth[k])
                cur = level[k]
                level[k] = want if cur is None else min(cur + step, max(cur - step, want))
        lv = [level[k] for k in shared]
        top = max(lv)  # type: ignore[type-var]
        tot = top + 10.0 * math.log10(sum(10.0 ** ((x - top) / 10.0) for x in lv))  # type: ignore[operator]
        sums_db.append(db(sum(10.0 ** ((x - tot) / 10.0) for x in lv)))  # type: ignore[operator]
        acc = [0.0] * n
        for k in range(len(srcs)):
            if k in shared:
                g_db = level[k] - tot  # type: ignore[operator]
                if prev_db[k] is not None:
                    max_rate = max(max_rate, abs(g_db - prev_db[k]) / hop_s)  # type: ignore[operator]
                prev_db[k] = g_db
                sum_db_gain[k] += g_db
                if g_db > -6.0:
                    open_hops[k] += 1
                g = 10.0 ** (g_db / 20.0) * trims[k]
                g0 = prev_gain[k] if hops else g
                q = (g / g0) ** (1.0 / n) if g0 > 0 else 1.0
                ramp = list(accumulate(repeat(q, n - 1), mul, initial=g0 * q))
                prev_gain[k] = g
            else:
                ramp = [trims[k]] * n
            acc = list(map(add, acc, map(mul, blocks[k], ramp)))
        out.extend(acc)
        hops += 1
    pcm, clipped = _to_int16(out)
    _write(dst, pcm, 1, rate)
    return {"sample_rate": rate, "samples": total, "hop_ms": round(hop_s * 1000.0, 6), "hops": hops, "window_ms": window_ms,
            "max_gain_rate_db_per_s": max_rate_db_per_s, "measured_max_gain_rate_db_per_s": round(max_rate, 3),
            "shared_gain_sum_db": {"min": round(min(sums_db), 6), "max": round(max(sums_db), 6)} if sums_db else None,
            "held_ratio": round(held / hops, 6) if hops else 0.0, "hold_below_dbfs": hold_below_dbfs, "clipped_samples": clipped,
            "inputs": [{"input": k, "pinned": channels[k]["pinned"], "gain_db": channels[k]["gain_db"], "weight_db": channels[k]["weight_db"],
                        "mean_share_db": round(sum_db_gain[k] / hops, 3) if k in shared and hops else None,
                        "open_ratio": round(open_hops[k] / hops, 6) if k in shared and hops else None} for k in range(len(srcs))]}
