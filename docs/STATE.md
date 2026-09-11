# Repository state

Maintained by the session that last changed the repository. Labels: CURRENT (in main, tested), EXPERIMENTAL
(in main, not yet exercised by a consumer), PLANNED (agreed, not implemented), VISION (direction only), UNKNOWN.

## CURRENT (main)

- Skill `audio-production` 0.3.0, contract `audio-production/contract@1`, one tool `audio-production/run`, 14
  operation types; sources may be audio files or video containers; outputs wav / flac / mp3 / m4a / aac / ogg / opus.
  `GAIN`, `FADE_IN`, `FADE_OUT`, `MONO`, `STEREO`, `DOWNMIX`, `NOISE_REDUCTION`, `DYNAMICS` take `audio_stream`
  (0-based, default 0) to pick which audio stream of their one input `ffmpeg-skill/audio` processes.
- Execution through ffmpeg-skill 0.12.0 ≤ v < 1.0 (`probe`, `audio`, `cut --accurate`, `loudness`, `join`); NORMALIZE
  verification reads the `result` field of the NORMALIZE call's own `--json` response (ADR-13), no second process.
- CLI `skill | contract [--check [FILE|-]]`, `doctor`, `validate`, `plan`, `run` (`--cleanup keep|intermediates`) with
  `--json`; one JSON document on stdout; 15 error codes with stable exit codes.
- Tests: unit / security / contract (pinned `tests/contract/contract.json`) / integration with real audio; CI on
  Linux 3.9 + 3.11, Windows, macOS with real FFmpeg and ffmpeg-skill pinned to commit `336e0c4` (0.12.2).
- Consumers: video-production-agent (ADR-030 there) pins this contract at `tools/audio_production/contract_0.1.0.json`
  and drives 9 of the 14 operations from its planner; its real-media `AudioProductionRealTests` (5) pass against
  main 2f31d4d (verified 2026-09-05).
- GitHub automation (ADR-14): `.github/workflows/release.yml` (single job on push to `main`: resolve the next
  version from PR labels via `release-drafter` dry run, bump `pyproject.toml` + `__init__.py` + the pinned contract
  snapshot only when the auto-bump condition holds, render `CHANGELOG.md` from `git log`, tag, GitHub Release, PyPI
  publish only if `PYPI_API_TOKEN` is set), `.github/workflows/autolabel.yml` (`release-drafter` autolabeler on
  `pull_request_target`), `.github/workflows/codeql.yml` (Python, PR + push + weekly), `.github/dependabot.yml`
  (github-actions + pip), `.github/pull_request_template.md`, `SECURITY.md`.

## EXPERIMENTAL

- `provides` (cross-repository Capability ids, lifecycle EXPERIMENTAL) for the AI-video-production-OS registry
  design. That design lives on the OS repository's `main` (`docs/SPEC.md`, `docs/CAPABILITY_MATRIX.md`) directly.
  Ids here match `docs/CAPABILITY_MATRIX.md` there.

## PLANNED / candidates (highest value first)

1. The release workflow (above) will cut the first tag/Release the next time it runs on a qualifying push, at
   whatever `pyproject.toml` version is current then -- it does not need a human to trigger it, only for PyPI
   publish to stay skipped until a human adds `PYPI_API_TOKEN` as a repository secret (see SECURITY-conscious
   default in release.yml: no token, no publish, tag/Release still happen). Two things depend on repository
   settings only a human can grant: `contents: write` for the default `GITHUB_TOKEN` (Settings → Actions → General
   → Workflow permissions) and, if `main` has branch protection, an allowance for GitHub Actions to push directly
   to it (or the push step in release.yml needs converting to open a PR instead). Do not claim PyPI availability
   before a human confirms a publish actually ran.
2. Per-input pan in MIX, typed CHANNEL_MAP, standalone RESAMPLE: each needs an ffmpeg-skill capability first.

## Known limitations (see README "Current limitations")

`TRIM`/`CUT`/`SILENCE_REMOVE`/`NORMALIZE`/`MIX`/`CONCAT` always use the first audio stream (their ffmpeg-skill tools
have no `--audio-stream`, or, for `MIX`, combine already-resolved inputs); artifact durations validated within 0.1 s
(cuts sample-accurate on PCM, AAC sources up to one codec frame short on FFmpeg 8 / Windows); 16-bit PCM
intermediates; MIX duration follows the first input; lossy outputs may overshoot the true-peak ceiling; core filters
reported `unknown` by doctor; no eviction.

## OS integration status

- ffmpeg-skill: engine, contract 1.0, minimum 0.12.0 (ADR-9, ADR-13). media-analysis-skill: no code dependency (its
  observations are the intended inputs, chosen by the agent). video-production-agent: integrated (see CURRENT).
- AI-video-production-OS: only `provides` (EXPERIMENTAL); no other OS-specific concept in this repository.

## Change log of state

- 2026-09-11 (#10): added GitHub automation infrastructure -- see the CURRENT bullet above for the file list.
  `.github/scripts/{compute_version,render_changelog,bump_version_files}.sh` back the release workflow and were
  each verified against an isolated git fixture (not this repo) before being wired in, including a deliberately
  hostile commit message (`$(...)`, backticks) to confirm `git log`-sourced text never reaches a shell as code;
  that testing caught and fixed two real bugs (a non-`MULTILINE` regex silently matching nothing, and
  `re.subn(count=1)` making the "ambiguous match" check always read back 1 no matter how many matches existed).
  ADR-14.

- 2026-09-05: #1 skill implemented (0.1.0), #2 sponsorship links, #3 `provides`, #4 contract --check + pinned
  snapshot + CLAUDE.md + this file, #5 `run --cleanup intermediates` (operator-level; the request block stays pinned).
- 2026-09-08 (#6): repointed the OS-spec references (README, decisions.md ADR-10, this file) from the stale
  `claude/ai-video-production-os-arch-fck6fy` branch to `AI-video-production-OS`'s `main`; `_verify_loudness` now
  reads the NORMALIZE call's own `result` instead of a second `--measure-only` process (ADR-13), `adapter.SUPPORTED_MIN`
  raised to `(0, 12, 0)`, `VERSION` bumped to `0.2.0` (breaking for the pinned `ffmpeg_skill` contract block —
  video-production-agent must re-pin); dropped the foreclosed "or an ffmpeg-skill change" branch from the
  audio-stream-selection PLANNED item; fixed README's example `ffmpeg-skill` version; repinned CI's ffmpeg-skill
  checkout from commit `2abd89c` (0.9.1) to `336e0c4` (0.12.2) so CI actually satisfies the new `SUPPORTED_MIN`
  (issue #6 item 5) — the full suite (97 tests) was already run and passed against that same checkout locally.
- 2026-09-08 (#8): `audio_stream` (0-based, default 0) added to `GAIN`, `FADE_IN`, `FADE_OUT`, `MONO`, `STEREO`,
  `DOWNMIX`, `NOISE_REDUCTION`, `DYNAMICS` — the operations whose one input is read directly by `ffmpeg-skill/audio`
  — threaded to `--audio-stream N` in the executor's argv, folded into operation identity like every other
  parameter (no separate change needed there), and added to `adapter.FLAGS_USED["audio"]`; `TRIM`/`CUT`/
  `SILENCE_REMOVE`/`NORMALIZE`/`MIX`/`CONCAT` deliberately excluded (their ffmpeg-skill tools have no
  `--audio-stream`, or, for `MIX`, fold already-resolved inputs). Adding a parameter to a pinned operation's schema
  is breaking per `contract_check.PINNED_OPERATION_FIELDS`, so `VERSION` bumped to `0.3.0` (`pyproject.toml` too)
  and `tests/contract/contract.json` regenerated — video-production-agent must re-pin. Closed the audio-stream-
  selection PLANNED item; corrected README "Future extensions" / docs/ffmpeg-skill.md's compatibility-gap table,
  which had framed it as blocked on a future ffmpeg-skill capability that has existed since 0.9.1.
