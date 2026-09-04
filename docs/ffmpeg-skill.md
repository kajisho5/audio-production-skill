# ffmpeg-skill relationship

audio-production-skill is a client of ffmpeg-skill's **public contract** (`ffmpeg-skill contract --json`,
`contract_version 1.0`, verified against ffmpeg-skill 0.9.0 at commit `6b71889`). It never calls `ffmpeg` or
`ffprobe` itself.

## Tools and flags used

| ffmpeg-skill tool | used for | flags emitted |
|---|---|---|
| `ffmpeg-skill/probe` | source facts, every output validation | positional input |
| `ffmpeg-skill/audio` | GAIN, FADE_IN, FADE_OUT, MONO, STEREO, DOWNMIX, NOISE_REDUCTION, MIX, decode-to-WAV, export | `--gain`, `--fade-in`, `--fade-out`, `--mono`, `--stereo`, `--downmix`, `--denoise`, `--denoise-strength`, `--music`, `--music-volume`, `-o`, `--json` |
| `ffmpeg-skill/cut` | TRIM, CUT, SILENCE_REMOVE | `--start`, `--end`, `--segments`, `-o`, `--json` |
| `ffmpeg-skill/loudness` | NORMALIZE and its verification | `-I`, `--tp`, `--lra`, `--sample-rate`, `--measure-only`, `-o`, `--json` |

`doctor` checks that the located ffmpeg-skill declares these tools with `audio_only: true` and that every flag exists
in the tool's generated `input_schema`; a mismatch is a `fail` and `run` refuses with `TOOL_ERROR`
(`ffmpeg_skill_incompatible`).

## Observed behaviour this skill relies on (measured, ffmpeg-skill 0.9.0 / ffmpeg 6.1.1)

- `audio.py` without processing flags re-encodes the first audio stream to the codec of the output extension
  (`.wav` → `pcm_s16le`, `.flac`, `.mp3` → libmp3lame, `.m4a`/`.aac` → aac, `.ogg` → libvorbis, `.opus` → libopus):
  used for decoding, MIX folding and export.
- `audio.py --music X --music-volume G` mixes exactly one bed with `amix=duration=first:normalize=0`; the output
  layout follows the first input; `-shortest` applies. N-way MIX is realised as a pairwise fold.
- `audio.py --mono` uses `pan=mono|c0=0.5*c0+0.5*c1`: on a mono input this halves the level, so MONO is refused
  unless the input has exactly 2 channels.
- `cut.py` stream-copies (`-c copy`); the cut lands on the packet boundary at or after the requested time (measured
  +17 ms on 48 kHz PCM WAV, +15 ms on AAC). `--accurate` re-encodes with AAC even into a `.wav` container
  (verified: `acc.wav` contained an AAC stream), so it is never used.
- `cut.py` on a compressed source copies the compressed packets into the `.wav` container; this skill therefore
  decodes non-PCM inputs to WAV before cutting.
- `loudness.py` refuses silent inputs (`input audio is silent`) → `TOOL_ERROR`; `--measure-only` prints
  `{input_i, input_tp, input_lra, input_thresh, target_offset}` as strings.
- `audio.py` always maps `0:v:0` when the input has video, which the `.wav` muxer rejects → video containers are
  refused at source validation (`video_stream_not_supported`).
- Failure document: `{"status": "failed", "error": {"kind": "input|ffmpeg|missing_tool", "message"}}` with a non-zero
  exit; parsed into `TOOL_ERROR` with `details.error_kind`.

## Compatibility gaps (required capability → not implemented here)

| wanted operation | missing in ffmpeg-skill 0.9 public contract | consequence |
|---|---|---|
| CONCAT of separate audio files | `join` is `video_required`; `cut --segments` only joins ranges of one input | `CONCAT` declared `not_implemented` |
| CHANNEL_MAP (arbitrary mapping / pan) | only `--mono`, `--stereo`, `--downmix` | provided as MONO / STEREO / DOWNMIX; `CHANNEL_MAP` not implemented |
| RESAMPLE (standalone) | `audio.py` has no sample-rate flag; only `loudness.py --sample-rate` | `NORMALIZE.sample_rate`; `RESAMPLE` not implemented; outputs may declare `expect.sample_rate` for verification |
| DYNAMICS (typed compressor / limiter / gate) | only the fixed `--voice` chain | not implemented |
| sample-accurate TRIM / CUT of audio | `--accurate` breaks audio-only outputs | packet-boundary precision, validated within 0.1 s |
| audio extraction from a video container | `audio.py` maps the video stream, `cut.py` copies it | video sources refused |
| MIX per-input pan, more than one bed per call | `--music` takes one file | pairwise fold; no pan |
| 24-bit intermediates | `.wav` → `pcm_s16le` fixed | 16-bit PCM intermediates |
| capability detection of core filters (`volume`, `afade`, `amix`, `pan`, `aformat`) | ffmpeg-skill doctor lists only its own table | reported `unknown`, verified per run |

Each gap is a request to ffmpeg-skill (or a future second backend), not something this skill works around with its
own ffmpeg invocation.
