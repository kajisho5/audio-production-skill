"""FILLER_REMOVE arithmetic: caller-supplied word timings + an explicit lexicon -> removal ranges (ADR-18).

Pure functions, no I/O, no speech recognition, no audio analysis. The words and their times come from the request
(typically transcription-skill's transcript word timestamps); the lexicon is chosen by the caller (named presets
and / or explicit words). A word is removed only where the caller's timings say it is. Matching is deterministic:

  1. every token is normalised (NFKC, case-folded, whitespace and punctuation dropped, the long-vowel marks
     ー / 〜 / ~ unified and collapsed, so "えーー、" and "えー" are the same token; "Um," and "um" too);
  2. a run of up to MAX_RUN_TOKENS adjacent words whose joined normal form is in the lexicon is one filler; the
     longest run wins (speech engines split Japanese into sub-word tokens: "えーと" may arrive as "えー" + "と");
     words of a run must touch (gap <= RUN_MAX_GAP s), so two separate words are never read as one;
  3. each filler becomes [first.start - pad, last.end + pad], the padding never reaching into the neighbouring
     word (and never beyond [0, duration]);
  4. consecutive fillers with no other word between them are removed as one range when the gap between them is at
     most merge_gap (so the breath between "えー" and "あのー" goes too); a gap with a real word in it never merges.
"""
from __future__ import annotations

import unicodedata
from typing import Any, Dict, FrozenSet, Iterable, List, Sequence, Tuple

# Named lexicons a caller may select (`lexicon.presets`). Only hesitation sounds: words that are also ordinary words
# in running speech ("あの" as in "あの人", "その", "まあ", "なんか", "like", "you know") are left out on purpose -- cutting
# them cuts real sentences. A caller who wants them adds them through `lexicon.words`.
LEXICON_PRESETS: Dict[str, Tuple[str, ...]] = {
    "ja": ("えー", "えーと", "えーっと", "えっと", "えと", "あー", "あのー", "そのー", "うーん", "んー", "ええと"),
    "en": ("um", "umm", "uh", "uhh", "uhm", "erm", "er", "hmm", "mm", "ah"),
}
MAX_RUN_TOKENS = 3
RUN_MAX_GAP = 0.05
_LONG_VOWEL = "ー"
_LONG_VOWEL_VARIANTS = {"〜": _LONG_VOWEL, "~": _LONG_VOWEL, "～": _LONG_VOWEL, "ｰ": _LONG_VOWEL}


def normalise(token: str) -> str:
    """Canonical form used for lexicon matching (both sides are normalised the same way)."""
    s = unicodedata.normalize("NFKC", token).casefold()
    out: List[str] = []
    for ch in s:
        ch = _LONG_VOWEL_VARIANTS.get(ch, ch)
        if ch == _LONG_VOWEL:
            if out and out[-1] != _LONG_VOWEL:
                out.append(ch)
            continue
        cat = unicodedata.category(ch)
        if ch.isspace() or cat.startswith("P") or cat.startswith("Z") or cat.startswith("C"):
            continue
        out.append(ch)
    return "".join(out)


def effective_lexicon(presets: Sequence[str], words: Sequence[str], keep: Sequence[str]) -> FrozenSet[str]:
    """Normal forms of presets + words, minus keep."""
    chosen = {normalise(w) for p in presets for w in LEXICON_PRESETS[p]} | {normalise(w) for w in words}
    chosen -= {normalise(w) for w in keep}
    chosen.discard("")
    return frozenset(chosen)


def match_fillers(words: Sequence[Dict[str, Any]], lexicon: Iterable[str]) -> List[Tuple[int, int]]:
    """Index runs [a, b) of `words` (sorted by start) that are fillers. Longest run first, left to right, no overlap."""
    lex = frozenset(lexicon)
    norms = [normalise(str(w["text"])) for w in words]
    out: List[Tuple[int, int]] = []
    i, n = 0, len(words)
    while i < n:
        hit = 0
        for k in range(min(MAX_RUN_TOKENS, n - i), 0, -1):
            if any(words[j + 1]["start"] - words[j]["end"] > RUN_MAX_GAP for j in range(i, i + k - 1)):
                continue
            joined = "".join(norms[i:i + k])
            if joined and joined in lex:
                hit = k
                break
        if hit:
            out.append((i, i + hit))
            i += hit
        else:
            i += 1
    return out


def removal_ranges(words: Sequence[Dict[str, Any]], runs: Sequence[Tuple[int, int]], pad: float, merge_gap: float,
                   duration: float) -> List[Tuple[float, float]]:
    """Ranges (start, end) to remove for the matched runs, padded and merged as described in the module docstring.
    Sorted and non-overlapping; empty ranges (a word wholly past `duration`) are dropped."""
    ranges: List[Tuple[float, float, int, int]] = []
    n = len(words)
    for a, b in runs:
        start, end = float(words[a]["start"]), float(words[b - 1]["end"])
        lo = float(words[a - 1]["end"]) if a > 0 else 0.0
        hi = float(words[b]["start"]) if b < n else duration
        s = max(start - pad, lo) if lo <= start else start
        e = min(end + pad, hi) if hi >= end else end
        s, e = max(0.0, s), min(duration, e)
        if e - s > 1e-6:
            ranges.append((s, e, a, b))
    merged: List[Tuple[float, float, int, int]] = []
    for s, e, a, b in ranges:
        if merged:
            ps, pe, pa, pb = merged[-1]
            if s <= pe + 1e-9 or (pb == a and s - pe <= merge_gap + 1e-9):
                merged[-1] = (ps, max(pe, e), pa, b)
                continue
        merged.append((s, e, a, b))
    return [(round(s, 6), round(e, 6)) for s, e, _, _ in merged]
