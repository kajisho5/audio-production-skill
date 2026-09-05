# Repository state

Maintained by the session that last changed the repository. Labels: CURRENT (in main, tested), EXPERIMENTAL
(in main, not yet exercised by a consumer), PLANNED (agreed, not implemented), VISION (direction only), UNKNOWN.

## CURRENT (main)

- Skill `audio-production` 0.1.0, contract `audio-production/contract@1`, one tool `audio-production/run`, 14
  operation types; sources may be audio files or video containers; outputs wav / flac / mp3 / m4a / aac / ogg / opus.
- Execution through ffmpeg-skill 0.9.1 ≤ v < 1.0 (`probe`, `audio`, `cut --accurate`, `loudness`, `join`).
- CLI `skill | contract [--check [FILE|-]]`, `doctor`, `validate`, `plan`, `run` (`--cleanup keep|intermediates`) with
  `--json`; one JSON document on stdout; 15 error codes with stable exit codes.
- Tests: unit / security / contract (pinned `tests/contract/contract.json`) / integration with real audio; CI on
  Linux 3.9 + 3.11, Windows, macOS with real FFmpeg and ffmpeg-skill pinned to commit 2abd89c.
- Consumers: video-production-agent (ADR-030 there) pins this contract at `tools/audio_production/contract_0.1.0.json`
  and drives 9 of the 14 operations from its planner; its real-media `AudioProductionRealTests` (5) pass against
  main 2f31d4d (verified 2026-09-05).

## EXPERIMENTAL

- `provides` (cross-repository Capability ids, lifecycle EXPERIMENTAL) for the AI-video-production-OS registry
  design. That design lives on the OS repository's branch `claude/ai-video-production-os-arch-fck6fy`
  (`docs/SPEC.md`, `docs/CAPABILITY_MATRIX.md`); the OS `main` is a README stub. Ids here match that branch.

## PLANNED / candidates (highest value first)

1. Audio-stream selection (`tracks[].audio_stream`): ffmpeg-skill `audio.py --audio-stream` exists; `cut`, `loudness`,
   `join` take the first stream only, so this needs either extraction-first for every such source or an ffmpeg-skill
   change. Not started.
2. A release tag / GitHub release for 0.1.0 once the human decides on distribution (PyPI is not set up; nothing is
   published). Do not claim availability.
3. Per-input pan in MIX, typed CHANNEL_MAP, standalone RESAMPLE: each needs an ffmpeg-skill capability first.

## Known limitations (see README "Current limitations")

First audio stream only; artifact durations validated within 0.1 s (cuts sample-accurate on PCM, AAC sources up to
one codec frame short on FFmpeg 8 / Windows); 16-bit PCM intermediates; MIX duration follows the first input; lossy
outputs may overshoot the true-peak ceiling; core filters reported `unknown` by doctor; no eviction.

## OS integration status

- ffmpeg-skill: engine, contract 1.0, minimum 0.9.1 (ADR-9). media-analysis-skill: no code dependency (its
  observations are the intended inputs, chosen by the agent). video-production-agent: integrated (see CURRENT).
- AI-video-production-OS: only `provides` (EXPERIMENTAL); no other OS-specific concept in this repository.

## Change log of state

- 2026-09-05: #1 skill implemented (0.1.0), #2 sponsorship links, #3 `provides`, #4 contract --check + pinned
  snapshot + CLAUDE.md + this file, #5 `run --cleanup intermediates` (operator-level; the request block stays pinned).
