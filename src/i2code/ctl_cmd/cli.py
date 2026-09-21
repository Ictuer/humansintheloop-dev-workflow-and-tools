"""i2code ctl: observe and steer a running ``i2code implement``."""

import json
import time
from pathlib import Path

import click
from git import Repo

from i2code.ctl_cmd.status_view import describe, is_alive
from i2code.idea.resolver import resolve_idea_directory
from i2code.supervision.run_journal import fold_status, read_events
from i2code.supervision.run_paths import RunPaths

FOLLOW_POLL_SECONDS = 1


def _locate(idea: str):
    name = Path(resolve_idea_directory(idea)).name
    repo = Repo(Path.cwd(), search_parent_directories=True)
    return name, RunPaths.for_idea(repo, name)


def _print_event(event):
    click.echo(json.dumps(event, ensure_ascii=False))


@click.group("ctl")
def ctl():
    """Observe and steer a running i2code implement (see docs/i2code-cli/ctl.adoc)."""


@ctl.command("status")
@click.argument("idea")
@click.option("--json", "as_json", is_flag=True, help="Print status.json plus alive and events_file")
def status_cmd(idea, as_json):
    """Show the state of the idea's implement run."""
    name, paths = _locate(idea)
    status = fold_status(read_events(paths))
    alive = is_alive(status["pid"])
    if as_json:
        click.echo(json.dumps({**status, "alive": alive, "events_file": str(paths.events_file)}, indent=2))
        return
    for line in describe(name, status, alive, str(paths.events_file)):
        click.echo(line)


@ctl.command("events")
@click.argument("idea")
@click.option("--limit", type=int, default=20, show_default=True, help="Number of most recent events to print")
@click.option("--follow", is_flag=True, help="Keep printing new events until run_finished")
@click.pass_context
def events_cmd(ctx, idea, limit, follow):
    """Print the idea's most recent journal events as JSON lines."""
    _, paths = _locate(idea)
    events = read_events(paths)
    for event in events[-limit:] if limit > 0 else []:
        _print_event(event)
    if follow:
        _follow(paths, len(events), (ctx.obj or {}).get("sleep", time.sleep))


def _follow(paths, seen, sleep):
    while True:
        sleep(FOLLOW_POLL_SECONDS)
        events = read_events(paths)
        for event in events[seen:]:
            _print_event(event)
            if event["event"] == "run_finished":
                return
        seen = len(events)
