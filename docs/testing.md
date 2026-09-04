# Testing

`python -m pytest -q` runs everything; nothing is skipped. The integration tests need FFmpeg on PATH (to synthesise
fixtures and for ffmpeg-skill) and an ffmpeg-skill checkout (`AUDIO_PRODUCTION_FFMPEG_SKILL_DIR`, or
`../ffmpeg-skill`, `./vendor/ffmpeg-skill`, `~/.claude/skills/ffmpeg-skill`); a missing one fails the session.

| file | covers |
|---|---|
| `tests/test_unit.py` | request schema (every operation's parameters, arity, references, tracks, outputs), timeline arithmetic (trim / cut / complement / silence rules / mix / fades), graph (implicit nodes, order, cycles, unreachable, determinism), identities (input content, parameters, tool version; op_id is not identity), canonical JSON, error table, file-name rules, path policy, symlink escapes, Windows names |
| `tests/test_security.py` | no shell in source, script allow-list, argv formatting, parameter injection, unsafe output paths / roots / symlinks through the CLI, input overwrite, existing output, malformed JSON, command fields, ffmpeg-skill directory handling, minimal environment, argv builder audit |
| `tests/test_contract.py` | contract ⇔ implementation, `skill` = `contract`, doctor statuses (`supported` / `unsupported` / `unknown`), doctor without ffmpeg-skill, capability table |
| `tests/test_integration.py` | per operation positive + negative (GAIN, TRIM, CUT, SILENCE_REMOVE, FADE_IN/OUT, NORMALIZE, MONO, STEREO, DOWNMIX, NOISE_REDUCTION, every output format), the real-audio pipeline `TRIM → GAIN → FADE_OUT → NORMALIZE`, two-source `MIX → NORMALIZE` (with implicit track range / gain and a muted third input), dry run writes nothing, re-run reuse + `--no-reuse` + tampered intermediate, invalid inputs, expectation failures remove the output, loudness verification failure, timeout, SIGINT cancellation, CLI validate / exit codes / human output |

Fixtures (`tests/fixtures/generate.py`) are synthesised with ffmpeg at test time: 6 s mono PCM tone (−23 LUFS),
4 s stereo AAC, gated tone (silence 0–2 s and 5–6 s), 5.1 PCM, digital silence, a video with audio, a video without
audio, a text file. Expected values in the tests follow from the construction.

Static checks used in development: `python -m pyflakes src tests`, `python -m mypy src/audio_production`,
`python -m compileall src tests`.
