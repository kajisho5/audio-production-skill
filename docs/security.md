# Security boundary

## Enforced

| rule | where | tested by |
|---|---|---|
| No shell, no `eval` / `exec`, no `os.system`; exactly one `subprocess.Popen` (argv list) in the package | `adapter._popen` | `test_security.test_no_shell_or_eval_in_source` |
| Only `scripts/{probe,audio,cut,loudness}.py` of the located ffmpeg-skill can be started | `adapter.FfmpegSkill.script` | `test_only_allowlisted_ffmpeg_skill_scripts_can_run` |
| The request never names a command, argv, executable, script, filter, environment or working directory: those field names are rejected anywhere in the document; unknown fields are rejected everywhere; parameter types and ranges are validated per operation type | `model.FORBIDDEN_KEYS`, `model.validate_parameters` | `test_request_rejects_command_like_fields_anywhere`, `test_gain_parameter_injection_is_rejected`, `test_request_with_command_fields_never_reaches_a_tool` |
| Every argv element is a fixed flag, a number formatted by this skill (`%.3f`) or a resolved absolute path | `adapter.fmt_seconds`, `fmt_db`, `executor._argv` | `test_argv_builder_uses_only_numbers_and_resolved_paths` |
| The ffmpeg-skill directory comes from the CLI / environment, never from the request; an explicit directory is validated (contract version, flags) and never silently replaced by a fallback | `adapter.locate`, `adapter.info` | `test_ffmpeg_skill_dir_is_not_taken_from_the_request`, `test_bogus_ffmpeg_skill_dir_is_rejected` |
| Inputs: regular files, symlinks resolved before every check, optional `--allowed-input` roots (`PATH_NOT_ALLOWED`) | `security.PathPolicy.resolve_input` | `test_path_policy_inputs_and_outputs`, `test_symlink_escape_is_refused`, `test_input_outside_allowed_roots_through_cli` |
| Outputs and the work directory resolve inside `--workspace`; `..`, absolute paths outside and symlinked directories pointing outside are refused; containment uses path components, not string prefixes | `security.PathPolicy.resolve_write_path` | same tests, `test_unsafe_output_paths_through_cli`, `test_symlink_escape_through_cli` |
| File names: no control / invalid characters, no reserved Windows device names (`CON`, `NUL`, `COM1`…), no trailing dot / space, no leading `-`, length limits | `security.check_filename` | `test_filename_rules`, `test_windows_style_paths_are_handled` |
| An output is never an input (after symlink resolution) and never an existing file unless `overwrite: true`; inputs are never modified | `executor._run` | `test_output_may_not_overwrite_input`, `test_existing_output_is_not_overwritten_by_default` |
| Child processes: own process group (`start_new_session` / `CREATE_NEW_PROCESS_GROUP`), killed as a tree on timeout or signal; minimal environment (`PATH`, home / temp / locale, Windows system variables, `PYTHONUTF8`) | `adapter` | `test_child_environment_is_minimal`, `test_timeout_is_a_retryable_tool_error`, `test_signal_cancellation_leaves_no_partial_output` |
| Failure hygiene: on any error the node's intermediate and sidecars are removed, a failed output is removed; a manifest is written only after validation, so an intermediate without a manifest is never reused | `executor._execute_node`, `_export`, `_reusable` | `test_output_expectation_failure_removes_output`, `test_loudness_verification_failure`, `test_rerun_reuses_intermediates_and_is_deterministic` |
| stdout is exactly one JSON document under `--json`, on success and failure alike, including malformed input; diagnostics go to stderr; no secrets are printed (`doctor.secrets_shown = false`) | `cli` | `test_malformed_documents_yield_one_json_error` |
| Request size limit 16 MiB; at most 1000 operations, 10000 ranges per operation, 8 MIX inputs | `cli._read_document`, `model` | unit tests |

## Not enforced

- Without `--allowed-input`, any regular file the process can read is accepted (same posture as media-analysis-skill
  and video-production-agent ADR-010). Embedders should set the roots.
- Resource use: long files mean long ffmpeg runs; `--timeout` (per tool call) and `options.timeout` bound wall time,
  not memory or disk.
- The work directory is trusted: manifests contain absolute paths and hashes; tampering with an intermediate is
  detected (sha256) and causes re-processing, but the directory should not be world-writable.
- ffmpeg-skill itself is trusted code: this skill validates its contract and its outputs, not its source.
