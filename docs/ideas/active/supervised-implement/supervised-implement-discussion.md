# Supervised `i2code implement` — Discussion

## Evidence (downstream project, 2026-09-19..21)

19 unattended `implement --non-interactive` runs, 23 tasks completed. 11 runs ended because a task finished without
`<SUCCESS>`; in 10 of them Claude had committed. Causes seen in the final result text: a step blocked on a human
(secret, a file Claude Code refuses to write), and Claude ending its turn while waiting for CI ("waiting for CI
result…"). The supervising session had to read the log, fix the cause and restart each time.

## Decisions

- **Q: Change behaviour by default?** No. Every new behaviour is behind an option; the journal is additive.
- **Q: Where does run state live?** Under the git common dir, so it is never committed and both the main checkout
  and the idea worktree see the same files.
- **Q: How does the supervisor talk to the run?** Through files (inbox + journal), not a socket: works for any
  supervisor that can run a shell command, survives restarts, easy to test.
- **Q: Resume a blocked task in the same Claude session or a new one?** Same session by default (keeps what Claude
  already learned); `--fresh` when the session went wrong.
- **Q: Which failure points can block?** Task failures, CI-fix exhaustion and push failure — the points that
  currently call `sys.exit(1)` in worktree mode. Review-processor paths stay as they are.
- **Q: Scope of the first version?** Worktree mode only; trunk and isolate reject `--on-failure wait`.
- **Q: Replace the caller workarounds?** Yes: `--claude-args`, `--allow-push` (removes `I2CODE_ALLOW_CLAUDE_PUSH`),
  `--extra-prompt-file`.
