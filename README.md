# audio-production-skill

Deterministic audio **production / processing** Skill for the AI Video Production Ecosystem: gain, sample-accurate
trim / cut, silence removal (explicit ranges), fades, EBU R128 loudness normalisation, mix, concat, mono / stereo /
surround down-mix, FFT noise reduction, typed dynamics (gate / compressor / limiter), format conversion and audio
extraction from video containers, executed as a typed **operation graph** through
[ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill), with validated artifacts and provenance out.

**audio-production-skill is NOT an AI agent.** It contains no LLM, no prompt, no reasoning, no decision, no
production plan. It executes what a typed request says, refuses everything else, and reports what it observed.

```text
audio-production skill --json              # Skill / Capability / Tool contract (alias: contract --json)
audio-production doctor --json             # environment vs. contract: ffmpeg-skill, ffmpeg, capabilities per operation
audio-production validate - --json         # validate a request document, run nothing, read no media
audio-production plan - --json             # dry run: graph, tool selection, expected timeline; writes no media
audio-production run - --json              # execute (stdin: request document; stdout: exactly one response document)
```

Requirements: Python 3.9+, standard library only; an **ffmpeg-skill** checkout (0.9.1 ≤ version < 1.0, contract 1.0;
0.9.0 is refused because its audio cuts could write AAC packets into `.wav`) and FFmpeg (`ffmpeg` + `ffprobe`) on
PATH for ffmpeg-skill. Install: `pip install -e .`

## What it is, and what it is not

| | [ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) | [media-analysis-skill](https://github.com/kajisho5/media-analysis-skill) | **audio-production-skill** | [video-production-agent](https://github.com/kajisho5/video-production-agent) |
|---|---|---|---|---|
| Role | low-level media execution engine (hands) | measurement / observation (meters) | **audio production execution** (typed audio operations) | reasoning / decision / planning / orchestration (brain) |
| Does | runs ffmpeg for cut, audio post, loudness, join, export, … | measures loudness, silence, streams, integrity | executes GAIN / TRIM / CUT / SILENCE_REMOVE / FADE / NORMALIZE / MIX / CONCAT / MONO / STEREO / DOWNMIX / NOISE_REDUCTION / DYNAMICS as a dependency graph, validates every artifact, records provenance | decides *whether* and *what* to process, builds the request |
| Never | holds a project model | edits or writes media | measures for decisions, decides which ranges are silence, invents parameters, runs ffmpeg directly, accepts commands / filters | runs ffmpeg |

- **audio-production-skill ≠ media-analysis-skill.** media-analysis-skill says "silence from 0.0 to 2.0 s, −23.1 LUFS".
  audio-production-skill only executes "remove [0.0, 2.0)", "normalise to −16 LUFS / −1.5 dBTP". The one measurement
  this skill performs is the verification of its own NORMALIZE output (re-measured through ffmpeg-skill/loudness);
  it never reports a measurement of an *input* for a decision.
- **audio-production-skill ≠ ffmpeg-skill.** ffmpeg-skill runs FFmpeg per script call. audio-production-skill owns the
  *audio project model*: sources, tracks, operation graph with deterministic identities, source ↔ timeline mapping,
  intermediate management, output validation and provenance. It never calls `ffmpeg` itself: every process it starts
  is `python3 <ffmpeg-skill>/scripts/{probe,audio,cut,loudness,join}.py` with a typed argv.
- **audio-production-skill ≠ video-production-agent.** There is no Observation → Inference → Decision → ProductionPlan
  here. The agent decides; this skill executes one typed request and says exactly what happened.

## Architecture

```text
external caller (video-production-agent adapter)
   │  JSON request  audio-production/request@1   (stdin)
   ▼
audio-production run - --json
   ├─ model.parse_request        typed validation: schema, ids, references, parameter schemas per operation type,
   │                             forbidden fields (command / argv / filter / …), formats, layouts, sample rates
   ├─ security.PathPolicy        inputs are regular files (symlinks resolved, optional allowed roots);
   │                             outputs inside the workspace, never an input, never an existing file unless overwrite
   ├─ graph.OperationGraph       nodes (tracks, implicit track range / gain, operations), deterministic topological
   │                             order, cycle / unreachable detection
   ├─ adapter.FfmpegSkill.probe  every source probed (read-only) and sha256-fingerprinted; a video container's audio
   │                             track is extracted to a PCM WAV intermediate (ffmpeg-skill/audio)
   ├─ graph.identities           operation_id = sha256(type, parameters, input identities, tool versions)
   ├─ plan                       tool per node, argv template, expected timeline (segments) and duration
   ├─ execute (in order)         ffmpeg-skill/{audio,cut,loudness,join} → PCM WAV intermediate, reused when the identity
   │                             and input hashes match a manifest; sidecars and partial files removed on failure
   ├─ validate                   exists, size > 0, readable, audio stream, codec, duration ± tolerance, channels,
   │                             sample rate, sha256; NORMALIZE re-measured (loudness / true peak vs. tolerance)
   ├─ export outputs             ffmpeg-skill/audio to the requested format, validated against `expect`
   ▼
JSON response  audio-production/response@1   (stdout, exactly one document; stderr = diagnostics)
```

Full description: [docs/architecture.md](docs/architecture.md).

## Skill / Capability / Tool

- **Skill**: `audio-production` (this package), kind `execution`, one tool `audio-production/run`.
- **Capabilities** (vocabulary of video-production-agent's CapabilityResolver): `ffmpeg-skill`, `ffmpeg`, `ffprobe`,
  `filter:<name>`, `encoder:<name>`. `doctor` reports each as `supported`, `unsupported` or `unknown` (core ffmpeg
  filters are not probed by ffmpeg-skill's doctor and are therefore reported `unknown`, then verified per run).
- **Tools used** (all through ffmpeg-skill's public contract 1.0): `ffmpeg-skill/probe`, `ffmpeg-skill/audio`,
  `ffmpeg-skill/cut`, `ffmpeg-skill/loudness`, `ffmpeg-skill/join`. Which flags are used per tool is listed in `skill --json` →
  `ffmpeg_skill.flags_used` and checked against the live ffmpeg-skill contract by `doctor`.

## Contract

`audio-production skill --json` prints `audio-production/contract@1`: stable identifiers `skill_id`, `version`,
`tools[].tool_id`, `operations[].type`, `operations[].parameters`, `operations[].required_capabilities`,
`unsupported_operations`, `output_formats`, `errors.codes`, `errors.exit_codes`, `schema_versions`. Everything is
derived from the tables the code runs on; there are no placeholder operations.

### Input schema (`audio-production/request@1`)

```json
{
  "schema": "audio-production/request@1",
  "project": {
    "project_id": "talk-42",
    "sources": [{"source_id": "mic", "path": "rec/mic.wav"}, {"source_id": "room", "path": "rec/room.wav"}],
    "tracks": [
      {"track_id": "voice", "source_id": "mic", "channel_layout": "mono"},
      {"track_id": "ambience", "source_id": "room", "range": {"start": 0.5, "end": 3600.0}, "gain_db": -12}
    ],
    "operations": [
      {"op_id": "trim", "type": "TRIM", "inputs": ["track:voice"], "parameters": {"start": 12.0, "end": 3612.0}},
      {"op_id": "cleanup", "type": "SILENCE_REMOVE", "inputs": ["op:trim"],
       "parameters": {"ranges": [{"start": 0.0, "end": 2.1}], "margin": 0.15, "min_duration": 0.6, "threshold_db": -40}},
      {"op_id": "nr", "type": "NOISE_REDUCTION", "inputs": ["op:cleanup"], "parameters": {"mode": "fft", "strength_db": 20}},
      {"op_id": "mix", "type": "MIX", "inputs": ["op:nr", "track:ambience"], "parameters": {"levels": [{"gain_db": 0}, {"gain_db": -6}]}},
      {"op_id": "fade", "type": "FADE_OUT", "inputs": ["op:mix"], "parameters": {"duration": 1.5}},
      {"op_id": "master", "type": "NORMALIZE", "inputs": ["op:fade"],
       "parameters": {"target_lufs": -16.0, "true_peak_db": -1.5, "tolerance_lufs": 1.0, "profile": "podcast"}}
    ],
    "outputs": [
      {"output_id": "master", "operation": "op:master", "path": "deliver/talk-42.wav", "format": "wav",
       "expect": {"channels": 1, "sample_rate": 48000}},
      {"output_id": "preview", "operation": "op:master", "path": "deliver/talk-42.m4a", "format": "m4a"}
    ]
  },
  "options": {"reuse_intermediates": true, "timeout": 900}
}
```

- `sources`: files. `tracks`: generic tracks over a source (label, expected `channel_layout`, optional source
  `range` and `gain_db`, materialised as implicit TRIM / GAIN nodes). `operations`: the graph; inputs are references
  `track:<id>` / `op:<id>`. `outputs`: terminal artifacts with format and expectations.
- Every id matches `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`; times are seconds as numbers; ranges are half-open.
- Fields named `command`, `argv`, `args`, `cmd`, `shell`, `exec`, `executable`, `script`, `filter`, `filter_complex`,
  `af`, `env`, `cwd` are rejected anywhere in the document; unknown fields are rejected everywhere.
- **Loudness**: `target_lufs` and `true_peak_db` are required and have **no default**; the profile / caller decides.
  `loudness_range_lu`, `tolerance_lufs` (verification), `sample_rate`, `profile` (label, recorded only) are optional.
- **Silence**: `SILENCE_REMOVE` executes the caller's ranges with `margin` / `min_duration` arithmetic and records
  `threshold_db`; it never detects silence and never decides that a range is unwanted.

### Output schema (`audio-production/response@1`)

```json
{
  "schema": "audio-production/response@1", "skill": {"id": "audio-production", "version": "0.1.0"},
  "ok": true, "status": "ok", "dry_run": false,
  "plan": {"plan_id": "<sha256>", "graph": {"order": ["track:voice", "op:trim", "..."]}, "steps": ["..."], "required_capabilities": ["..."], "tool_versions": {"ffmpeg-skill": "0.9.0", "ffmpeg": "6.1.1"}},
  "results": [{
    "node_id": "op:master", "operation_id": "<sha256>", "type": "NORMALIZE", "tool": "ffmpeg-skill/loudness", "status": "completed",
    "parameters": {"target_lufs": -16.0, "true_peak_db": -1.5, "tolerance_lufs": 1.0, "profile": "podcast"},
    "inputs": ["op:fade"], "input_hashes": ["<sha256>"],
    "segments": [{"timeline": {"start": 0.0, "end": 3597.9}, "source_id": "mic", "source": {"start": 14.1, "end": 3612.0}, "input_index": 0}, "..."],
    "artifact": {"path": ".../.audio-production/talk-42/<id16>.wav", "duration": 3597.9, "channels": 1, "sample_rate": 48000, "codec": "pcm_s16le", "size": 0, "sha256": "<sha256>"},
    "measurements": {"loudness": {"measured_by": "ffmpeg-skill/loudness --measure-only", "integrated_lufs": -16.0, "true_peak_dbtp": -1.6, "loudness_range_lu": 6.2}},
    "tool_commands_observed": ["ffmpeg ..."], "seconds": 12.3
  }],
  "outputs": [{"output_id": "master", "status": "completed", "path": ".../deliver/talk-42.wav", "format": "wav", "artifact": {"sha256": "<sha256>"}, "segments": ["..."],
               "provenance": {"skill": "audio-production", "skill_version": "0.1.0", "tool": "ffmpeg-skill/audio", "tool_versions": {}, "output_hash": "<sha256>",
                              "operation_id": "<sha256>", "operations": ["output -> operation -> ... -> source, with hashes and status"], "sources": {"mic": {"sha256": "<sha256>"}}}}],
  "tool_runs": [{"tool": "ffmpeg-skill/probe", "exit_code": 0, "seconds": 0.1, "commands_observed": []}],
  "warnings": []
}
```

Failure (any stage) is the same document shape with `"ok": false`, `"status": "error" | "cancelled"` and
`"error": {"code", "message", "retryable", "details"}`; the per-node `results` show which node failed and which
were skipped. `ok` mirrors the process exit code (0 ⇔ `ok`); `status` follows the media-analysis-skill convention.

## Supported operations

| type | inputs | parameters | ffmpeg-skill tool | notes |
|---|---|---|---|---|
| `GAIN` | 1 | `gain_db` [−60, 60] | `audio --gain` | |
| `TRIM` | 1 | `start`, `end` (source time) | `cut --start/--end --accurate` | keeps [start, end); sample-accurate, `measurements.cut.precision` |
| `CUT` | 1 | `remove: [{start,end}]` sorted, non-overlapping | `cut --segments --accurate` (kept ranges) | sample-accurate |
| `SILENCE_REMOVE` | 1 | `ranges`, `margin`, `min_duration`, `threshold_db` (recorded) | `cut --segments --accurate` | ranges come from the caller |
| `FADE_IN` / `FADE_OUT` | 1 | `duration` | `audio --fade-in/--fade-out` | |
| `NORMALIZE` | 1 | `target_lufs`, `true_peak_db` (required), `loudness_range_lu`, `tolerance_lufs`, `sample_rate`, `profile` | `loudness -I/--tp/--lra/--sample-rate` | two-pass loudnorm, re-measured after |
| `MIX` | 2..8 | `levels: [{gain_db, mute}]` | `audio --music/--music-volume` (pairwise fold) | duration = first input (amix `duration=first`) |
| `MONO` | 1 | – | `audio --mono` | requires a 2-channel input |
| `STEREO` | 1 | – | `audio --stereo` | requires a 1- or 2-channel input |
| `DOWNMIX` | 1 | – | `audio --downmix` | requires 5.1 / 7.1 |
| `NOISE_REDUCTION` | 1 | `mode: "fft"`, `strength_db` [10, 60] | `audio --denoise` (afftdn) | the only implemented mode |
| `DYNAMICS` | 1 | `gate {threshold_db, ratio, attack_ms, release_ms, range_db, knee_db}`, `compressor {threshold_db, ratio, attack_ms, release_ms, makeup_db, knee_db}`, `limiter {ceiling_db, attack_ms, release_ms}` (≥ 1 stage) | `audio --gate/--compress/--limit` (agate, acompressor, alimiter) | fixed order gate → compressor → limiter; omitted fields keep ffmpeg's defaults |
| `CONCAT` | 2..32 | `crossfade` (s, default 0), `sample_rate`, `channels` (1/2/6/8) | `join --transition none\|fade --duration` (concat / acrossfade) | output layout = widest input unless `channels`; total = Σ − (n−1)·crossfade |

Output formats (`outputs[].format`): `wav` (pcm_s16le), `flac`, `mp3`, `m4a`, `aac`, `ogg`, `opus`; encoder
availability is reported by `doctor`. Every intermediate is PCM WAV (no generation loss between operations).

Sources may be audio files or video containers (the audio track is extracted first; the output never carries video).

**Declared but not implemented** (`unsupported_operations` in the contract, `UNSUPPORTED_OPERATION` at validation):
`CHANNEL_MAP` (no typed mapping in ffmpeg-skill), `RESAMPLE` as a standalone operation (only `NORMALIZE.sample_rate`
and `CONCAT.sample_rate`), `FORMAT_CONVERT` (an output property). Details and the reasons:
[docs/ffmpeg-skill.md](docs/ffmpeg-skill.md).

## CLI

| command | reads media | writes media | exit |
|---|---|---|---|
| `skill --json` / `contract --json` | no | no | 0 |
| `doctor --json [--ffmpeg-skill DIR] [--workspace DIR] [--allowed-input ROOT]` | no (runs ffmpeg-skill doctor) | no | 0, 1 on `fail` |
| `validate REQUEST\|- --json` | no | no | 0 / error exit code |
| `plan REQUEST\|- --json` | probe only (read-only) | no | 0 / error exit code |
| `run REQUEST\|- --json [--dry-run] [--workspace DIR] [--allowed-input ROOT] [--ffmpeg-skill DIR] [--timeout S] [--no-reuse]` | yes | yes | 0 / error exit code |

The ffmpeg-skill checkout is found from `--ffmpeg-skill`, else `AUDIO_PRODUCTION_FFMPEG_SKILL_DIR`,
`VIDEO_AGENT_FFMPEG_SKILL_DIR`, `~/.claude/skills/ffmpeg-skill`, `./vendor/ffmpeg-skill`, `../ffmpeg-skill`. It is
never taken from the request document.

## Doctor

`doctor --json` (`audio-production/doctor@1`) reports: Python; the located ffmpeg-skill (directory, version,
contract version, the flags this skill needs, problems); ffmpeg / ffprobe versions as detected by ffmpeg-skill's
doctor; every capability as `supported` / `unsupported` / `unknown`; every operation type with its status and the
missing or unknown capabilities; the not-implemented operations; output formats with encoder status; channel layout
and sample-rate policy; the path policy. Status: `ok`, `degraded` (some operation unsupported), `fail` (ffmpeg-skill
missing / incompatible or path policy broken). `secrets_shown` is always `false`.

## Process boundary

`caller → JSON (stdin) → typed validation → operation graph → ffmpeg-skill adapter (argv) → media execution →
output validation → JSON (stdout)`. With `--json`, stdout carries exactly one document, on success and failure
alike; stderr carries diagnostics (including ffmpeg-skill's own log lines). Exit codes are stable (`errors.exit_codes`).

## Security

- No shell, no `eval`, no user-supplied command, argv, executable, script or filter string; the request schema
  rejects such fields by name and rejects unknown fields. One `subprocess.Popen` in the package (adapter), argv only,
  allow-listed scripts only, minimal child environment.
- Every argv value is a number formatted by this skill (`0.500`, `-6.000`) or a resolved absolute path.
- Inputs: regular files, symlinks resolved, optional `--allowed-input` roots; outputs: inside `--workspace`, no `..`,
  no symlinked escape, no reserved Windows names / invalid characters / option-like names, never an input, never an
  existing file unless `overwrite: true`. Inputs are never modified.
- Failed or cancelled runs leave no partial output and no intermediate without a manifest.
Details: [docs/security.md](docs/security.md).

## Determinism

Operation identity = sha256 over canonical JSON of `{type, effective parameters, input identities, tool versions}`;
a source's identity is its file sha256. No timestamps, no UUIDs, stable topological order (smallest node id first
among ready nodes), stable key order. Identical request + identical inputs + identical ffmpeg-skill / ffmpeg version
→ identical `plan_id` and operation ids, reused intermediates, and content-equivalent outputs (bytes may differ
between encoder builds for lossy formats; WAV / FLAC are reproducible in practice).

## Provenance

Per operation: `operation_id`, `type`, `tool`, `tool_versions`, `parameters`, `input_hashes`, `output_hash`,
`segments` (timeline ↔ source), `status`, `measurements`, `tool_commands_observed`. Per output: `skill`,
`skill_version`, `tool`, `tool_versions`, `output_hash`, the whole operation chain back to the sources with their
sha256. The caller's *instruction* (the request) and the skill's *observation* (`results`, `measurements`,
`tool_runs`) are separate parts of the response.

## Error handling

| code | exit | retryable | when |
|---|---|---|---|
| `INVALID_REQUEST` | 2 | no | document shape, unknown / forbidden field, bad type or range |
| `INVALID_INPUT` | 3 | no | input missing, not a regular file, unreadable, no audio stream, video container |
| `PATH_NOT_ALLOWED` | 4 | no | outside allowed roots / workspace, traversal, symlink escape, unsafe name |
| `UNSUPPORTED_OPERATION` | 5 | no | not implemented (declared or unknown type, unsupported mode) |
| `UNSUPPORTED_FORMAT` | 6 | no | output format unknown, extension mismatch, encoder missing |
| `MISSING_INPUT` | 7 | no | reference to an undeclared source / track / operation |
| `INVALID_TIME_RANGE` | 8 | no | end ≤ start, overlap, outside the media, nothing would remain, fade too long |
| `INVALID_CHANNEL_LAYOUT` | 9 | no | unsupported layout, expectation mismatch, MONO / STEREO / DOWNMIX on the wrong input |
| `INVALID_SAMPLE_RATE` | 10 | no | sample rate not in the accepted list |
| `DEPENDENCY_ERROR` | 11 | no | duplicate id, cycle, self-reference, unreachable node |
| `TOOL_ERROR` | 12 | yes | ffmpeg-skill missing / incompatible (not retryable), tool failure, timeout |
| `OUTPUT_ERROR` | 13 | no | output exists, collides with an input, empty, could not be written |
| `VALIDATION_ERROR` | 14 | no | artifact failed post-validation (stream, duration, channels, rate, codec, loudness) |
| `CANCELLED` | 15 | yes | SIGINT / SIGTERM |
| `INTERNAL_ERROR` | 16 | no | a bug in this skill (still one JSON document) |

## Testing

```text
pip install -e . pytest
export AUDIO_PRODUCTION_FFMPEG_SKILL_DIR=/path/to/ffmpeg-skill     # or clone it as ../ffmpeg-skill
python -m pytest -q          # unit + security + contract + integration with real audio; nothing is skipped
```

`tests/test_unit.py` (schema, timeline arithmetic, graph, identities, canonical JSON, path policy, Windows names),
`tests/test_security.py` (no shell in source, script allow-list, argv formatting, injection through every parameter
kind, traversal / absolute / symlink escapes through the CLI, input-overwrite refusal, malformed JSON, minimal child
environment), `tests/test_contract.py` (contract ⇔ implementation, doctor honesty), `tests/test_integration.py`
(one positive and one negative case per operation, the pipeline below, a two-source mix, output validation, failure
handling, dry run, idempotent re-runs, timeout, SIGINT cancellation, CLI exit codes). CI runs Linux (3.9, 3.11),
Windows and macOS with a real FFmpeg and a fresh ffmpeg-skill clone: [.github/workflows/tests.yml](.github/workflows/tests.yml).

## Real media verification

`tests/test_integration.py::test_real_audio_pipeline_trim_gain_fade_normalize` runs
`source → TRIM → GAIN → FADE_OUT → NORMALIZE → output validation` on a generated 6 s / 48 kHz PCM fixture through the
real ffmpeg-skill and FFmpeg and asserts duration, channels, sample rate, hashes and the re-measured loudness;
`test_mix_two_sources_then_normalize` runs `A → GAIN, B (range, gain) → MIX → NORMALIZE`; `test_concat`, `test_dynamics`
and the video-container cases cover the 0.9.1 capabilities. Fixtures are synthesised with ffmpeg at test time (the
tests may call ffmpeg; the skill never does). Measured on ffmpeg 6.1.1 / ffmpeg-skill 0.9.1 (and FFmpeg 8 in CI).

## Relationship to the other skills

- **ffmpeg-skill** – the execution engine; see [docs/ffmpeg-skill.md](docs/ffmpeg-skill.md) for the exact tools,
  flags and the compatibility gaps that define what is *not* offered here.
- **media-analysis-skill** – measures; its `silence` and `loudness` observations are the natural inputs for
  `SILENCE_REMOVE.ranges` and `NORMALIZE` targets, chosen by the agent.
- **video-production-agent** – the only intended caller: builds the request from its Decision / ProductionPlan,
  runs `audio-production run - --json`, reads `results` / `outputs` into its provenance and Artifact model.
- **transcription-skill, video-editing-skill, QC** – not touched; this skill has no speech, video or final-QC role.

## Current limitations

- Only the first audio stream of a source is used (no `audio_stream` selection yet).
- Every artifact's duration is validated within 0.1 s; cuts are sample-accurate, but CONCAT / MIX / export may
  differ by a codec frame (measured +8–10 ms with AAC sources).
- Intermediates are 16-bit PCM WAV. MIX output duration follows the first input; per-input pan is not available.
- Loudness true-peak of a lossy output (`m4a`, `mp3`, …) may exceed the ceiling by the codec's overshoot; the
  verification happens on the PCM intermediate.
- `doctor` reports core ffmpeg filters (`volume`, `afade`, `amix`, `pan`, `aformat`) as `unknown` because
  ffmpeg-skill's doctor does not probe them; they are verified by output validation at run time. On FFmpeg ≥ 8.0
  ffmpeg-skill 0.9's doctor parses no filters at all (`-filters` prints two flag characters, its pattern expects
  three), so *every* filter capability is `unknown` there (`checks.filter_detection`), never falsely `unsupported`.
- No cache eviction: the work directory grows until the caller removes `<workspace>/.audio-production/<project_id>/`.

## Future extensions (not in this release)

Typed channel mapping, standalone resampling, audio-stream selection, more noise-reduction modes with detected
capabilities, per-input pan in MIX, 24-bit intermediates — each requires a corresponding capability in ffmpeg-skill's
public contract (or a decision to add a second execution backend), and will be declared only once implemented and tested.

## License

[MIT](LICENSE)
