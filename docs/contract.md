# Contract: versioning, pinned blocks, drift

`audio-production skill --json` (alias `contract --json`) prints `audio-production/contract@1`. It is derived from the
tables the code runs on (`model.OPERATION_TYPES`, `executor.TOOL_FOR`, `adapter.FLAGS_USED`, `errors.ERROR_TABLE`,
`contract.CAPABILITY_IDS`); `contract --check` proves that.

## What agents pin

video-production-agent's audio-production adapter keeps a copy of this contract (`tools/audio_production/contract_0.1.0.json`)
and compares these keys verbatim on every run (`DRIFT_KEYS`): `schema, skill_id, version, kind, tools,
unsupported_operations, output_formats, intermediate_format, channel_layouts, sample_rates, execution, ffmpeg_skill,
request, response, provenance, schema_versions, errors`, plus per operation `type, inputs, parameters, tool,
required_capabilities, keeps_timeline, deterministic`. Any difference makes the agent mark `audio-production` MISSING
until it re-pins. `contract_check.PINNED_BLOCKS` is that list plus `operations` and `provides`.

| change | classification | what it means |
|---|---|---|
| a value inside a pinned block, an operation's pinned field, a removed operation, a removed key anywhere, the schema id | **breaking** | agents must re-verify and re-pin; bump `version` |
| an operation added | **breaking** (for pinning agents) | they cannot use it before re-pinning; bump `version` |
| a new top-level key outside the pinned blocks, or a change inside such a key | **additive** | allowed within a version (this is how `provides` arrived in 0.1.0) |

`version` itself is a pinned block: bumping it is a breaking event for the agent's pin. Coordinate a bump with a
re-pin in video-production-agent (its `contract_0.1.0.json` and `SUPPORTED_SKILL_VERSIONS`).

## The check

```text
audio-production contract --check                      # live contract vs. the implementation and README
audio-production contract --check tests/contract/contract.json --json   # + drift against the saved copy
```

Report `audio-production/contract-check@1`: `status` `ok` | `additive` | `breaking` | `fail` (`fail` = the live
contract disagrees with the code or the README), `problems`, `drift.breaking`, `drift.additive`, `exit_code` (0 for
`ok` / `additive`, 1 otherwise). CI runs it against `tests/contract/contract.json`; regenerate that file with
`audio-production contract --json > tests/contract/contract.json` when a contract change is intended, and say so in
the PR.

Implementation checks: header equals the package, `operations` equals `OPERATION_TYPES` (tool, arity, parameters,
required capabilities), `unsupported_operations` equals `UNSUPPORTED_OPERATIONS` and is disjoint from
`operations`, `provides` covers every operation with a valid lifecycle and the single `audio-production/run` tool,
`tools` is that one ToolSpec, `output_formats`, `errors` (codes, exit codes, retryable), every `execution` safety
flag is `false`, `ffmpeg_skill` equals the adapter's tools and flags, and the README names every implemented and
declared-unsupported operation type.

## Schema versions

`contract@1`, `request@1`, `response@1`, `doctor@1`, `contract-check@1`, all independent. Within `@1` only additive
changes; a `@2` is a different `schema` value and is reported as breaking by `--check`.
