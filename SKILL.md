---
name: audio-production
description: Deterministic audio production execution Skill for the AI Video Production Ecosystem. Use it when a caller (normally video-production-agent) has already decided what to do to an audio file and needs it executed safely - gain, trim, cut, removal of explicitly given silence ranges, removal of filler words at caller-supplied word timestamps (e.g. transcription-skill's transcript) against a caller-chosen lexicon, fade in / out, EBU R128 loudness normalisation to a given target and true-peak ceiling, several delivery loudness versions of one master in one run (EBU R128 / ATSC A/85 profiles or stated targets, each measured), mixing two to eight tracks (optionally ducking one bed under the first track), stems (dialogue / music / effects as separate files of the programme's length that sum to the mix), mono / stereo / surround down-mix, FFT noise reduction, speech clean-up presets (ffmpeg-skill's --voice light / medium / strong), conversion to wav / flac / mp3 / m4a / aac / ogg / opus - as a typed operation graph with validated outputs and provenance. Do NOT use it to measure audio (media-analysis-skill), to decide which ranges are silence or what loudness to target (video-production-agent), to transcribe (transcription-skill), to edit video (video-editing-skill), or to run arbitrary ffmpeg commands or filters (it refuses them).
---

# audio-production

Machine interface: `audio-production run - --json` with a `audio-production/request@1` document on stdin; exactly one
`audio-production/response@1` document on stdout. `skill --json` prints the contract, `doctor --json` the environment,
`plan - --json` a dry run, `validate - --json` a schema check.

Rules for a calling agent:
1. Measure first with media-analysis-skill (or ffmpeg-skill probe / loudness --measure-only); decide; then build a request.
2. Give explicit, typed parameters: `NORMALIZE` needs `target_lufs` and `true_peak_db`; each `LOUDNESS_SET` version names a profile or states both, and the set needs `tolerance_lufs`; `SILENCE_REMOVE` needs the
   ranges you decided to remove; `FILLER_REMOVE` needs word timestamps and a `lexicon`. This skill has no defaults for production values and never guesses.
3. Never send commands, argv, filter strings or executable paths: the request is rejected.
4. Keep outputs inside the workspace, never at an input path; set `overwrite: true` deliberately.
5. Read `results[].status`, `outputs[].provenance` and `error` from the JSON; the exit code is 0 only when `ok` is true.

Loudness: this skill owns 音量・ラウドネス, and ffmpeg-skill is the engine: `NORMALIZE` and `LOUDNESS_SET` run through
ffmpeg-skill `loudness`. video-production-agent's video path keeps calling `ffmpeg-skill/loudness` itself; that routing changes
only after a measured comparison of `loudnorm` and `ebur128` (video-production-agent step 34). That comparison
([ADR-116](https://github.com/kajisho5/video-production-agent/blob/main/docs/decisions.md)) found both paths write the same samples, so the routing stays as it is.

See README.md for the schemas, operation table, error codes and limitations.
