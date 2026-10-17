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
  going forward. `FADE_IN` and `FADE_OUT` are both directions of one `audio.fade` capability; the other twelve
  operation types map 1:1. Every operation type gets an id: this Skill has a single tool (`{SKILL_ID}/run`) and
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
