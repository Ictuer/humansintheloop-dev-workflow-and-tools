---

# Implementation Plan: Supervised `i2code implement`

## Idea Type

**Type A** — User-facing feature

## Instructions for Coding Agent

- IMPORTANT: Use simple commands that you have permission to execute. Avoid complex commands that may fail due to permission issues.

### Required Skills

| Skill | When to Use |
|-------|-------------|
| `idea-to-code:plan-tracking` | ALWAYS - track task completion in the plan file |
| `idea-to-code:tdd` | When implementing code - write failing tests first |
| `idea-to-code:apply-design-patterns` | Before implementing or refactoring code |
| `idea-to-code:commit-guidelines` | Before creating any git commit |
| `idea-to-code:test-output-to-logfile` | When running the test suite |

### TDD Requirements

- NEVER write production code without first writing a failing test
- Unit tests use the existing fakes in `tests/implement/` (`FakeClaudeRunner`, `FakeGitRepository`, `fake_loop_collaborators.py`); inject `clock` and `sleep` instead of sleeping

### Verification Requirements

- Before committing, print the exact test command (`uv run python -m pytest -m unit`), its exit code, and the last 20 lines of output
- Without the new options, all existing tests must keep passing unchanged

## Architecture Notes

- The spec is `supervised-implement-spec.md`; section numbers below refer to it.
- `RunPaths` (value object) resolves `<git common dir>/i2code/implement/<idea>/`.
- `RunJournal` appends events and rewrites `status.json` from a pure `fold_status(events)` function.
- `Inbox` is a file queue (`inbox/<ns>-<kind>.json`, atomic write) shared by `i2code ctl` (writer) and the run (reader).
- `Supervisor` is the facade used by `WorktreeMode`, `GithubActionsBuildFixer` and the Claude runner decorator: `record(event)`, `drain_notes()`, `stop_requested()`, `block(kind, reason, detail) -> ResumeRequest | Stop`. `NullSupervisor` keeps today's behaviour when no supervisor is wired (existing tests).
- `SupervisedClaudeRunner` decorates `ClaudeRunner`: journals `claude_started`/`claude_finished` and injects notes by `ClaudeCodeCommand.label`.
- `ClaudeResult` gains `session_id` and `stats` (num_turns, cost_usd, duration_s) parsed from stream-json.

---

## Steel Thread 1: Options That Replace Caller Workarounds

Lets callers drop the `claude` wrapper on PATH, the `I2CODE_ALLOW_CLAUDE_PUSH` variable and the long `--extra-prompt` argument (spec §1).

- [x] **Task 1.1: `--extra-prompt-file` supplies the extra prompt from a file**
  - TaskType: OUTCOME
  - Entrypoint: `i2code implement <idea-dir> --extra-prompt-file prompt.md --dry-run`
  - Observable: The file's text reaches the task prompt exactly as `--extra-prompt` would; giving both options fails with a usage error naming both
  - Evidence: `uv run python -m pytest tests/implement/test_cli_extra_prompt_file.py -m unit`
  - Steps:
    - [x] Write failing CLI tests (CliRunner with a command factory that captures `ImplementOpts`): file content becomes `extra_prompt`; both options → `UsageError`; missing file → click error
    - [x] Add `--extra-prompt-file` (`click.Path(exists=True, dir_okay=False)`) to `implement_cmd` and map it to `extra_prompt` before building `ImplementOpts`
    - [x] Document the option in `docs/i2code-cli/implement.adoc`

- [x] **Task 1.2: `--claude-args` appends extra arguments to every real Claude invocation**
  - TaskType: OUTCOME
  - Entrypoint: `i2code implement <idea-dir> --non-interactive --claude-args "--effort high"`
  - Observable: Every `claude` argv built by `ClaudeRunner` ends with `--effort high` before `-p <prompt>`; owned flags (`-p`, `--print`, `--output-format`, `--resume`, `--session-id`, `--allowedTools`) are rejected with a usage error; the option is forwarded to the inner command in isolate mode
  - Evidence: `uv run python -m pytest tests/implement/test_claude_runner.py tests/implement/test_implement_opts.py tests/implement/test_cli_claude_args.py -m unit`
  - Steps:
    - [x] Write failing `ClaudeRunner._build_argv` tests for global extra args (interactive and non-interactive, with and without session/add-dir)
    - [x] Add `global_args` to `ClaudeRunner.__init__` and place them after i2code's own flags, before the prompt
    - [x] Write failing tests for `ImplementOpts.claude_args` parsing (`shlex`), owned-flag rejection and `inner_cli_flags` forwarding
    - [x] Add `claude_args` to `ImplementOpts`, validate it, and pass the split list to `ClaudeRunner` in `assemble_implement`
    - [x] Document the option in `docs/i2code-cli/implement.adoc`

- [x] **Task 1.3: `--allow-push` lets Claude push the idea branch mid-task**
  - TaskType: OUTCOME
  - Entrypoint: `i2code implement <idea-dir> --non-interactive --allow-push`
  - Observable: The worktree `.claude/settings.local.json` has no `Bash(git push:*)` deny rule (an existing one is removed); the task prompt allows pushing the current branch without `--force` instead of forbidding pushes; without the flag both stay as today; `I2CODE_ALLOW_CLAUDE_PUSH` no longer has any effect
  - Evidence: `uv run python -m pytest tests/claude tests/implement/test_command_builder.py tests/implement/test_worktree_setup.py -m unit`
  - Steps:
    - [x] Write failing tests for `ensure_claude_permissions(repo_root, allow_push=True)` (no deny rule added, existing one removed) and for the default (deny rule present)
    - [x] Replace the environment-variable check with an `allow_push` parameter threaded through `setup_claude_settings_local_json` and `ProjectSetup`
    - [x] Write failing template tests: `task_execution.j2` renders the push permission line when `allow_push` is set and the current line otherwise
    - [x] Add `allow_push` to `TaskCommandOpts` and the template; pass it from `WorktreeMode._build_command`
    - [x] Add `--allow-push` to the CLI, `ImplementOpts` and inner-command forwarding; document it

---

## Steel Thread 2: Run Journal and ctl status
Makes a running implement observable: every Claude invocation and lifecycle step lands in events.jsonl and status.json, readable with i2code ctl status and ctl events (spec §2–§4).

- [x] **Task 2.1: Claude results carry the session id and run statistics**
  - TaskType: INFRA
  - Entrypoint: `ClaudeRunner.execute(command) in non-interactive mode`
  - Observable: ClaudeResult.session_id is the first session_id seen in the stream-json output; ClaudeResult.stats has num_turns, cost_usd and duration_s from the result message; both are empty when the output has no such messages
  - Evidence: `uv run python -m pytest tests/implement/test_claude_runner.py -m unit`
  - Steps:
    - [x] Write failing tests for _parse_stream_json_output with init/result messages, without them, and with invalid lines
    - [x] Add session_id and stats to ClaudeResult and fill them in _run_claude_with_output_capture
    - [x] Add an integration_claude test that runs a one-line prompt and asserts a non-empty session_id

- [x] **Task 2.2: RunJournal writes events.jsonl and status.json under the git common dir**
  - TaskType: INFRA
  - Entrypoint: `RunJournal(RunPaths.for_idea(repo, idea_name)).record(event, **fields)`
  - Observable: Each record appends one JSON line with ts and event; status.json equals fold_status(all events) after every record and is replaced atomically; RunPaths resolves <git common dir>/i2code/implement/<idea> from the main checkout and from a worktree
  - Evidence: `uv run python -m pytest tests/supervision/test_run_paths.py tests/supervision/test_run_journal.py -m unit`
  - Steps:
    - [x] Write failing tests for RunPaths from a main checkout and from a linked worktree (tmp git repos)
    - [x] Write failing table tests for fold_status covering every state transition in spec §3
    - [x] Implement RunPaths, fold_status and RunJournal (append + atomic os.replace) in the new src/i2code/supervision package (design-pattern-catalog: package cohesion, tests mirror source)

- [x] **Task 2.3: Every Claude invocation is journaled with its label**
  - TaskType: OUTCOME
  - Entrypoint: `i2code implement <idea-dir> --non-interactive`
  - Observable: events.jsonl has a claude_started and a claude_finished event for each Claude invocation, with label task, ci_fix, triage, fix_feedback, recovery, scaffolding or feedback, the outcome tag, session id and stats
  - Evidence: `uv run python -m pytest tests/supervision/test_supervised_claude_runner.py tests/implement/test_command_builder.py tests/implement/test_claude_runner.py tests/implement/test_command_assembler.py -m unit`
  - Steps:
    - [x] Write failing CommandBuilder tests asserting the label of each built command
    - [x] Add an optional label to ClaudeCodeCommand and set it in every CommandBuilder method and in the task and CI-fix mock paths
    - [x] Write failing tests for ClaudeResult.outcome (success, failure, missing, not captured)
    - [x] Write failing tests for SupervisedClaudeRunner journaling around a FakeClaudeRunner
    - [x] Implement SupervisedClaudeRunner in src/i2code/supervision and wrap the runner in assemble_implement with a RunJournal for the idea

- [x] **Task 2.4: Worktree mode journals the run, task, push and CI lifecycle**
  - TaskType: OUTCOME
  - Entrypoint: `i2code implement <idea-dir> --non-interactive`
  - Observable: events.jsonl records run_started, task_started, task_completed, pushed, ci_waiting, ci_finished and run_finished; run_finished is written with status failed and the exit code when existing code calls sys.exit
  - Evidence: `uv run python -m pytest tests/implement/test_worktree_mode_journal.py -m unit`
  - Steps:
    - [x] Write failing WorktreeMode tests using a recording supervisor fake for a two-task run
    - [x] Add an optional supervisor to LoopSteps with NullSupervisor as default, and record the lifecycle events
    - [x] Make GithubActionsMonitor report the CI result so ci_finished can carry success and failing_workflow
    - [x] Write failing tests for run_finished on normal completion and on SystemExit, then wrap execute accordingly

- [x] **Task 2.5: i2code ctl status and events show the run to a supervisor**
  - TaskType: OUTCOME
  - Entrypoint: `i2code ctl status <idea> [--json]; i2code ctl events <idea> [--limit N] [--follow]`
  - Observable: status prints state, pid liveness, current task and Claude invocation, block reason with resume hint and the events path; --json prints status.json plus alive and events_file; a dead pid with a non-final state shows as dead; no journal prints 'no run recorded'; events prints the last N events and --follow streams new ones until run_finished
  - Evidence: `uv run python -m pytest tests/ctl-cmd -m unit`
  - Steps:
    - [x] Write failing CliRunner tests for status (running, blocked, dead, no run yet) and --json
    - [x] Add the ctl Click group in src/i2code/ctl_cmd, register it in i2code/cli.py, and implement status
    - [x] Write failing tests for events --limit and for --follow with an injected sleep, ending at run_finished
    - [x] Implement events
    - [x] Add docs/i2code-cli/ctl.adoc and link it from i2code-cli.adoc and README.adoc

---

## Steel Thread 3: Supervisor Control: note, nudge, block, resume, stop
Lets a supervising session steer and unblock a running implement instead of killing and restarting it (spec §5–§8).

- [x] **Task 3.1: i2code ctl note steers the next Claude invocation**
  - TaskType: OUTCOME
  - Entrypoint: `i2code ctl note <idea> "text"`
  - Observable: The note is stored in inbox/; ctl status shows the queued note count; the next Claude invocation labelled task, ci_fix, fix_feedback, nudge or resume gets the notes appended under 'Notes from the supervising session (oldest first):', the inbox notes are deleted, and note_delivered is journaled; other labels and mock commands never receive notes
  - Evidence: `uv run python -m pytest tests/supervision/test_inbox.py tests/supervision/test_supervised_claude_runner.py tests/ctl-cmd -m unit`
  - Steps:
    - [x] Write failing Inbox tests: atomic write, ordered read by kind, delete after drain, notes kept when other kinds are drained
    - [x] Implement Inbox in src/i2code/supervision
    - [x] Write failing CliRunner tests for ctl note (including when nothing is running) and the queued note count in ctl status
    - [x] Implement ctl note and the count
    - [x] Write failing SupervisedClaudeRunner tests for note injection by label and mock commands untouched
    - [x] Implement note injection and the note_delivered event

- [x] **Task 3.2: --nudge-missing-tag resumes a session that ended without an outcome tag**
  - TaskType: OUTCOME
  - Entrypoint: `i2code implement <idea-dir> --non-interactive --nudge-missing-tag 1`
  - Observable: When a task invocation exits 0 without <SUCCESS> or <FAILURE>, i2code runs claude --resume <session id> with the nudge prompt (label nudge, same allowed tools and extra args) up to N times, then validates the last result as today; no session id or a <FAILURE> tag means no nudge; default 0 keeps today's behaviour
  - Evidence: `uv run python -m pytest tests/implement/test_worktree_mode_nudge.py tests/implement/test_command_builder.py -m unit`
  - Steps:
    - [x] Write failing CommandBuilder test for build_nudge_command from an original task command and a session id
    - [x] Add outcome_nudge.j2 and build_nudge_command
    - [x] Write failing WorktreeMode tests: nudge then SUCCESS passes; nudge still missing exits as today; FAILURE tag is not nudged; no session id is not nudged
    - [x] Add nudge_missing_tag to CLI and ImplementOpts and implement the nudge loop in _run_claude_and_validate

- [ ] **Task 3.3: --on-failure=wait blocks a failed task until i2code ctl resume**
  - TaskType: OUTCOME
  - Entrypoint: `i2code implement <idea-dir> --non-interactive --on-failure wait; i2code ctl resume <idea> [--note TEXT] [--fresh]`
  - Observable: A task that ends with <FAILURE>, with a missing tag after nudges, or with all attempts exhausted journals blocked (kind task, reason, detail, session id, permission denials), prints one line with the ctl commands, and polls the inbox every 5 s; resume continues the blocked session with the resume prompt (or a fresh session with --fresh or without a session id) and validates the result, blocking again on failure; ctl resume refuses when the run is not blocked or not alive; --on-failure wait with --trunk or --isolate is a usage error
  - Evidence: `uv run python -m pytest tests/implement/test_worktree_mode_blocked.py tests/ctl -m unit`
  - Steps:
    - [ ] Write failing Supervisor.block tests with an injected sleep: resume message returns a ResumeRequest, stop returns Stop, notes stay queued
    - [ ] Implement Supervisor, NullSupervisor and the blocked/resumed events
    - [ ] Add supervisor_resume.j2 and build_resume_command with a failing CommandBuilder test first
    - [ ] Write failing WorktreeMode tests for each block reason followed by resume (continue and fresh) and by a second failure
    - [ ] Add on_failure to CLI and ImplementOpts with validation, and route task failures through the supervisor
    - [ ] Write failing CliRunner tests for ctl resume (blocked, not blocked, dead) and implement it

- [ ] **Task 3.4: CI-fix exhaustion and push failure also block with --on-failure=wait**
  - TaskType: OUTCOME
  - Entrypoint: `i2code implement <idea-dir> --non-interactive --on-failure wait`
  - Observable: When CI-fix retries are exhausted the run blocks with kind ci_fix and resume runs the fix loop again with a fresh budget; when a push fails the run blocks with kind push and resume pushes again; with --on-failure exit both still call sys.exit(1)
  - Evidence: `uv run python -m pytest tests/implement/test_github_actions_build_fixer.py tests/implement/test_worktree_mode_blocked.py -m unit`
  - Steps:
    - [ ] Write failing build fixer tests for block-then-resume and for the unchanged exit path
    - [ ] Give GithubActionsBuildFixer the supervisor through its factory and block instead of exiting when waiting is enabled
    - [ ] Write failing WorktreeMode tests for push failure block-then-resume
    - [ ] Route push failures through the supervisor

- [ ] **Task 3.5: i2code ctl stop ends the run gracefully at the next checkpoint**
  - TaskType: OUTCOME
  - Entrypoint: `i2code ctl stop <idea>`
  - Observable: The run honours the request at the top of the task loop, in the review poll loop and while blocked, journals stop_requested and run_finished with status stopped, and exits 0; ctl stop refuses when no live run exists; at start the run deletes resume and stop messages left from an earlier run and keeps notes
  - Evidence: `uv run python -m pytest tests/implement/test_worktree_mode_stop.py tests/ctl -m unit`
  - Steps:
    - [ ] Write failing WorktreeMode tests for stop at each checkpoint
    - [ ] Implement the checkpoints and the stopped exit
    - [ ] Write failing tests for stale-message cleanup at start and implement it
    - [ ] Write failing CliRunner tests for ctl stop and implement it

---

## Steel Thread 4: End-to-End Proof and Documentation
Proves the control plane with a mock-Claude run and documents how a supervising session uses it.

- [ ] **Task 4.1: A mock-Claude run proves block, note, resume and stop end to end**
  - TaskType: OUTCOME
  - Entrypoint: `i2code implement <idea-dir> --mock-claude <script> --non-interactive --on-failure wait --skip-ci-wait`
  - Observable: In a temporary repo with a fake GitHub, a mock Claude that fails the first task makes the run block; ctl status shows blocked; ctl note plus ctl resume let the second invocation succeed; ctl stop ends the run with run_finished stopped
  - Evidence: `uv run python -m pytest tests/implement/test_supervised_run_integration.py -m integration`
  - Steps:
    - [ ] Study test_task_execution_integration.py for the existing temporary-repo and mock-Claude setup
    - [ ] Write the integration test driving implement in a background thread and ctl from the test
    - [ ] Fix any gaps it exposes

- [ ] **Task 4.2: Document supervising a non-interactive run**
  - TaskType: INFRA
  - Entrypoint: `docs/i2code-cli/implement.adoc and docs/design/implement-steps.md`
  - Observable: implement.adoc lists every new option; implement-steps.md describes the journal, the blocked state and the success-criteria changes; a 'Supervising a run' section shows the ctl commands a supervising Claude session uses, including following events.jsonl with a monitor
  - Evidence: `uv run python -m pytest -m unit`
  - Steps:
    - [ ] Update implement.adoc and implement-steps.md
    - [ ] Add the 'Supervising a run' section with a worked example

---

## Change History

### 2026-09-21 21:45 - insert-thread-after
Initial plan: run journal thread

### 2026-09-21 21:45 - insert-thread-after
Initial plan: supervisor control thread

### 2026-09-21 21:45 - insert-thread-after
Initial plan: end-to-end and docs thread

### 2026-09-21 21:49 - mark-task-complete
test_cli_extra_prompt_file.py: file content → extra_prompt, both options → usage error, missing file → usage error; unit suite 1467 passed

### 2026-09-21 21:51 - mark-task-complete
ClaudeRunner global_args + ImplementOpts.claude_args (shlex, owned-flag rejection, forwarded) + CLI + assembler wiring test; unit suite 1485 passed

### 2026-09-21 21:54 - mark-task-complete
allow_push threaded CLI→opts→ProjectSetup→permissions (deny rule removed) and TaskCommandOpts→task_execution.j2; env var removed; default prompt byte-identical; unit suite 1497 passed

### 2026-09-21 21:57 - mark-task-complete
ClaudeResult.session_id + RunStats from stream-json (unit tests) and verified against real claude (integration_claude passed); unit suite 1500 passed

### 2026-09-21 21:57 - replace-task
Place supervision code in its own package per design-pattern-catalog (package cohesion, tests mirror source)

### 2026-09-21 21:59 - mark-task-complete
RunPaths (main + linked worktree), fold_status table tests, RunJournal append/atomic status/continuation; unit suite 1519 passed

### 2026-09-21 21:59 - replace-task
Supervision package location; add ClaudeResult.outcome step; list all labels

### 2026-09-21 22:01 - mark-task-complete
Labels on all CommandBuilder commands + task/ci_fix mocks, ClaudeResult.outcome, SupervisedClaudeRunner journaling, assembler wiring test writes events under .git; unit suite 1537 passed

### 2026-09-21 22:05 - mark-task-complete
test_worktree_mode_journal: lifecycle order, fields, skipped CI, SystemExit→run_finished failed; supervisor wired via ModeFactory (assembler test); unit suite 1542 passed

### 2026-09-21 22:05 - replace-task
ctl_cmd package per catalog; queued-note count moves to 3.1 where the inbox exists; --follow ends at run_finished

### 2026-09-21 22:05 - replace-task
Supervision/ctl-cmd test locations; queued-note count moved here from 2.5

### 2026-09-21 22:07 - mark-task-complete
tests/ctl-cmd/test_ctl_cli.py: status running/blocked/dead/finished/none/--json/path, events --limit/--follow; docs ctl.adoc linked; unit suite 1552 passed; real CLI smoke ok

### 2026-09-21 22:09 - mark-task-complete
Inbox (atomic post/take/count/discard), ctl note + queued count, note injection by steerable label with note_delivered, assembler wires Inbox; unit suite 1573 passed

### 2026-09-21 22:12 - mark-task-complete
build_nudge_command + outcome_nudge.j2; WorktreeMode nudges missing-tag sessions (incl. uncommitted work), not FAILURE/no session/default; opts forwarded, trunk rejects; unit suite 1587 passed
