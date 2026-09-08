"""audio-production-skill: deterministic audio production execution Skill (not an AI agent).

It executes typed, validated audio operations (gain, trim, cut, fades, loudness normalisation, mix, ...)
through ffmpeg-skill and reports provenance. It does not measure media for decisions (media-analysis-skill),
does not decide what to do (video-production-agent) and never runs a shell or an arbitrary command."""

SKILL_ID = "audio-production"
PACKAGE_NAME = "audio-production-skill"
VERSION = "0.2.0"

CONTRACT_SCHEMA_VERSION = 1
REQUEST_SCHEMA_VERSION = 1
RESPONSE_SCHEMA_VERSION = 1
DOCTOR_SCHEMA_VERSION = 1

__all__ = ["SKILL_ID", "PACKAGE_NAME", "VERSION"]
