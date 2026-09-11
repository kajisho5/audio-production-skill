# Security Policy

## Reporting a vulnerability

Please report security vulnerabilities privately through GitHub, not as a public issue:

1. Go to this repository's **Security** tab.
2. Select **Advisories** → **Report a vulnerability**.
3. Fill in as much detail as you can: the affected version, how to reproduce it, and its impact.

(Direct link, once you're on the repository: `Security` → `Advisories` → `New draft security advisory`,
or `/security/advisories/new` under the repository URL.)

This opens a private advisory visible only to you and the maintainer, so the report is not disclosed
before a fix is available. You'll get a response through that advisory thread.

If the private reporting flow is unavailable for some reason, use the repository's usual issue tracker,
but omit exploit details from a public report.

## Scope

This repository executes typed, validated audio operations through a local `ffmpeg-skill` checkout; see
[docs/security.md](docs/security.md) for what it guarantees (no shell, no arbitrary commands or filter
strings, path containment, a minimal child process environment) and what it does not. A vulnerability
report about this repository's own code -- the request validation, path policy, or process boundary in
`src/audio_production/` -- is in scope. A vulnerability in `ffmpeg-skill`, FFmpeg itself, or another
Skill in this ecosystem should go to that project instead.

## Supported versions

This project is pre-1.0 (see `pyproject.toml`); only the latest released version is supported. There is
no separate maintenance branch for older releases.
