# ffmpeg-skill relationship

audio-production-skill is a client of ffmpeg-skill's **public contract** (`ffmpeg-skill contract --json`,
`contract_version 1.0`, verified against ffmpeg-skill 0.9.1 at commit `2abd89c`, 0.12.2 at `336e0c4`, and 2.10.3).
There is no version window: compatibility is detected from the located checkout's contract (see below, ADR-15).
It never calls `ffmpeg` or `ffprobe` itself.

## Tools and flags used

| ffmpeg-skill tool | used for | flags emitted |
|---|---|---|
| `ffmpeg-skill/probe` | source facts, every output validation | positional input |
| `ffmpeg-skill/audio` | GAIN, FADE_IN, FADE_OUT, MONO, STEREO, DOWNMIX, NOISE_REDUCTION, DYNAMICS, VOICE_CLEANUP, MIX, audio extraction from video, export | `--gain`, `--fade-in`, `--fade-out`, `--mono`, `--stereo`, `--downmix`, `--denoise`, `--denoise-strength`, `--voice [light\|medium\|strong]`, `--music`, `--music-volume`, `--duck`, `--duck-amount`, `--duck-threshold`, `--duck-attack`, `--duck-release` (MIX `duck`, optional), `--gate/--gate-*`, `--compress/--comp-*`, `--limit/--limit-*`, `-o`, `--json` |
| `ffmpeg-skill/cut` | TRIM, CUT, SILENCE_REMOVE, FILLER_REMOVE | `--start`, `--end`, `--segments`, `--accurate`, `--declick-ms` (FILLER_REMOVE, optional), `-o`, `--json` |
| `ffmpeg-skill/loudness` | NORMALIZE, verified from that same call's `--json` `result` field | `-I`, `--tp`, `--lra`, `--sample-rate`, `-o`, `--json` |
| `ffmpeg-skill/join` | CONCAT | positional inputs, `--transition none|fade`, `--duration`, `--sample-rate`, `--channels`, `-o`, `--json` |

`doctor` checks that the located ffmpeg-skill declares these tools with `audio_only: true` and that every flag exists
in the tool's generated `input_schema`; a mismatch is a `fail` and `run` refuses with `TOOL_ERROR`
(`ffmpeg_skill_incompatible`). That is the whole compatibility check — the version string is reported, never
compared, and a `contract_version` other than `1.x` is a warning.

Optional flags (`adapter.OPTIONAL_FLAGS_USED`), emitted only when the located contract declares them; `doctor`
reports each under `checks.ffmpeg_skill.optional_flags`:

| flag | declared by | used for | without it |
|---|---|---|---|
| `audio --overwrite` | ffmpeg-skill >= 1.10 (which refuses to replace an existing output without it) | export to an existing path when the request set `overwrite: true` | not passed; older releases replace the file anyway and reject the flag |
| `audio --voice <level>` (value form) | ffmpeg-skill >= 1.13 (`voice` declared as a string with `enum` light / medium / strong) | VOICE_CLEANUP `level` passed as `--voice <level>` | `voice` is a boolean switch (0.12.2 … 1.12): only `level: medium` runs, as the bare `--voice` — the same chain byte for byte; `light` / `strong` are refused at planning with `TOOL_ERROR` `voice_level_unsupported` (ADR-16). `doctor` reports `checks.ffmpeg_skill.voice_levels` |
| `audio --duck`, `--duck-amount` | 0.12.2 and later | MIX `duck` (the ducked bed's fold) | a MIX with `duck` is refused at planning, `TOOL_ERROR` `ffmpeg_skill_flag_missing` (ADR-19); a MIX without `duck` never needs them |
| `audio --duck-threshold`, `--duck-attack`, `--duck-release` | 2.x (not 0.12.2, whose sidechain threshold 0.05, attack 20 ms and release 400 ms are fixed) | MIX `duck.threshold_db` / `attack_ms` / `release_ms` when given | refused at planning when given; omitted, ffmpeg-skill's defaults apply |
| `cut --declick-ms` | 2.x (not 0.12.2) | FILLER_REMOVE `declick_ms` (fade on each side of every `--segments` join) | `declick_ms` > 0 refused at planning (ADR-18); 0 or omitted needs nothing (0.12.2 butts the joins) |
| `loudness --measure-only` | every release this skill can run | NORMALIZE verification when the call's `--json` has no `result` (before 0.12.0); the measurement is read from the document's top level, `result` or `measured` (2.x) | `TOOL_ERROR` `loudness_result_missing` if `result` is also absent |

ffmpeg-skill 2.0's other breaking changes do not reach this skill: it emits no `--crf` (renamed `--quality`), imports
nothing from `scripts/_common` (now a package) and does not read probe's `hdr` (meaning changed; `bt2020_or_hdr`
keeps the old one). Result top-level keys are unchanged.

## Why there is no minimum version (ADR-15; previously 0.12.0, ADR-13)

Measured on 0.9.0: `cut --accurate` and the keyframe fallback re-encoded audio with AAC even into a `.wav`
container, `cut -c copy` copied compressed packets into `.wav`, `audio.py` always mapped the video stream so a video
container could not yield an audio-only output, `join` required video, there were no typed dynamics, and the doctor
reported every filter missing on FFmpeg ≥ 8. 0.9.1 fixed all of these (its CHANGELOG). 0.9.0 is still refused, but
by capability detection: it lacks `--audio-stream` and the `--gate/--compress/--limit` flags in `FLAGS_USED`.
0.12.0 added the post-normalization measurement (`result`) to `loudness.py`'s `--json` response, which
`_verify_loudness` reads (ADR-13); for an older release whose response has no `result` the output is measured with
`--measure-only` instead, so `[0.9.1, 0.12.0)` no longer needs a floor. There is no upper bound: 1.x and 2.x are
verified to keep every tool and flag this skill emits, and CI runs against ffmpeg-skill `main` so the next release
is checked as soon as it lands.

## Observed behaviour this skill relies on (measured, ffmpeg-skill 0.9.1 / ffmpeg 6.1.1, 0.12.2, and 2.10.3)

- `audio.py` without processing flags re-encodes the first audio stream to the codec of the output extension
  (`.wav` → `pcm_s16le`, `.flac`, `.mp3` → libmp3lame, `.m4a`/`.aac` → aac, `.ogg` → libvorbis, `.opus` → libopus):
  used for decoding, MIX folding and export.
- `audio.py --music X --music-volume G` mixes exactly one bed with `amix=duration=first:normalize=0`; the output
  layout follows the first input; `-shortest` applies. N-way MIX is realised as a pairwise fold.
- `audio.py --mono` uses `pan=mono|c0=0.5*c0+0.5*c1`: on a mono input this halves the level, so MONO is refused
  unless the input has exactly 2 channels.
- `cut.py --accurate` on audio trims at the sample (`atrim`) and encodes to the codec of the output extension;
  measured `precision: sample`, `duration_error_ms: 0.0` on WAV, AAC and video-container sources with ffmpeg 6.1;
  on the Windows CI runner's FFmpeg an AAC source came out 12 ms short (decoder priming), PCM exact. This skill always
  passes `--accurate` and records `precision` / `duration_error_ms` / `reencoded` in `measurements.cut`.
- `join.py` with audio-only inputs concatenates at one sample rate (first clip's or `--sample-rate`) and one layout
  (widest or `--channels`), with `acrossfade` (`--transition fade --duration`) or a butt join (`--transition none`);
  audio and video inputs cannot be mixed, so video-container sources are extracted first.
- `audio.py --gate/--compress/--limit` build `agate`, `acompressor`, `alimiter` from range-checked numbers (dB
  converted to linear by ffmpeg-skill); order gate → compressor → limiter.
- `audio.py <video> -o x.wav` drops the picture and extracts the first audio track; this is how SOURCE_TRACK nodes
  of video containers are materialised.
- `loudness.py` refuses silent inputs (`input audio is silent`) → `TOOL_ERROR`; a normal (non-`--measure-only`)
  call's `--json` response includes a `result` field (`{input_i, input_tp, input_lra, input_thresh, target_offset,
  silent}`, ffmpeg-skill >= 0.12.0) with the post-normalization measurement, which `_verify_loudness` reads directly.
  `--measure-only --json` prints the same keys at the top level (0.x) or under `measured` (2.x).
- `audio.py -o <existing file>`: replaced silently before 1.10; refused (`kind: input`) from 1.10 / 2.0 unless
  `--overwrite` is passed, a flag the older releases reject.
- Failure document: `{"status": "failed", "error": {"kind": "input|ffmpeg|missing_tool", "message"}}` with a non-zero
  exit; parsed into `TOOL_ERROR` with `details.error_kind`.

## Compatibility gaps (required capability → not implemented here)

| wanted operation | missing in ffmpeg-skill 0.9 public contract | consequence |
|---|---|---|
| CHANNEL_MAP (arbitrary mapping / pan) | only `--mono`, `--stereo`, `--downmix` | provided as MONO / STEREO / DOWNMIX; `CHANNEL_MAP` not implemented |
| RESAMPLE (standalone) | `audio.py` has no sample-rate flag; only `loudness.py --sample-rate` and `join.py --sample-rate` | `NORMALIZE.sample_rate`, `CONCAT.sample_rate`; `RESAMPLE` not implemented; outputs may declare `expect.sample_rate` for verification |
| MIX per-input pan, more than one bed per call | `--music` takes one file | pairwise fold; no pan |
| 24-bit intermediates | `.wav` → `pcm_s16le` fixed | 16-bit PCM intermediates |
| capability detection of core filters (`volume`, `afade`, `amix`, `pan`, `aformat`) | ffmpeg-skill doctor lists only its own table | reported `unknown`, verified per run |
| filter detection on FFmpeg ≥ 8.0 | ffmpeg-skill 0.9 `_ff_list` matches `[TSC.]{3}`, FFmpeg 8 prints `%c%c` (two flags) → every filter "missing" (observed on macOS CI, brew ffmpeg 8) | when the doctor reports ffmpeg but zero filters, all filter capabilities are `unknown`, not `unsupported`; execution proceeds and output validation decides |

Each gap is a request to ffmpeg-skill (or a future second backend), not something this skill works around with its
own ffmpeg invocation.

Audio stream selection is no longer a gap: `--audio-stream` has existed on `audio.py` since 0.9.1, and 0.3.0 adds an
`audio_stream` parameter (0-based, default 0) to `GAIN`, `FADE_IN`, `FADE_OUT`, `MONO`, `STEREO`, `DOWNMIX`,
`NOISE_REDUCTION` and `DYNAMICS` — every operation whose one input is read directly by `audio.py`. `TRIM` / `CUT` /
`SILENCE_REMOVE` (`cut.py`), `NORMALIZE` (`loudness.py`) and `CONCAT` (`join.py`) still always use stream 0: those
tools deliberately exclude `--audio-stream` (0.12.0 CHANGELOG: combining separate files is "a different problem
shape"). `MIX` also always uses stream 0 of each input, for the same reason — it folds already-resolved inputs
through `--music`, which reads stream 0 of the bed file regardless of `--audio-stream` on the main input.

## `--duck` (MIX) and `cut --declick-ms` (FILLER_REMOVE), read 2026-10-31

From `scripts/audio.py` / `scripts/cut.py` and `_contract.py --json` at 0.12.2 (`336e0c4`) and 2.22.4 (`bb51b28`):

- `--music B --duck`: `[main]asplit[mainA][sc]; [music][sc]sidechaincompress=threshold=<lin>:ratio=max(2, amount/3):
  attack=<ms>:release=<ms>:makeup=1[ducked]; [mainA][ducked]amix=inputs=2:duration=first:normalize=0`. 0.12.2 fixes
  threshold 0.05 (−26.02 dBFS), attack 20, release 400; 2.x exposes them (`--duck-threshold` in dBFS, converted to
  linear) and range-checks all four (amount 0..60, threshold −60..0, attack 0.01..2000, release 0.01..9000) before
  ffmpeg runs. `--duck` without `--music` is refused by audio.py; `--effects F` is a further `volume` + `amix
  normalize=0` that is never ducked — the same graph as one more `--music` fold (ADR-19).
- Measured with the integration fixtures (key: 1 kHz at amplitude 0.5 only 2..5 s; bed: 440 Hz at 0.1), `amount_db`
  24 on both 0.12.2 and 2.22.4: the bed's 440 Hz band under the key drops by more than 6 dB against the un-ducked mix
  and is within 1.5 dB of it before the key.
- `cut --segments ... --accurate --declick-ms N`: an `afade` out / in of N ms on each side of every join the
  re-encode makes (default 20, `--no-declick` / `0` = butt); duration and sample count are unchanged (the fixture keeps
  exactly 244800 samples with `declick_ms` 15). The `--json` response reports the applied `declick_ms`. 0.12.2 has no
  declick at all.

## `--voice` (VOICE_CLEANUP), measured 2026-10-30

Read from `scripts/audio.py` and `_contract.py --json --static` at 0.12.2 (`336e0c4`) and 2.21.0:

| level | chain (`VOICE_LEVELS` in audio.py) | 0.12.2 | 2.21.0 |
|---|---|---|---|
| light | `highpass=f=80,acompressor=threshold=-18dB:ratio=2:attack=5:release=80:makeup=1` | – | yes |
| medium | `highpass=f=80,deesser=i=0.4,afftdn=nf=-25:tn=1,acompressor=threshold=-18dB:ratio=3:attack=5:release=80:makeup=2` | bare `--voice` | yes |
| strong | medium + `deesser=i=0.6,acompressor=threshold=-24dB:ratio=4:attack=5:release=120:makeup=3,alimiter=limit=0.891251:level=disabled` | – | yes |

- The chains take no parameters: `--voice` has no strength, threshold or frequency flag, so VOICE_CLEANUP has only
  `level` (and `audio_stream`).
- Under `--voice`, `--denoise` / `--denoise-strength` are ignored (`elif` in audio.py); VOICE_CLEANUP never emits them.
- afftdn's `nf` is only valid in [−80, −20] dB (FFmpeg 6.1 refuses `nf=-15` with "Numerical result out of range").
  The voice chains use a fixed `nf=-25`, so VOICE_CLEANUP cannot hit that limit; the integration test asserts every
  observed `afftdn=nf=` value lies in range. `--denoise-strength` maps to `nf=-<strength>` and ffmpeg-skill accepts
  10..60, so a NOISE_REDUCTION `strength_db` below 20 would reach ffmpeg out of range; NOISE_REDUCTION therefore
  declares `strength_db` 20..60 and refuses 10..19.99 at validation with the reason (ADR-17).
