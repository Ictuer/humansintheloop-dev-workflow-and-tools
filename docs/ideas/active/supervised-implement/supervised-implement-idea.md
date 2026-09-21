# Supervised `i2code implement`

## Problem

`i2code implement --non-interactive` is usually watched by a second, interactive Claude Code session (the
*supervisor*). Today the supervisor can only tail a log and kill/restart the process:

- Any task that ends without `<SUCCESS>` ends the whole run (`sys.exit(1)`), even when Claude committed work
  and only forgot the tag or ended its turn while waiting for CI. In one downstream project, 11 of 19 unattended
  runs (2026-09-19..21) died this way; 10 of those 11 had already committed.
- A task that is blocked on something only a human can do (a secret, a file Claude Code refuses to write) also ends
  the run. After the human fixes it, a restart pays for worktree setup, commit recovery and a fresh Claude session.
- Progress is visible only as dots in a log; there is no machine-readable state to watch.
- The supervisor cannot steer the next task without stopping the run.
- Callers patch around missing options: a `claude` wrapper on `PATH` to add `--effort`, an environment variable to
  let Claude push its branch, and a very long `--extra-prompt` passed on the command line.

## Idea

Give the running `implement` a small control plane that a supervising session can use:

1. `--on-failure=wait`: instead of exiting, the run becomes *blocked*, records why, and waits for
   `i2code ctl resume|stop`.
2. `--nudge-missing-tag N`: when Claude exits cleanly without an outcome tag, resume the same Claude session and
   ask it to finish with `<SUCCESS>`/`<FAILURE>` before treating it as a failure.
3. A run journal (`events.jsonl` + `status.json`) and `i2code ctl status|events` for the supervisor to read or
   follow with a monitor.
4. `i2code ctl note "..."`: queue guidance that is appended to the next Claude prompt.
5. First-class options for today's workarounds: `--claude-args`, `--allow-push`, `--extra-prompt-file`.

All new behaviour is opt-in; without the new options `implement` behaves as before.
