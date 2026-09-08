# ffmpeg-skill relationship

audio-production-skill is a client of ffmpeg-skill's **public contract** (`ffmpeg-skill contract --json`,
`contract_version 1.0`, verified against ffmpeg-skill 0.9.1 at commit `2abd89c` and again against 0.12.2; versions
below 0.12.0 are refused, see below). It never calls `ffmpeg` or `ffprobe` itself.

## Tools and flags used

| ffmpeg-skill tool | used for | flags emitted |
|---|---|---|
| `ffmpeg-skill/probe` | source facts, every output validation | positional input |
| `ffmpeg-skill/audio` | GAIN, FADE_IN, FADE_OUT, MONO, STEREO, DOWNMIX, NOISE_REDUCTION, DYNAMICS, MIX, audio extraction from video, export | `--gain`, `--fade-in`, `--fade-out`, `--mono`, `--stereo`, `--downmix`, `--denoise`, `--denoise-strength`, `--music`, `--music-volume`, `--gate/--gate-*`, `--compress/--comp-*`, `--limit/--limit-*`, `-o`, `--json` |
| `ffmpeg-skill/cut` | TRIM, CUT, SILENCE_REMOVE | `--start`, `--end`, `--segments`, `--accurate`, `-o`, `--json` |
| `ffmpeg-skill/loudness` | NORMALIZE, verified from that same call's `--json` `result` field | `-I`, `--tp`, `--lra`, `--sample-rate`, `-o`, `--json` |
| `ffmpeg-skill/join` | CONCAT | positional inputs, `--transition none|fade`, `--duration`, `--sample-rate`, `--channels`, `-o`, `--json` |

`doctor` checks that the located ffmpeg-skill declares these tools with `audio_only: true` and that every flag exists
in the tool's generated `input_schema`; a mismatch is a `fail` and `run` refuses with `TOOL_ERROR`
(`ffmpeg_skill_incompatible`).

## Why 0.12.0 is the minimum

Measured on 0.9.0: `cut --accurate` and the keyframe fallback re-encoded audio with AAC even into a `.wav`
container, `cut -c copy` copied compressed packets into `.wav`, `audio.py` always mapped the video stream so a video
container could not yield an audio-only output, `join` required video, there were no typed dynamics, and the doctor
reported every filter missing on FFmpeg ≥ 8. 0.9.1 fixed all of these (its CHANGELOG), and the adapter's minimum was
0.9.1 for a while on that basis. 0.12.0 added the post-normalization measurement (`result`) to `loudness.py`'s
`--json` response for a NORMALIZE call (previously only available via a second `--measure-only` process); this
skill's `_verify_loudness` now reads it directly (ADR-13, docs/decisions.md) instead of re-running `loudness.py`,
so the adapter's version window is `[0.12.0, 1.0.0)`. A checkout in `[0.9.1, 0.12.0)` still runs the two-pass
NORMALIZE fine but its `loudness.py --json` has no `result` field, which this skill would otherwise misread as a
missing measurement — hence the raised floor rather than a feature-detection fallback.

## Observed behaviour this skill relies on (measured, ffmpeg-skill 0.9.1 / ffmpeg 6.1.1, and 0.12.2)

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
