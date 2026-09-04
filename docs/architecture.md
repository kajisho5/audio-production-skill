# Architecture

## Position in the ecosystem

```text
video-production-agent          Observation → Inference → Decision → ProductionPlan            (brain)
        │  builds a typed request (sources, tracks, operations, outputs)
        ▼
audio-production-skill          request → validation → graph → plan → execute → validate       (this package)
        │  ffmpeg-skill/{probe,audio,cut,loudness} with typed argv
        ▼
ffmpeg-skill                    runs ffmpeg / ffprobe                                          (hands)

media-analysis-skill            measures inputs (silence, loudness, streams) for the agent     (meters)
```

Responsibilities that are deliberately **absent** here: measurement for decisions, silence detection, loudness
targets, any semantic judgement ("this is speech"), production planning, approvals, video, transcription, QC.

## Typed Audio Project Model (`model.py`)

| concept | fields | notes |
|---|---|---|
| `AudioProject` | `project_id`, `sources[]`, `tracks[]`, `operations[]`, `outputs[]` | one request = one project |
| `AudioSource` | `source_id`, `path` | fingerprinted (sha256) at execution |
| `AudioTrack` | `track_id`, `source_id`, `label?`, `channel_layout?`, `range?`, `gain_db?` | generic; the expected `channel_layout` is verified against the probe; `range` / `gain_db` become implicit TRIM / GAIN nodes `op:<track>.range` / `op:<track>.gain` |
| `AudioOperation` | `op_id`, `type`, `inputs[]` (refs), `parameters` | parameters validated per type (`OPERATION_TYPES`) |
| `AudioOutput` | `output_id`, `operation` (ref), `path`, `format`, `overwrite?`, `expect?` | terminal artifact |
| `AudioSegment` (`timeline.py`) | `timeline {start,end}`, `source_id`, `source {start,end}`, `input_index` | half-open ranges in seconds |
| `OperationDependency` (`graph.py`) | node `inputs[]`, `consumers[]` | topological order = Kahn with smallest ready id first |
| `OperationResult` (`executor.NodeState`) | `operation_id`, `type`, `tool`, `status`, `segments`, `artifact`, `input_hashes`, `measurements`, `tool_commands_observed`, `error?` | one per node in the response |

## Timeline

Source time and timeline time are separate. A track over a 6 s source starts as one segment
`timeline [0,6) ← source [0,6)`. TRIM 1..4 yields `[0,3) ← [1,4)`; CUT of `[1,2)` then yields `[0,1) ← [1,2)` and
`[1,2) ← [3,4)`. Gain / fades / normalisation / channel operations keep the mapping. MIX keeps every input's segments
tagged with `input_index`, clipped to the first input's duration. Every artifact and every output therefore carries
`output → operation → source → source range`. `SILENCE_REMOVE` applies `margin` and `min_duration` arithmetic to the
caller's ranges first (`measurements.effective_ranges` records the result).

## Operation graph and identity

Nodes: `track:<id>` (SOURCE_TRACK), implicit `op:<track>.range` / `op:<track>.gain`, and `op:<id>`. The graph rejects
duplicate ids, self references, cycles, and operations or tracks not connected to any output.

Identity (`graph.identities`): `sha256(canonical_json({kind: "source_track", source_sha256, channel_layout}))` for a
track, `sha256(canonical_json({kind: "operation", type, parameters, inputs: [identities], tool_versions}))` for an
operation. `op_id` is a label, not part of the identity. `plan_id` hashes all identities plus the outputs.

## Execution (`executor.py`)

1. Probe every source through `ffmpeg-skill/probe` (also under dry run: read-only), refuse no-audio and video
   containers, fingerprint (sha256).
2. Resolve outputs (workspace, collisions, existence), the work directory `<workspace>/.audio-production/<project_id>/`.
3. Plan every node: tool selection (`TOOL_FOR`), capability check against the doctor's statuses, expected segments.
   `plan` / `--dry-run` stops here and returns the plan and planned results.
4. Execute in topological order. Each node writes one PCM WAV intermediate named `<identity[:16]>.wav` next to a
   manifest `<identity[:16]>.json`; if both exist, the manifest matches the identity and input hashes and the file's
   sha256 matches, the node is `reused`. A non-PCM input of TRIM / CUT / SILENCE_REMOVE is decoded to a `.decode.wav`
   sidecar first (ffmpeg-skill/cut stream-copies). MIX folds pairwise through `ffmpeg-skill/audio --music` with
   `.mixN.wav` sidecars. Sidecars are always removed; on failure the intermediate is removed too.
5. Validate every intermediate: exists, size > 0, readable, probed audio stream, codec `pcm_s16le`, duration within
   0.1 s of the expected timeline, channel count as derived from the graph, requested sample rate. NORMALIZE outputs
   are re-measured with `ffmpeg-skill/loudness --measure-only`; with `tolerance_lufs` set, an off-target result is a
   `VALIDATION_ERROR`.
6. Export every output with `ffmpeg-skill/audio` to the requested format and validate it against `expect`.
7. Return one response document: `ok`, `status`, `plan`, `results`, `outputs` (with provenance), `tool_runs`,
   `error?`. On the first failure the remaining nodes are `skipped`; on SIGINT / SIGTERM the running tool's process
   group is killed and the status is `cancelled`.

## ffmpeg-skill adapter (`adapter.py`)

The only module that starts a process. `locate()` finds the checkout; `info()` reads its contract
(`_contract.py --json --static`) and checks `contract_version == 1.0`, the version window and that every flag this
skill emits exists in the tool's `input_schema`; `run_tool()` runs `[sys.executable, scripts/<tool>.py, *argv, --json]`
in its own process group with a minimal environment and a timeout, and parses the `{"status": "completed"|"failed"}`
document; `probe()` and `measure_loudness()` wrap the two read-only tools.

## Response envelope

`{"schema": "audio-production/response@1", "skill": {"id", "version"}, "ok": bool, "status": "ok|error|cancelled", ...}`.
`ok` is the field named by the ecosystem's typed error contract (`error: {code, message, retryable, details}`);
`status` mirrors media-analysis-skill's convention so an adapter written for it can read this document too. The
process exit code is `0` iff `ok`, else `errors.EXIT_CODES[code]`.

## Versioning

- Package / Skill version: `audio_production.VERSION` (`0.1.0`), carried in every document and in every intermediate manifest.
- Document schemas: `audio-production/{contract,request,response,doctor}@1`, versioned independently; within `@1`
  changes are additive only. Renaming an operation type, a parameter, or changing how an operation is realised bumps
  the minor package version (and therefore every operation identity, by design).
- ffmpeg-skill compatibility window: contract `1.0`, version `[0.8.4, 1.0.0)`; checked at every run and by `doctor`.
