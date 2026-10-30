# Roadmap — audio-production-skill

Part of the [AI Video Production OS](https://github.com/kajisho5/AI-video-production-OS/blob/main/docs/ecosystem/ROADMAP.md) ecosystem: department **音 (Audio)**, status `active`.
Written from a read-only audit of this repository on 2026-10-30. Each step below is meant to be one pull request; tick it off by linking the PR.

## Where it stands

v0.4.0, Python 3.9+ stdlib. 14 operations (GAIN, TRIM, CUT, SILENCE_REMOVE, FADE_IN/OUT, NORMALIZE, MIX, CONCAT, MONO, STEREO, DOWNMIX, NOISE_REDUCTION, DYNAMICS). 83 tests, CI on 3 OSes against ffmpeg-skill pinned + main.

## What this repository owns

`ecosystem.json` gives each job to exactly one repository. Work outside this list belongs to its owner and is handed over, not re-implemented.

| Owned job | Status | Note |
|---|---|---|
| 音量・ラウドネス | done |  |
| ミックス | partial | MIX has no pan, no ducking; ffmpeg-skill/audio --duck/--effects not exposed |
| ダイナミクス | done |  |
| 従来型ノイズ低減 | done | afftdn; ffmpeg-skill/audio --voice preset not exposed |
| 無音・フィラー除去 | partial | silence removal for caller-given ranges; filler removal missing everywhere |

## Wiring

Agent plans 9 ops; MIX, SILENCE_REMOVE, NOISE_REDUCTION, DYNAMICS are executable but never planned. Delegates to ffmpeg-skill probe, audio, cut, loudness, join.

## Steps

- [x] **1. Contract dependencies + split audio.fade** — dependency on ffmpeg-skill; audio.fade_in / audio.fade_out ids
  - Done when: OS dependency_version_ranges PASS
- [ ] **2. MIX ducking / effects** — typed duck and effects params -> ffmpeg-skill/audio
  - Done when: music bed lower under speech (integration)
- [ ] **3. VOICE_CLEANUP op** — typed wrapper of ffmpeg-skill/audio --voice
  - Done when: contract + unit tests
- [ ] **4. FILLER_REMOVE op** — caller-supplied word ranges (from transcript word timestamps); the skill cuts, never decides
  - Done when: sample-accurate fixture
- [ ] **5. (agent side) plan MIX / NR / DYNAMICS / FILLER_REMOVE** — from explicit requirements
  - Done when: tracked in video-production-agent

## Boundaries

Classic noise reduction here vs AI voice isolation in voice-enhance-skill: clean split. sound-design and dubbing outputs are placed through MIX here.

---
Engine for every media operation: [ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) (42 tools, treated as complete). Orchestrator: [video-production-agent](https://github.com/kajisho5/video-production-agent).
