# Pluggable decider

## Problem

`i2code implement` makes several small classification decisions, and each one is
either a full Claude Code session or a hard-coded rule:

- **CI failure** — every red run goes straight to a Claude fix attempt; only after
  retries are exhausted does the run block for the supervisor. Failures caused by
  the environment (missing secret, runner capacity, gateway timeout) burn every
  retry before a human sees them.
- **PR feedback triage** — a Claude session returns free-form JSON
  (`will_fix` / `needs_clarification`). When parsing fails, all feedback is marked
  processed and silently dropped.
- **Task outcome without a tag** — a clean exit without `<SUCCESS>`/`<FAILURE>`
  resumes the Claude session just to ask for the tag (`--nudge-missing-tag`).
- **Improve loop** — `.hitl` issues are grouped and prioritised by an interactive
  Claude session.

These are "System One" decisions: choose one of N labels, or score a value. A
typed decision model — TypeSafe AI's Jev (early access, Choice / Score / Noul
questions, typed output with calibrated probabilities, 70–500 ms) — fits them
better than a coding session: faster, cheaper, and cannot return an unparseable
answer.

## Goal

Decision points in `i2code implement` and `i2code improve` ask a `Decider`
instead of calling Claude or a fixed rule directly. The current Claude behaviour
stays the default; a Jev-backed decider can be switched on per run. When Jev is
unavailable or not confident enough, the decision falls back to the current
behaviour. The first decision to move is CI failure classification, measured
against past red runs (accuracy, latency, cost vs. Claude).

Constraints from the discussion:

- Jev is proprietary SaaS in early access (key available): opt-in only, never
  required to run i2code.
- CI logs and PR comments leave the machine: secrets must be redacted before a
  request is sent.
- This repository is a fork: keep the change in a new module behind an
  interface so upstream merges touch few existing files.

## Locations

- **CI failure classification** (first target)
  - `src/i2code/implement/github_actions_build_fixer.py:56` `check_and_fix_ci`
  - `src/i2code/implement/github_actions_build_fixer.py:84` `_block_until_resumed`
  - `src/i2code/implement/github_actions_build_fixer.py:93` `fix_ci_failure`
  - `src/i2code/implement/github_actions_build_fixer.py:154` `_invoke_claude_for_fix`
- **PR feedback triage**
  - `src/i2code/implement/pull_request_review_processor.py:166` `_triage_feedback`
  - `src/i2code/implement/pull_request_review_processor.py:224` `_run_triage`
  - `src/i2code/implement/pull_request_review_processor.py:459` `_parse_triage_result`
  - `src/i2code/implement/command_builder.py:194` `build_triage_command`
- **Task outcome without a tag**
  - `src/i2code/implement/task_execution.py:92` `_validate`
  - `src/i2code/implement/task_execution.py:133` `_run_and_recover`
  - `src/i2code/implement/task_execution.py:166` `_needs_nudge`
  - `src/i2code/implement/command_builder.py:38` `build_nudge_command`
- **Improve loop**
  - `src/i2code/improve/review_issues.py:62` `review_issues`
  - `src/i2code/improve/analyze_sessions.py:42` `analyze_sessions`
- **Options** (where a per-run switch would live)
  - `src/i2code/implement/implement_opts.py:39` (`nudge_missing_tag`, `on_failure`)
- **Unchanged**
  - `src/i2code/supervision/supervisor.py:28` `Supervisor.block` — blocking
    semantics stay; the decider only chooses whether to block earlier.
  - Task implementation itself — coding stays with Claude Code.
