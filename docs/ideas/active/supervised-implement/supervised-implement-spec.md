# Supervised `i2code implement` — Specification

Scope: worktree mode of `i2code implement` (the default mode). Trunk and isolate modes are out of scope except
where noted. Every new behaviour is opt-in or additive; with no new option, exit codes, prompts and output stay
as they are today.

## 1. Options for current workarounds

| Option | Behaviour |
|--------|-----------|
| `--extra-prompt-file PATH` | Reads the file (UTF-8) and uses it as `--extra-prompt`. Giving both is a usage error. |
| `--claude-args TEXT` | `shlex`-split and appended to every real `claude` invocation (task, CI fix, review triage/fix, commit recovery, nudge, resume) after i2code's own flags and before the prompt. Forwarded to the inner command in isolate mode. Tokens that i2code owns are rejected as a usage error: `-p`, `--print`, `--output-format`, `--resume`, `--session-id`, `--allowedTools`. |
| `--allow-push` | (a) The worktree's `.claude/settings.local.json` does not get the `Bash(git push:*)` deny rule, and an existing one is removed. (b) The task prompt replaces "Do not push to the remote repository - the caller handles pushing." with a line allowing Claude to push the current branch to `origin` — never `--force`, never another branch — when the task needs CI on a new commit before it can finish; i2code still pushes after the task. Replaces the `I2CODE_ALLOW_CLAUDE_PUSH` environment variable, which is removed. Forwarded in isolate mode. |

## 2. Run state directory

`<git common dir>/i2code/implement/<idea name>/` — inside `.git`, so it is never committed, and the same path is
reached from the main checkout and from the idea worktree. Contents:

- `events.jsonl` — append-only journal, one JSON object per line, written only by the running `implement`.
- `status.json` — the current state, rewritten atomically (write temp file, `os.replace`) after every event.
- `inbox/` — messages from `i2code ctl`, one file each, named `<ns timestamp>-<kind>.json`, written atomically.
  The running `implement` deletes a message once it has acted on it.

## 3. Journal

Every event has `ts` (ISO 8601 with UTC offset) and `event`. Events and their extra fields:

| Event | Fields |
|-------|--------|
| `run_started` | `pid`, `idea`, `branch`, `worktree`, `on_failure`, `nudge_missing_tag` |
| `task_started` | `task` (e.g. `"3.1"`), `title`, `index`, `total` |
| `claude_started` | `label` (`task`, `ci_fix`, `triage`, `fix_feedback`, `recovery`, `nudge`, `resume`), `resumes_session` (or null) |
| `claude_finished` | `label`, `exit_code`, `outcome` (`success`, `failure`, `missing`), `session_id`, `num_turns`, `cost_usd`, `duration_s`, `summary` (first 300 characters of the final result text) |
| `note_delivered` | `label`, `count` |
| `task_completed` | `task`, `duration_s`, `head` |
| `pushed` | `head` |
| `ci_waiting` / `ci_finished` | `head` / `head`, `success`, `failing_workflow` |
| `blocked` | `kind` (`task`, `ci_fix`, `push`), `reason` (`failure_tag`, `missing_tag`, `attempts_exhausted`, `ci_retries_exhausted`, `push_failed`), `task`, `detail`, `session_id`, `permission_denials` |
| `resumed` | `mode` (`continue`, `fresh`), `note` |
| `stop_requested` | — |
| `run_finished` | `status` (`completed`, `stopped`, `failed`), `exit_code` |

`status.json` is a pure fold of the events: `pid`, `idea`, `state` (`running`, `ci_wait`, `blocked`, `completed`,
`stopped`, `failed`), `task`, `claude` (label, since, session id, or null), `blocked` (the last `blocked` event, or
null), `updated`. A `run_finished` event is written on every exit path, including `sys.exit` in existing code.

`session_id` comes from the first stream-json message that has a `session_id` field; `num_turns`, `cost_usd` and
`duration_s` come from the `result` message (`num_turns`, `total_cost_usd`, `duration_ms`).

## 4. `i2code ctl`

A new command group. `IDEA` accepts the same forms as `implement` does.

| Command | Behaviour |
|---------|-----------|
| `i2code ctl status IDEA [--json]` | Prints state, pid and whether it is alive, current task, the current Claude invocation, the block reason with the resume hint, the number of queued notes, and the path to `events.jsonl`. `--json` prints `status.json` plus `alive` and `queued_notes`. A dead pid with a non-final state is shown as `dead (last state: …)`. |
| `i2code ctl events IDEA [--limit N] [--follow]` | Prints the last N events (default 20), one per line; `--follow` keeps printing new ones. |
| `i2code ctl note IDEA TEXT` | Queues a note. Allowed at any time, including when nothing is running. |
| `i2code ctl resume IDEA [--note TEXT] [--fresh]` | Only when the state is `blocked` and the pid is alive; otherwise exits 1 with the current state. |
| `i2code ctl stop IDEA` | Asks for a graceful stop. Only when the pid is alive. |

## 5. Notes

Before each real Claude invocation labelled `task`, `ci_fix`, `fix_feedback`, `nudge` or `resume`, the queued
notes are removed from the inbox and appended to the prompt, oldest first:

```
Notes from the supervising session (oldest first):
- <note>
```

A `note_delivered` event records the delivery. Other labels never receive notes.

## 6. `--nudge-missing-tag N` (default 0)

Non-interactive only. When a Claude invocation for a task exits with code 0 and its output has neither
`<SUCCESS>` nor `<FAILURE>`, i2code resumes the same session (`--resume <session id>`, same allowed tools and
extra arguments) with a short prompt: if the task is complete, print `<SUCCESS>`; if work remains, continue it in
the foreground and then print a tag; if blocked, print `<FAILURE>`. Up to N times per attempt, then the result
is judged as it is today. No session id means no nudge.

## 7. `--on-failure [exit|wait]` (default `exit`)

With `wait`, the points below mark the run as blocked instead of calling `sys.exit(1)`:

| Kind | Reason | Where |
|------|--------|-------|
| `task` | `failure_tag` — Claude printed `<FAILURE>`; `detail` is its payload | task validation |
| `task` | `missing_tag` — no outcome tag after the nudges | task validation |
| `task` | `attempts_exhausted` — all automatic attempts failed | task validation |
| `ci_fix` | `ci_retries_exhausted` | `check_and_fix_ci` |
| `push` | `push_failed` | pushing after a task |

While blocked, i2code prints one line naming the reason and the `ctl` commands, then checks the inbox every
5 seconds. On `resume`:

- `task`: by default continue the blocked session (`--resume <session id>`, label `resume`) with a prompt that
  carries the note and restates the outcome-tag rule; `--fresh`, or no session id, starts a new session with the
  task prompt and the note. The result is validated as usual (nudges apply); failing again blocks again.
- `ci_fix`: run the CI fix loop again with a fresh retry budget.
- `push`: push again.

`--on-failure=wait` together with `--isolate` or `--trunk` is a usage error.

## 8. Graceful stop

`stop` is honoured at the top of the task loop, in the review poll loop, and while blocked. i2code then writes
`stop_requested` and `run_finished` (`stopped`) and exits 0. At start, `resume` and `stop` messages left from an
earlier run are deleted; notes are kept.

## Out of scope

Trunk and isolate modes for the journal and control commands; interrupting a running Claude process from
`ctl`; the separate `i2code status` idea; review-processor failure paths; Windows.
