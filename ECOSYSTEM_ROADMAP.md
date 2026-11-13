# Roadmap — audio-production-skill

Part of the [AI Video Production OS](https://github.com/kajisho5/AI-video-production-OS/blob/main/docs/ecosystem/ROADMAP.md) ecosystem: department **音 (Audio)**, status `active`.
Written from a read-only audit of this repository on 2026-10-30. Each step below is meant to be one pull request; tick it off by linking the PR.

## Where it stands

v1.2.0, Python 3.9+ stdlib. Operations: GAIN, TRIM, CUT, SILENCE_REMOVE, FADE_IN/OUT, NORMALIZE, MIX (with duck), CONCAT, MONO, STEREO, DOWNMIX, NOISE_REDUCTION, DYNAMICS, VOICE_CLEANUP (step 3), FILLER_REMOVE (step 4). CI on 3 OSes against ffmpeg-skill pinned + main; ffmpeg-skill 3.x accepted (step 6).

## What this repository owns

`ecosystem.json` gives each job to exactly one repository. Work outside this list belongs to its owner and is handed over, not re-implemented.

| Owned job | Status | Note |
|---|---|---|
| 音量・ラウドネス | done | owned here; ffmpeg-skill `loudness` is the engine underneath ([Velisa ownership decisions 2026-11-12](https://github.com/kajisho5/Velisa/blob/main/docs/ecosystem/DECISION_LOG.md)) |
| ミックス | partial | MIX ducks one bed under input 0 (ffmpeg-skill/audio --duck, #25); no per-input pan |
| ダイナミクス | done |  |
| 従来型ノイズ低減 | done | afftdn (NOISE_REDUCTION); ffmpeg-skill/audio --voice presets as VOICE_CLEANUP |
| 無音・フィラー除去 | done | silence removal for caller-given ranges; FILLER_REMOVE at caller-supplied word timestamps (#25) |

## Wiring

Agent plans MIX (and duck), NOISE_REDUCTION, DYNAMICS, VOICE_CLEANUP and FILLER_REMOVE from explicit requirements (step 5); pinned at 1.2.0. Delegates to ffmpeg-skill probe, audio, cut, loudness, join.

## Steps

- [x] **1. Contract dependencies + split audio.fade** — dependency on ffmpeg-skill; audio.fade_in / audio.fade_out ids
  - Done when: OS dependency_version_ranges PASS
- [x] **2. MIX ducking / effects** — typed duck and effects params -> ffmpeg-skill/audio ([#25](https://github.com/kajisho5/audio-production-skill/pull/25); effects = un-ducked inputs, ADR-19)
  - Done when: music bed lower under speech (integration)
- [x] **3. VOICE_CLEANUP op** — typed wrapper of ffmpeg-skill/audio --voice ([#22](https://github.com/kajisho5/audio-production-skill/pull/22))
  - Done when: contract + unit tests
- [x] **4. FILLER_REMOVE op** — caller-supplied word ranges (from transcript word timestamps); the skill cuts, never decides ([#25](https://github.com/kajisho5/audio-production-skill/pull/25))
  - Done when: sample-accurate fixture
- [x] **5. (agent side) plan MIX / NR / DYNAMICS / FILLER_REMOVE** — from explicit requirements (video-production-agent [#81](https://github.com/kajisho5/video-production-agent/pull/81) MIX / NOISE_REDUCTION / DYNAMICS, [#87](https://github.com/kajisho5/video-production-agent/pull/87) FILLER_REMOVE / MIX.duck)
  - Done when: tracked in video-production-agent
- [x] **6. ffmpeg-skill 3.0.0** — accept its contract_version 2.0; Python 3.9 CI against the last 2.x (ADR-21) ([#27](https://github.com/kajisho5/audio-production-skill/pull/27))
  - Done when: CI passes against 3.0.0 and the range includes it
- [ ] **7. Mastering matched to a reference** — new op `MATCH_REFERENCE`: measure a reference track the user supplies (integrated loudness, loudness range, true peak, the average spectrum in fixed bands, stereo width) and the target the same way, then build the corrections: a smooth EQ curve limited to a stated maximum gain per band, then loudness and a true-peak limiter to the reference's level or to the delivery target, whichever the caller names. Every stage runs through ffmpeg-skill `audio` / `loudness`; if the engine has no general EQ, the op is refused with the missing flag and that gap is handed to ffmpeg-skill. The report lists the reference's numbers, the target's numbers before and after, and every band's gain. 参考：matchering（考え方のみ。コードは使わない）
  - Done when: a fixture whose target is the reference with a tilt EQ and -6 dB ends within 1 dB per band and 0.5 LU of the reference; a reference with clipping is reported, not copied; the true peak never exceeds the limit
- [x] **8. Stems and multitrack delivery** — STEMS: from the MIX inputs, write dialogue, music and effects as separate files, or as one multitrack WAV / MOV with named tracks, all of the programme's length and aligned to its first sample. ([#33](https://github.com/kajisho5/audio-production-skill/pull/33): separate files; one multitrack file waits for an ffmpeg-skill multitrack writer, ADR-24) Field review 2026-11-05 (Velisa ROADMAP, third round)
  - なぜ要るか：MA・放送局・海外版の作業で求められるステム納品に、そのまま応えられる
  - Needs: Velisa ROADMAP, Proposals to ffmpeg-skill (multitrack audio) for one multitrack file; separate files work without it
  - Done when: the stems summed equal the mix within -60 dB residual; every stem has the programme's sample count
- [x] **9. Loudness versions in one run** — LOUDNESS_SET: one master into several versions, each measured and true-peak limited ([#32](https://github.com/kajisho5/audio-production-skill/pull/32); a target out of reach under the ceiling is refused, ADR-23). International targets first: -23 LUFS / -1 dBTP (EBU R128), -24 LKFS / -2 dBTP (ATSC A/85), the streaming platforms' own values (for example -14 LUFS), or stated targets; regional broadcast profiles beside them, never the default, for example ARIB TR-B32 (-24 LKFS / -1 dBTP, Japan). Each version's measurement is in the result
  - Why: streaming and broadcast versions come out of one run and cannot be swapped by mistake（なぜ要るか：配信用と放送用の音を取り違えずに一度に出せる）
  - Done when: three versions of a fixture are within 0.5 LU of their targets and never exceed their true-peak limit
- [ ] **10. Automixer for panel microphones** — AUTOMIX: several mic tracks of one discussion mixed by gain sharing (each mic's gain follows its share of the total level) with a number-of-open-mics attenuation, so only the talking mics are open; a fixed mic (the chair) can be pinned
  - なぜ要るか：学会のパネルや座談会で、話していないピンマイクのノイズと被りを自動で抑えられる
  - Done when: a 3-mic fixture where one person speaks at a time keeps the other two at least 10 dB down, and no level jump exceeds a stated rate
- [ ] **11. Per-input pan and language tracks** — MIX takes a pan per input, and a `languages` mode writes one mix per language (floor sound plus that language's interpreter or dub) as separate tracks with language tags
  - なぜ要るか：同時通訳の言語別音声や、吹替版と原音の両方を一つの納品にまとめられる
  - Done when: a fixture with ja and en interpreters gives two tagged tracks, each with the floor sound under its interpreter
- [x] **12. Loudness: owner and engine written down** — this repository owns 音量・ラウドネス and ffmpeg-skill is the engine (NORMALIZE and LOUDNESS_SET run through ffmpeg-skill `loudness`) ([Velisa ownership decisions 2026-11-12](https://github.com/kajisho5/Velisa/blob/main/docs/ecosystem/DECISION_LOG.md)). The agent's video path keeps calling `ffmpeg-skill/loudness`; that routing changes only after a measured comparison of `loudnorm` and `ebur128` (video-production-agent step 34)
  - Done when: README and SKILL.md say who owns loudness and which engine runs it, and the comparison is linked here once it is committed
  - Done: README and SKILL.md say this repository owns loudness and ffmpeg-skill `loudness` runs it ([#36](https://github.com/kajisho5/audio-production-skill/pull/36)). The comparison is committed in video-production-agent ([ADR-116](https://github.com/kajisho5/video-production-agent/blob/main/docs/decisions.md), [#160](https://github.com/kajisho5/video-production-agent/pull/160), its step 34): four generated fixtures (speech-like, music-like, quiet, dynamic) at -14 / -1 and -23 / -1, read by `loudnorm` and `ebur128`. `LOUDNESS_SET` and the agent's direct `ffmpeg-skill/loudness` call write the same samples, all on target by both meters. The meters differ by up to 0.5 LU on material with pauses, and this path takes about 3.3 s more per call. The agent's routing stays as it is, and no change is proposed. Found there: on dynamic material loudnorm falls back to dynamic mode, and the `loudness_sets` response does not report that mode, while ffmpeg-skill does ([#37](https://github.com/kajisho5/audio-production-skill/pull/37))
- [x] **13. Two Japanese filler lists** — ([#35](https://github.com/kajisho5/audio-production-skill/pull/35): both lists kept; the four words are the opt-in list `ja-ambiguous`, outside every preset, ADR-25) FILLER_REMOVE's `ja` preset (`fillers.py:26`) and ffmpeg-skill `silence --filler`'s built-in list (`_common/decision.py:1630-1640`) differ. ffmpeg-skill's list has あの・その・まあ・なんか; this preset leaves them out on purpose, because they are also content words ("あの人", "その", `fillers.py:23`), and has あー・うーん・んー instead. Decide whether to align: keep both and say why, add the four words as an opt-in preset here, or ask ffmpeg-skill to change its list (a new request, since ffmpeg-skill is treated as complete). The agent does not use `silence --filler`
  - Done when: an ADR records the choice; if a preset is added, a fixture with each of the four words used as a filler and as a content word shows which are cut

## Boundaries

Classic noise reduction here vs AI voice isolation in voice-enhance-skill: clean split. sound-design and dubbing outputs are placed through MIX here.

---
Engine for every media operation: [ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) (3.x, 42 tools, contract_version 2.0; treated as complete, proposals go to Velisa's ROADMAP). Orchestrator: [video-production-agent](https://github.com/kajisho5/video-production-agent).
