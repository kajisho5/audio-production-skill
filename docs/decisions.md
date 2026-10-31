# Decisions

- **ADR-1 Execute through ffmpeg-skill only.** No direct ffmpeg. Missing capabilities are declared gaps
  (docs/ffmpeg-skill.md), never worked around with a private ffmpeg call.
- **ADR-2 Envelope carries both `ok` and `status`.** The ecosystem's error contract for this skill asks for
  `{ok, error{code, message, retryable}}`; media-analysis-skill uses `status: ok|partial|error`. Both are present and
  consistent; exit codes are per error code, starting at 2.
- **ADR-3 No production defaults.** `target_lufs`, `true_peak_db`, silence ranges, gains are always explicit. The
  only defaults are arithmetic (`margin 0`, `min_duration 0`) and are recorded as effective parameters.
- **ADR-4 PCM WAV intermediates, one per node, named by identity.** Enables reuse, hashing and validation with a
  single probe per artifact; costs disk space (no eviction).
- **ADR-5 Implicit track nodes are visible.** `range` / `gain_db` on a track become `op:<track>.range` / `.gain`
  nodes with `implicit: true`, so provenance never hides a transformation.
- **ADR-6 Refuse rather than approximate.** MONO on mono (would attenuate), DOWNMIX on stereo, video containers,
  compressed cut sources without decoding, silent normalisation — each is an explicit error, not a best effort.
- **ADR-7 Unknown is a capability status.** Core ffmpeg filters not probed by ffmpeg-skill's doctor are reported
  `unknown` and verified by output validation, instead of being claimed `supported`.
- **ADR-8 Generic core.** No conference / speaker / presentation vocabulary in code, contract or schemas; tracks are
  generic (`label` is free text for the caller's domain layer).
- **ADR-9 Track ffmpeg-skill's contract, not its version number.** 0.9.1 added exactly the capabilities 0.9.0
  lacked (sample-accurate audio cuts, audio join, typed dynamics, extraction, FFmpeg 8 detection); the skill adopted
  them the same day and raised its minimum to 0.9.1 instead of keeping work-arounds for 0.9.0's defects.
- **ADR-10 `provides`: publish this Skill's fourteen operations as cross-repository Capability ids.** Added for
  `kajisho5/AI-video-production-OS`'s `CapabilityContract.provides` (`docs/SPEC.md` there), so a registry can
  resolve "who provides `audio.gain`" without hardcoding this repository. `model.OPERATION_TYPES` has no native
  capability-shaped id of its own (unlike video-editing-skill's `operations.OPERATIONS`), so the id per operation
  type is a new naming decision, not a mechanical derivation — it matches the ids already assigned in that
  project's own `docs/CAPABILITY_MATRIX.md`, kept here in `contract.CAPABILITY_IDS` as the single source of truth
  going forward. Every operation type maps 1:1 (`FADE_IN` / `FADE_OUT` were one shared `audio.fade` until
  2026-10-30; split into `audio.fade_in` / `audio.fade_out` because the OS registry keys capabilities by id, and a
  duplicate id made the two operations indistinguishable there). Every operation type gets an id: this Skill has a single tool (`{SKILL_ID}/run`) and
  every operation always writes a validated audio artifact through it, unlike `thumbnail-skill`'s `validate` tool,
  which produces no output and is excluded there. Additive: it adds a new top-level `provides` key derived from
  `OPERATION_TYPES` and says nothing `operations[]` doesn't already say, only indexed by Capability id instead of
  by operation type.
- **ADR-11 `contract --check` with a pinned snapshot, classifying drift as breaking or additive.** The ecosystem
  pattern (video-editing-skill `contract --check`, video-production-agent `DRIFT_KEYS`): the pinned blocks are exactly
  what the agent compares plus `operations` and `provides`, so this repository fails its own CI before a change can
  silently make the agent mark the Skill MISSING. `version` is pinned too: a bump is coordinated with the agent's
  re-pin, and additive keys (like `provides`) stay within a version. The OS specification behind `provides` lives on
  `AI-video-production-OS`'s `main`; `provides` is labelled EXPERIMENTAL here (docs/STATE.md) until a consumer there
  actually resolves it.
- **ADR-12 Cleanup is an operator flag, not a request option.** video-production-agent pins the whole `request`
  and `response` contract blocks; adding `options.cleanup` would be breaking for its pin for a purely local
  housekeeping choice. `run --cleanup intermediates` removes the project's work directory only after every output
  is exported and validated (never on failure, never outputs), reports it under a new response key `cleanup`, and is
  described by the additive contract key `cleanup`. Age / size eviction across projects stays with the operator.
- **ADR-13 `_verify_loudness` reads the NORMALIZE call's own `result`, not a second `--measure-only` process.**
  ffmpeg-skill 0.12.0 added the post-normalization measurement (`result`) to `loudness.py`'s `--json` response for
  every NORMALIZE call, closing the gap that used to require a separate `--measure-only` process purely to learn
  the achieved loudness (doubling every `loudnorm` pass). This was never a deliberate "never trust the tool's
  self-report" security measure — the second call re-ran the same tool against the same artifact it had just
  written, so it verified nothing a compromised or buggy ffmpeg-skill could not also have faked the first time; it
  was a workaround for a limitation ffmpeg-skill has since fixed. `adapter.SUPPORTED_MIN` moves to `(0, 12, 0)`
  since `result` did not exist before (ADR-9's rule: raise the minimum only once the older version is actually
  unsafe/insufficient, not kept as a permanent workaround). `adapter.measure_loudness` and the `measure_only` flag
  in `FLAGS_USED` are removed as dead code. Breaking for the pinned `ffmpeg_skill` contract block; `VERSION` bumps
  to `0.2.0` and `tests/contract/contract.json` is regenerated (video-production-agent must re-pin).
  *Superseded in part by ADR-15:* the `SUPPORTED_MIN` floor is gone; a release without `result` is verified with
  `--measure-only` when its contract declares that flag, and refused with `TOOL_ERROR` only when it declares neither.
- **ADR-14 GitHub release automation is one workflow, triggered on push to `main`, not push+tag split.**
  A commit or tag pushed by a workflow using the default `GITHUB_TOKEN` does not itself trigger another
  `on: push`/`on: push: tags` workflow (GitHub's documented anti-recursion rule) — splitting version-decision and
  tag-triggered publish into two workflows would leave the second half permanently unrun. `release.yml` instead
  does everything in one job: read `pyproject.toml`'s current version, resolve the next version via
  `release-drafter --dry-run` (labels attached by the separate `autolabel.yml`, itself `pull_request_target` so
  forked PRs still get labeled), decide whether to actually apply that resolution (`.github/scripts/compute_version.sh`
  — auto-bump fires only when the current version already equals the latest tag; otherwise a version a human
  already bumped by hand, per this repo's existing manual-bump convention for a breaking contract change, is
  respected and released as-is, never overwritten), bump `pyproject.toml` / `__init__.py` / regenerate
  `tests/contract/contract.json` (the release workflow's own bump is a version-block change like any other,
  so it must keep the pinned snapshot in sync the same way a human contributor's manual bump already has to),
  render `CHANGELOG.md` from `git log` (never from `${{ github.event.* }}` or a marketplace action's text output
  spliced into a `run:` block — the known GitHub Actions script-injection pattern; `git log` output only ever
  becomes a shell variable or file content, never re-parsed as script source — verified against a fixture commit
  message containing `$(...)` and backticks), tag, create the GitHub Release, and publish to PyPI only when
  `PYPI_API_TOKEN` is set (absent: tag and Release still happen, publish is silently skipped, never a hard
  failure). The three scripts behind this were each exercised against an isolated git fixture before being wired
  into the workflow, which is how two real bugs were caught: a version-line regex missing `re.MULTILINE` (matched
  nothing, ever, against multi-line file content) and `re.subn(..., count=1)` always reporting exactly one
  replacement regardless of how many lines actually matched, making the "reject an ambiguous version string"
  check never triggerable. Needs two things only a human can grant before it can push to `main`: `contents: write`
  for the default token (Settings → Actions → General → Workflow permissions) and, if `main` has branch
  protection, an allowance for Actions to push to it directly (see docs/STATE.md PLANNED).
- **ADR-15 ffmpeg-skill compatibility is capability detection; there is no version window.** The adapter refused
  every ffmpeg-skill outside `[0.12.0, 1.0.0)`, so ffmpeg-skill 1.x / 2.x ("ffmpeg-skill 2.10.3 is outside the
  supported window") was rejected even though each tool and flag this skill emits is still declared there, and
  ffmpeg-skill keeps `contract_version` at `1.0` across 0.x, 1.x and 2.x, so the number says nothing about what
  changed. Measured differences between 0.12.2 and 2.10.3 that touch this skill: (1) from 1.10 an existing output
  is refused without `--overwrite`, a flag older releases reject — detected from `audio`'s `input_schema` and
  emitted on export only when the request set `overwrite: true` (intermediates are deleted before each tool call,
  so they never need it); (2) a `--measure-only` document moved its numbers under `measured` — read from the top
  level, `result` or `measured`. 2.0's other breaks (`--crf` → `--quality`, `scripts/_common/` package, probe's `hdr`
  meaning) do not touch this skill: it emits no `--crf`/`--quality`, imports nothing from ffmpeg-skill and reads no
  `hdr`. Rules: required capabilities go in `FLAGS_USED` and are checked flag by flag (that check is what refuses
  0.9.0, which lacks `--audio-stream` and the dynamics flags — no version comparison is needed for it); a capability
  whose absence has a working older behaviour goes in `OPTIONAL_FLAGS_USED` and is used through `takes_flag()`;
  `contract_version` other than `1.x` is a doctor warning, not a refusal; no upper bound, ever. `doctor` reports the
  detected optional flags. CI runs every cell against commit `336e0c4` (0.12.2) and ffmpeg-skill `main`, daily too,
  so a new ffmpeg-skill release is exercised without a commit here. Pinned by `tests/test_ffmpeg_skill_compat.py`.
  Breaking for the pinned `ffmpeg_skill` contract block (`version_window` removed, `compatibility` and
  `optional_flags_used` added): `VERSION` bumps to `0.4.0`, video-production-agent must re-pin.
- **ADR-16 VOICE_CLEANUP wraps `ffmpeg-skill/audio --voice` as a typed level, detected from the contract.** The
  speech chains ffmpeg-skill ships (light / medium / strong, see docs/ffmpeg-skill.md) are exposed as one operation
  with one required enum `level` and `audio_stream` — nothing else, because `--voice` takes nothing else: inventing
  a strength or cutoff here would be a parameter the engine never receives. `level` has no default (ADR-3: the
  caller decides; ffmpeg-skill's own bare-flag default `medium` is not taken over). The flag changed shape in
  ffmpeg-skill 1.13 (boolean switch → `light|medium|strong`, bare = medium, byte-identical chain), and CI runs both
  shapes (0.12.2 and `main`), so `voice` is a required flag in `FLAGS_USED` and the form is chosen per run from the
  located contract's `input_schema` (`enum` present → `--voice <level>`; boolean → bare `--voice`, `medium` only).
  A level the located ffmpeg-skill cannot run is refused at planning (dry run included) with `TOOL_ERROR`
  `voice_level_unsupported`, `retryable: false`, `details.available_levels` — never silently substituted by the
  medium chain, and never by a version comparison (ADR-15). `--denoise` / `--denoise-strength` are never emitted
  with `--voice` (audio.py ignores them under `--voice`). The medium / strong chains contain afftdn with a fixed
  `nf=-25`, inside afftdn's valid [−80, −20]; there is no caller-controlled value that could reach the out-of-range
  `nf` that NOISE_REDUCTION's `strength_db` < 20 produces (recorded as a separate PLANNED item, not changed here).
  Required capabilities list every filter any level may use (`highpass`, `deesser`, `afftdn`, `acompressor`,
  `alimiter`), as DYNAMICS lists all three stages. Provenance: the operation's `parameters` (`level`), the
  `measurements.voice_cleanup` record of the flag form actually passed, and ffmpeg-skill's observed ffmpeg command
  (the full filter chain) in `tool_commands_observed`. Capability id `audio.voice_cleanup`. Adding an operation
  is breaking for pinning agents (docs/contract.md): `tests/contract/contract.json` is regenerated, and the version
  is bumped by the release workflow from the PR label (ADR-14), not by hand.
- **ADR-17 NOISE_REDUCTION `strength_db` is 20..60, the range afftdn can run.** ffmpeg-skill's `--denoise-strength S`
  becomes afftdn `nf=-S`, and afftdn accepts a noise floor only in [−80, −20] dB (FFmpeg 6.1 refuses `nf=-15`,
  docs/ffmpeg-skill.md), while ffmpeg-skill's own flag accepts 10..60. The schema minimum of 10 therefore let
  10 ≤ strength_db < 20 pass validation and fail at run time with `TOOL_ERROR` after earlier steps had already run.
  The minimum is now 20 (`model.NOISE_REDUCTION_STRENGTH_DB`), so the contract only declares values that execute;
  10..19.99 is refused at validation (and so by `validate`, `plan` and `run --dry-run`, before any tool runs) with
  `INVALID_REQUEST`, `details {field, min, max}` like every other range error, and a message that names afftdn's range
  so a caller sees why. Rejected: clamping to 20 (silently runs something other than what was asked, ADR-3) and
  remapping strength to a different `nf` (the engine's mapping is ffmpeg-skill's; a fix there, if it ever narrows its
  own flag, needs nothing here). This narrows a pinned operation's `parameters`, which docs/contract.md classifies as
  breaking for a pinning agent: `tests/contract/contract.json` is regenerated and the agent must re-pin. It is still
  released as a fix (patch, label `fix`), not a minor: no request that could ever succeed is refused, the only
  requests newly refused are ones that always failed, and video-production-agent already refused 10..19.99 itself
  (its ADR-063), so its `0.4.x` range check keeps accepting the release. VOICE_CLEANUP is unaffected (fixed `nf=-25`,
  ADR-16).
- **ADR-18 FILLER_REMOVE cuts at caller-supplied word timings against a caller-chosen lexicon; it never transcribes.**
  Filler removal belongs to this skill (`ecosystem.json`: "filler and silence removal"), speech recognition to
  transcription-skill, so the operation takes the words with their times in the request: `words: [{start, end, text,
  confidence?}]`, or a `transcription-skill/transcript/0.1` document as `transcript` (exactly one of the two; times on
  the operation's input timeline). Missing timestamps are refused, never estimated: a word without `start` / `end`, no
  words at all, or a transcript segment whose `words` is null (transcribed without word timestamps) is `INVALID_REQUEST`
  with `details.reason: word_timestamps_missing` (the transcript case names the segments); a caller that accepts partial
  coverage passes `words` for the segments that have them. From the transcript only `schema`, `segments[].words` and
  the identifying `id` / `asset_id` / `language` are read (the rest is that skill's document, validated by it); the
  effective parameters keep the extracted `words` plus that reference, so the identity depends on the words, not on
  the transcript's unrelated fields. `lexicon {presets?, words?, keep?}` is required with no default (ADR-3): the
  presets `ja` / `en` hold only hesitation sounds (えー, えーと, えっと, あのー, うーん, ... / um, uh, erm, hmm, ...);
  words that are also ordinary words in running speech (あの, その, まあ, なんか, like) are left out because cutting them
  cuts real sentences, and a caller adds them through `words` deliberately. Matching is exact on a normal form (NFKC,
  case-folded, punctuation and whitespace dropped, ー / 〜 / ~ unified and collapsed); a run of up to 3 touching tokens
  (gap <= 0.05 s) counts as one word because speech engines split Japanese into sub-word tokens ("えーと" as "えー" +
  "と"), and the longest run wins. Each filler becomes `[start - pad, end + pad]` (`pad` 0..0.5, default 0), never
  reaching into the neighbouring word; consecutive fillers with no word between them merge when at most `merge_gap`
  apart (default 0: only touching ranges merge); a real word in the gap never merges. This is arithmetic on the
  caller's data (`fillers.py`, pure), recorded in `measurements.filler_remove` (matched words, ranges, removed
  seconds, effective lexicon) and `measurements.effective_ranges`. Lowering reuses the CUT path: `ffmpeg-skill/cut
  --segments <kept ranges> --accurate`, sample-accurate (the integration fixture checks the exact sample count).
  `declick_ms` maps to `cut --declick-ms` (fades at each join, no duration change: a fade-out / fade-in, not an
  overlapping crossfade, so the kept audio stays sample-aligned); omitted, nothing is passed and ffmpeg-skill's own
  behaviour applies (20 ms on releases with the flag, none on 0.12.2); `0` needs nothing; a value above 0 on an
  ffmpeg-skill whose contract lacks `declick_ms` is refused at planning (`TOOL_ERROR`, `reason:
  ffmpeg_skill_flag_missing`, `retryable: false`) instead of silently running butt joins. Rejected: running
  ffmpeg-skill's own `silence --filler` (it also removes silences, decides with its own heuristics, and reads a file
  rather than the request), and doing ASR here. Capability id `audio.filler_remove`; `keeps_timeline: false`.
- **ADR-19 MIX ducks one bed under input 0 through `ffmpeg-skill/audio --duck`, detected from the contract.** The
  roadmap's "MIX ducking / effects": `MIX.duck {input, amount_db, threshold_db?, attack_ms?, release_ms?}` names the
  one input that is lowered (sidechaincompress keyed by input 0) — exactly what `--music X --duck` does in one
  ffmpeg-skill call, where the key is that call's main track. The ducked bed is therefore folded first, with input 0
  as the main track; the other inputs are summed afterwards un-ducked. `amix normalize=0` makes the sum independent
  of the fold order, so that reordering changes nothing else. More than one ducked bed is not offered: a later fold's
  main track already contains the first bed, so it could not be keyed by input 0 alone. Effects are the inputs that
  are not ducked: ffmpeg-skill's `--effects` is the same graph (`volume`, then `amix normalize=0`, never ducked) as
  one more `--music` fold, so it adds no capability here and is not emitted (one code path instead of two).
  `amount_db` is required (ADR-3); threshold / attack / release left out keep ffmpeg-skill's defaults (-26.02 dBFS,
  20 ms, 400 ms; fixed in 0.12.2), visible in the observed command. Ranges are those audio.py checks for
  sidechaincompress (0..60 dB, -60..0 dBFS, 0.01..2000 ms, 0.01..9000 ms). Input 0 and the ducked input must be
  unmuted (validation). All duck flags are optional flags (`OPTIONAL_FLAGS_USED`), because a MIX without duck needs
  none and 0.12.2 declares only `--duck` / `--duck-amount`: a flag the request needs and the located contract does
  not declare is refused at planning, dry run included, with `TOOL_ERROR` `ffmpeg_skill_flag_missing` and
  `details.missing_flags` — never a version comparison (ADR-15), never a silently dropped knob. `measurements.duck`
  records the parameters and the flags passed. MIX's `required_capabilities` are deliberately unchanged
  (sidechaincompress is a core filter ffmpeg-skill's doctor does not list, like amix; and that list is a field
  video-production-agent treats as fatal drift). Both changes are additive for the agent: a new operation and a new
  optional MIX parameter (`parameters` is not in its fatal drift set), no change to `errors`, `request`,
  `response`, `provenance` or `execution`; `tests/contract/contract.json` is regenerated and the release stays
  `0.4.x` (no `feature` label), which video-production-agent's range check accepts.
- **ADR-20 The CLI's JSON boundary is UTF-8 in and ASCII out, independent of the console code page.** FILLER_REMOVE
  brought the first non-ASCII text into the contract (`filler_lexicons`) and into requests (えー). Windows consoles
  encode stdio through cp1252, so `skill --json` crashed writing the contract and a request with Japanese words could
  not be read (Windows CI). The request document is read from `sys.stdin.buffer` and decoded as UTF-8 (RFC 8259; a BOM
  is tolerated, anything else is `INVALID_REQUEST`), and every JSON document is written with `ensure_ascii=True`
  (non-ASCII as `\u` escapes), so any consumer parses the same document on any code page; human-readable output
  uses `errors="backslashreplace"`. A caller must send the request as UTF-8 bytes.
