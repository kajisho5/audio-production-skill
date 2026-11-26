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


class _Reader:
    """A 16-bit PCM WAV read in blocks (never the whole file in memory)."""

    def __init__(self, path: str):
        try:
            self.w = wave.open(path, "rb")
        except (wave.Error, EOFError, OSError) as e:
            raise AudioError("VALIDATION_ERROR", f"{path}: not a readable PCM WAV ({e})", {"reason": "pcm_unreadable", "path": path})
        self.channels, self.rate, self.frames = self.w.getnchannels(), self.w.getframerate(), self.w.getnframes()
        if self.w.getsampwidth() != 2:
            self.w.close()
            raise AudioError("VALIDATION_ERROR", f"{path}: {8 * self.w.getsampwidth()}-bit samples, expected 16-bit PCM", {"reason": "pcm_width", "path": path})

    def read(self, frames: int) -> array:
        """Up to `frames` frames (interleaved), zero-padded to exactly that many once the file has ended."""
        raw = self.w.readframes(frames)
        a = array("h")
        a.frombytes(raw[: len(raw) - len(raw) % 2])
        if sys.byteorder == "big":
            a.byteswap()
        want = frames * self.channels
        if len(a) < want:
            a.extend(array("h", bytes(2 * (want - len(a)))))
        return a

    def close(self) -> None:
        self.w.close()


class _Writer:
    def __init__(self, path: str, channels: int, rate: int):
        self.w = wave.open(path, "wb")
        self.w.setnchannels(channels)
        self.w.setsampwidth(2)
        self.w.setframerate(rate)
        self.clipped = 0

    def write(self, values: "List[float] | array") -> None:
        out = array("h", bytes(2 * len(values)))
        clipped = 0
        for i, v in enumerate(values):
            r = int(round(v))
            if r > 32767:
                r, clipped = 32767, clipped + 1
            elif r < -32768:
                r, clipped = -32768, clipped + 1
            out[i] = r
        self.clipped += clipped
        if sys.byteorder == "big":
            out.byteswap()
        self.w.writeframes(out.tobytes())

    def close(self) -> None:
        self.w.close()


def db(power: float) -> float:
    return 10.0 * math.log10(max(power, _FLOOR_POWER))


def pan_gains(pan: float) -> "tuple[float, float]":
    """Constant-power pan law: -1 left, 0 centre (-3.01 dB per side, as ffmpeg-skill's --stereo of a mono track), 1 right."""
    theta = (pan + 1.0) * math.pi / 4.0
    return math.cos(theta), math.sin(theta)


def pan_mono(src: str, dst: str, pan: float) -> Dict[str, Any]:
    """A 1-channel 16-bit WAV to a 2-channel one, placed by `pan` (constant power), one second at a time."""
    r = _Reader(src)
    try:
        if r.channels != 1:
            raise AudioError("INVALID_CHANNEL_LAYOUT", f"pan needs a 1-channel input, got {r.channels} channels", {"channels": r.channels, "reason": "pan_needs_mono"})
        gl, gr = pan_gains(pan)
        w = _Writer(dst, 2, r.rate)
        try:
            done = 0
            while done < r.frames:
                n = min(r.rate, r.frames - done)
                a = r.read(n)
                out = [0.0] * (2 * n)
                out[0::2] = map(mul, a, repeat(gl))
                out[1::2] = map(mul, a, repeat(gr))
                w.write(out)
                done += n
        finally:
            w.close()
    finally:
        r.close()
    return {"pan": pan, "left_gain_db": round(20 * math.log10(max(gl, 1e-9)), 3), "right_gain_db": round(20 * math.log10(max(gr, 1e-9)), 3), "samples": r.frames}


def automix(srcs: List[str], dst: str, channels: List[Dict[str, Any]], window_ms: float, max_rate_db_per_s: float,
            hold_below_dbfs: Optional[float] = None) -> Dict[str, Any]:
    """Gain-sharing mix of 1-channel 16-bit WAVs (module docstring). The output has input 0's length; a shorter
    input is padded with silence, a longer one is cut there. Returns the measurements the executor reports."""
    readers: List[_Reader] = []
    try:
        for k, src in enumerate(srcs):
            readers.append(_Reader(src))
            if readers[k].channels != 1:
                raise AudioError("INTERNAL_ERROR", f"AUTOMIX input {k} was not conformed to one channel ({readers[k].channels})")
            if readers[k].rate != readers[0].rate:
                raise AudioError("INVALID_REQUEST", f"AUTOMIX inputs must share one sample rate: input 0 is {readers[0].rate} Hz, input {k} is {readers[k].rate} Hz",
                                 {"reason": "automix_sample_rate_mismatch", "input": k, "sample_rate": readers[k].rate, "expected": readers[0].rate})
        return _automix(readers, dst, channels, window_ms, max_rate_db_per_s, hold_below_dbfs)
    finally:
        for r in readers:
            r.close()


def _automix(readers: List[_Reader], dst: str, channels: List[Dict[str, Any]], window_ms: float, max_rate_db_per_s: float,
             hold_below_dbfs: Optional[float]) -> Dict[str, Any]:
    count = len(readers)
    rate, total = readers[0].rate, readers[0].frames
    hop = max(1, int(round(rate * HOP_SECONDS)))
    tau = window_ms / 1000.0
    shared = [k for k, c in enumerate(channels) if not c["pinned"]]
    weights = [10.0 ** (c["weight_db"] / 10.0) for c in channels]
    trims = [10.0 ** (c["gain_db"] / 20.0) for c in channels]
    smooth = [0.0] * count
    level: List[Optional[float]] = [None] * count
    prev_gain = [0.0] * count
    prev_db: List[Optional[float]] = [None] * count
    open_hops, sum_db_gain = [0] * count, [0.0] * count
    sums_db: List[float] = []
    max_rate, held, hops = 0.0, 0, 0
    out: List[float] = []
    writer = _Writer(dst, 1, rate)
    try:
        for start in range(0, total, hop):
            n = min(total, start + hop) - start
            hop_s = n / rate                              # the last hop may be shorter: its budget and its measured rate use its own length
            alpha = 1.0 - math.exp(-hop_s / tau)
            step = max_rate_db_per_s / 2.0 * hop_s        # per-hop limit on every level; the gains then move at most twice that
            blocks = [r.read(n) for r in readers]
            for k in shared:
                ms = sum(map(mul, blocks[k], blocks[k])) / (n * _FULL * _FULL) * weights[k]
                smooth[k] = ms if level[k] is None else smooth[k] + alpha * (ms - smooth[k])
            if hold_below_dbfs is not None and all(level[k] is not None for k in shared) and \
                    max(db(smooth[k] / weights[k]) for k in shared) < hold_below_dbfs:
                held += 1                                 # everything quiet: the levels, and so the gains, hold
            else:
                for k in shared:
                    want, cur = db(smooth[k]), level[k]
                    level[k] = want if cur is None else min(cur + step, max(cur - step, want))
            lv = [float(level[k]) for k in shared]  # type: ignore[arg-type]
            top = max(lv)
            tot = top + 10.0 * math.log10(sum(10.0 ** ((x - top) / 10.0) for x in lv))
            sums_db.append(db(sum(10.0 ** ((x - tot) / 10.0) for x in lv)))
            acc = [0.0] * n
            for k in range(count):
                if k in shared:
                    g_db = float(level[k]) - tot  # type: ignore[arg-type]
                    last = prev_db[k]
                    if last is not None:
                        max_rate = max(max_rate, abs(g_db - last) / hop_s)
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
            if len(out) >= rate:                          # written about once a second: memory stays bounded
                writer.write(out)
                out.clear()
            hops += 1
        if out:
            writer.write(out)
    finally:
        writer.close()
    return {"sample_rate": rate, "samples": total, "hop_ms": round(hop / rate * 1000.0, 6), "hops": hops, "window_ms": window_ms,
            "max_gain_rate_db_per_s": max_rate_db_per_s, "measured_max_gain_rate_db_per_s": round(max_rate, 3),
            "shared_gain_sum_db": {"min": round(min(sums_db), 6), "max": round(max(sums_db), 6)} if sums_db else None,
            "held_ratio": round(held / hops, 6) if hops else 0.0, "hold_below_dbfs": hold_below_dbfs, "clipped_samples": writer.clipped,
            "inputs": [{"input": k, "pinned": channels[k]["pinned"], "gain_db": channels[k]["gain_db"], "weight_db": channels[k]["weight_db"],
                        "mean_share_db": round(sum_db_gain[k] / hops, 3) if k in shared and hops else None,
                        "open_ratio": round(open_hops[k] / hops, 6) if k in shared and hops else None} for k in range(count)]}
