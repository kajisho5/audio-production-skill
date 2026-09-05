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
  re-pin, and additive keys (like `provides`) stay within a version. The OS specification behind `provides` is a
  draft branch; it is labelled EXPERIMENTAL here (docs/STATE.md) until the OS merges it.
