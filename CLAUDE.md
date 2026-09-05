# audio-production-skill — guide for a future session

Read this first; do not rely on conversation history. Current state and next tasks: `docs/STATE.md`.

## What this repository is

A deterministic **audio production execution Skill** of the kajisho5 video production ecosystem (README "What it is,
and what it is not"). It executes a typed operation graph (GAIN, TRIM, CUT, SILENCE_REMOVE, FADE_IN/OUT, NORMALIZE,
MIX, CONCAT, MONO, STEREO, DOWNMIX, NOISE_REDUCTION, DYNAMICS) through **ffmpeg-skill ≥ 0.9.1**'s public contract
and reports validated artifacts with provenance. It is not an agent, not a measurement skill, not ffmpeg-skill.

## Boundaries that must hold

- No shell, no `ffmpeg` call of its own, no filter strings, no request field may name a command / argv / executable
  (`model.FORBIDDEN_KEYS`); one `subprocess.Popen` in `adapter.py`; allow-listed ffmpeg-skill scripts only.
- No production defaults (`target_lufs`, `true_peak_db`, silence ranges are always explicit), no silence detection,
  no decisions, no conference / speaker vocabulary in code or contract (tested).
- Inputs are never modified; outputs stay inside `--workspace`; failed runs leave no partial output.
- Everything the contract says is derived from `model.OPERATION_TYPES`, `executor.TOOL_FOR`, `adapter.FLAGS_USED`,
  `errors.ERROR_TABLE`, `contract.CAPABILITY_IDS`; `audio-production contract --check` proves it.

## Layout

`src/audio_production/`: `model` (request schema, operation parameter schemas), `security` (path policy), `graph`,
`timeline`, `adapter` (ffmpeg-skill boundary), `executor`, `doctor`, `contract`, `contract_check`, `cli`, `errors`,
`canonical`. `tests/`: unit, security, contract (with `tests/contract/contract.json` pinned), integration (real
audio through the real ffmpeg-skill; nothing skipped). `docs/`: architecture, security, ffmpeg-skill (observed
behaviours and gaps), contract (pinned blocks / drift), testing, decisions (ADRs), STATE.

## Commands

```text
pip install -e . pytest pyflakes mypy
export AUDIO_PRODUCTION_FFMPEG_SKILL_DIR=/path/to/ffmpeg-skill      # or clone it as ../ffmpeg-skill / vendor/ffmpeg-skill
python -m pytest -q                                                 # ~90 s with real ffmpeg
python -m pyflakes src tests && python -m mypy src/audio_production --ignore-missing-imports
audio-production doctor --json
audio-production contract --check tests/contract/contract.json --json
```

## Rules for changes

- A contract change inside the pinned blocks (docs/contract.md) is breaking for video-production-agent's pin:
  bump `VERSION` (`src/audio_production/__init__.py` and `pyproject.toml`), regenerate `tests/contract/contract.json`,
  and note in the PR that the agent must re-pin. Additive keys are fine within a version.
- Never claim an operation the code cannot run: gaps go to `model.UNSUPPORTED_OPERATIONS` with the reason.
- New ffmpeg-skill features: verify the real behaviour first (docs/ffmpeg-skill.md records measurements), then
  raise `adapter.SUPPORTED_MIN` only when the older version is actually unsafe.
- CI pins ffmpeg-skill by commit in `.github/workflows/tests.yml`; runners ship FFmpeg 6 (Ubuntu) and 8 (macOS,
  Windows), which behave differently for AAC trim boundaries (docs/ffmpeg-skill.md).
- Commit messages and PR bodies follow the existing ones; PRs are squash-merged; the branch cannot be deleted
  through the git proxy (delete it in the GitHub UI).
