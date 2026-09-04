"""Timeline arithmetic: source time vs. timeline time.

An AudioSegment maps a contiguous timeline range of an artifact to a contiguous range of one source. A track over a
6 s source is one segment  timeline [0, 6) <- source [0, 6). TRIM 1..4 makes  timeline [0, 3) <- source [1, 4).
CUT of [2, 3) from that makes two segments  [0, 1) <- source [1, 2)  and  [1, 3) <- source [3, 4).
Gain, fades, normalisation and channel operations keep the mapping. MIX keeps every input's segments, tagged with
the input index, over the first input's duration. Every number is seconds as a float; ranges are half-open."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .errors import AudioError
from .model import TimeRange

EPS = 1e-6


@dataclass
class AudioSegment:
    timeline_start: float
    timeline_end: float
    source_id: str
    source_start: float
    source_end: float
    input_index: int = 0     # which input of a MIX contributed it (0 for single-input chains)

    @property
    def duration(self) -> float:
        return self.timeline_end - self.timeline_start

    def to_dict(self) -> Dict[str, Any]:
        return {"timeline": {"start": round(self.timeline_start, 6), "end": round(self.timeline_end, 6)},
                "source_id": self.source_id, "source": {"start": round(self.source_start, 6), "end": round(self.source_end, 6)},
                "input_index": self.input_index}


def base_segments(source_id: str, duration: float) -> List[AudioSegment]:
    if duration <= 0:
        raise AudioError("INVALID_INPUT", f"source {source_id!r} has no positive duration", {"source_id": source_id, "duration": duration})
    return [AudioSegment(0.0, duration, source_id, 0.0, duration)]


def total_duration(segments: List[AudioSegment]) -> float:
    return max((s.timeline_end for s in segments), default=0.0)


def keep_ranges(segments: List[AudioSegment], keep: List[TimeRange], where: str = "keep") -> List[AudioSegment]:
    """Keep the given timeline ranges (sorted, non-overlapping), re-packed contiguously from 0. Each kept range must lie
    inside the current timeline (INVALID_TIME_RANGE otherwise)."""
    total = total_duration(segments)
    out: List[AudioSegment] = []
    cursor = 0.0
    for rng in keep:
        if rng.start < -EPS or rng.end > total + EPS:
            raise AudioError("INVALID_TIME_RANGE", f"{where}: range {rng.to_dict()} lies outside the media duration {total:.3f}s",
                             {"start": rng.start, "end": rng.end, "duration": total})
        for seg in segments:
            a, b = max(seg.timeline_start, rng.start), min(seg.timeline_end, rng.end)
            if b - a <= EPS:
                continue
            off_a, off_b = a - seg.timeline_start, b - seg.timeline_start
            out.append(AudioSegment(cursor, cursor + (b - a), seg.source_id, seg.source_start + off_a, seg.source_start + off_b, seg.input_index))
            cursor += b - a
    if not out:
        raise AudioError("INVALID_TIME_RANGE", f"{where}: nothing would remain", {"duration": total})
    return out


def complement(remove: List[TimeRange], total: float, where: str = "remove") -> List[TimeRange]:
    """Timeline ranges that remain after removing `remove` (sorted, non-overlapping) from [0, total)."""
    keep: List[TimeRange] = []
    cursor = 0.0
    for rng in remove:
        if rng.start < -EPS or rng.end > total + EPS:
            raise AudioError("INVALID_TIME_RANGE", f"{where}: range {rng.to_dict()} lies outside the media duration {total:.3f}s",
                             {"start": rng.start, "end": rng.end, "duration": total})
        if rng.start - cursor > EPS:
            keep.append(TimeRange(cursor, rng.start))
        cursor = max(cursor, rng.end)
    if total - cursor > EPS:
        keep.append(TimeRange(cursor, total))
    if not keep:
        raise AudioError("INVALID_TIME_RANGE", f"{where}: removing these ranges would leave nothing", {"duration": total})
    return keep


def apply_silence_rules(ranges: List[TimeRange], margin: float, min_duration: float) -> List[TimeRange]:
    """Shrink each range by `margin` on both sides and drop ranges shorter than `min_duration` (after shrinking).
    Pure arithmetic on caller-provided ranges: no detection happens here."""
    out: List[TimeRange] = []
    for r in ranges:
        a, b = r.start + margin, r.end - margin
        if b - a <= EPS or (b - a) + EPS < min_duration:
            continue
        out.append(TimeRange(a, b))
    return out


def trim(segments: List[AudioSegment], rng: TimeRange, where: str = "trim") -> List[AudioSegment]:
    return keep_ranges(segments, [rng], where)


def cut(segments: List[AudioSegment], remove: List[TimeRange], where: str = "cut") -> List[AudioSegment]:
    return keep_ranges(segments, complement(remove, total_duration(segments), where), where)


def mix(inputs: List[List[AudioSegment]]) -> List[AudioSegment]:
    """MIX output lasts as long as the first input (amix duration=first). Segments of every input are kept, tagged
    with the input index, and clipped to that duration."""
    total = total_duration(inputs[0])
    out: List[AudioSegment] = []
    for idx, segs in enumerate(inputs):
        for s in segs:
            end = min(s.timeline_end, total)
            if end - s.timeline_start <= EPS:
                continue
            out.append(AudioSegment(s.timeline_start, end, s.source_id, s.source_start, s.source_start + (end - s.timeline_start), idx))
    return out


def fade_ranges(segments: List[AudioSegment], fade_in: Optional[float], fade_out: Optional[float]) -> None:
    total = total_duration(segments)
    for name, d in (("FADE_IN", fade_in), ("FADE_OUT", fade_out)):
        if d is not None and d > total + EPS:
            raise AudioError("INVALID_TIME_RANGE", f"{name} duration {d}s exceeds the media duration {total:.3f}s", {"duration": total, "fade": d})
