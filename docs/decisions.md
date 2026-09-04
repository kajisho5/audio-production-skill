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
