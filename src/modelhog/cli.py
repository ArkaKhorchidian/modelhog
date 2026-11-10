"""modelhog: find the AI models and developer caches eating your disk, and delete them safely."""
from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__
from .scan import AI, CACHE, PROJECT, Item, ago, human, scan, total

GROUP_STYLE = {AI: "#ff6ec7", CACHE: "#5cc8ff", PROJECT: "#ffd166"}
STALE_DAYS = 90


def stale(i: Item, days: int = STALE_DAYS) -> bool:
    return bool(i.last_used) and time.time() - i.last_used > days * 86400


def render(items: list[Item], console: Console, limit: int = 40) -> None:
    if not items:
        console.print("No big AI models or caches found. Your disk is cleaner than most.")
        return
    t_all = total(items)
    old = [i for i in items if stale(i)]
    by = {g: [i for i in items if i.group == g] for g in (AI, CACHE, PROJECT)}
    head = Text.assemble(("You have ", ""), (human(t_all), "bold #ff6ec7"), (" of AI models and caches", ""),
                         (" that can all be downloaded again.", "dim"))
    parts: list = [head]
    if old:
        parts.append(Text.assemble((human(total(old)), "bold"), f" of it hasn't been touched in {STALE_DAYS} days."))
    parts.append(Text(""))

    summary = Table.grid(padding=(0, 2))
    summary.add_column(no_wrap=True)
    summary.add_column(justify="right")
    summary.add_column()
    biggest = max((total(v) for v in by.values() if v), default=1)
    for g, its in by.items():
        if its:
            n = total(its)
            summary.add_row(Text(g, f"bold {GROUP_STYLE[g]}"), human(n),
                            Text("█" * max(1, round(36 * n / biggest)), GROUP_STYLE[g]))
    parts += [summary, Text("")]

    t = Table(box=None, header_style="dim", padding=(0, 2), expand=True)
    t.add_column("#", justify="right", style="dim")
    t.add_column("What", ratio=1, no_wrap=True, overflow="ellipsis")
    t.add_column("From", no_wrap=True)
    t.add_column("Size", justify="right", no_wrap=True)
    t.add_column("Last used", justify="right", no_wrap=True)
    for n, i in enumerate(items[:limit], 1):
        t.add_row(str(n), Text(i.name, "bold" if i.group == AI else ""), Text(i.source, GROUP_STYLE[i.group]),
                  human(i.size), Text(ago(i.last_used), "yellow" if stale(i) else "dim"))
    if len(items) > limit:
        t.add_row("", Text(f"+ {len(items) - limit} smaller ones (--all to list them)", "dim"), "", "", "")
    parts += [t, Text("")]
    parts.append(Text.assemble(("Free space: ", "dim"), ("modelhog clean 1 3 7", "bold"), ("  or  ", "dim"),
                               (f"modelhog clean --older-than {STALE_DAYS}d", "bold"),
                               ("  (asks before deleting anything)", "dim")))
    console.print(Panel(Group(*parts), title="[bold]What's eating your disk?[/]", border_style="#ff6ec7",
                        padding=(1, 2)))
    biggest_ai = by[AI][0] if by[AI] else None
    share = f"My laptop had {human(t_all)} of AI models and dev caches"
    if old:
        share += f", {human(total(old))} of it untouched for {STALE_DAYS} days"
    console.print(Text("  Share: ", "dim") + Text(share + ". Yours? uvx modelhog", "italic"))
    console.print()


def parse_age(s: str) -> float:
    m = re.fullmatch(r"(\d+)\s*([dwmy])", s)
    if not m:
        raise argparse.ArgumentTypeError("use e.g. 30d, 12w, 6m, 1y")
    return int(m.group(1)) * {"d": 1, "w": 7, "m": 30, "y": 365}[m.group(2)] * 86400


def remove(i: Item, console: Console) -> bool:
    if i.how_to_remove:
        cmd = shlex.split(i.how_to_remove)
        if not shutil.which(cmd[0]):
            console.print(f"  [yellow]skipped[/] {i.name}: run [bold]{i.how_to_remove}[/] (needs {cmd[0]})")
            return False
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            console.print(f"  [red]failed[/] {i.name}: {r.stderr.strip()[:200]}")
            return False
        return True
    try:
        if i.path.is_dir() and not i.path.is_symlink():
            shutil.rmtree(i.path)
        else:
            i.path.unlink()
        return True
    except OSError as e:
        console.print(f"  [red]failed[/] {i.name}: {e}")
        return False


def clean(items: list[Item], picks: list[int], older: float | None, group: str | None, yes: bool, dry: bool,
          console: Console, ask=input) -> int:
    chosen: list[Item] = []
    for n in picks:
        if not 1 <= n <= len(items):
            console.print(f"[red]No item #{n}[/] (there are {len(items)}).")
            return 2
        chosen.append(items[n - 1])
    if older is not None:
        chosen += [i for i in items if i.last_used and time.time() - i.last_used > older and i not in chosen
                   and (not group or i.group == group)]
    if not chosen:
        console.print("Nothing matches. Run [bold]modelhog[/] to see the list and pick numbers.")
        return 1
    console.print(Text.assemble("About to remove ", (f"{len(chosen)} item{'s' * (len(chosen) != 1)}", "bold"),
                                ", freeing ", (human(total(chosen)), "bold #ff6ec7"), ":"))
    for i in chosen:
        how = f"  (runs: {i.how_to_remove})" if i.how_to_remove else ""
        console.print(f"  [dim]·[/] {i.name} [dim]{i.source} · {human(i.size)} · {ago(i.last_used)}{how}[/]")
    if dry:
        console.print("[dim]Dry run: nothing deleted.[/]")
        return 0
    if not yes:
        try:
            if ask("Delete these? [y/N] ").strip().lower() not in ("y", "yes"):
                console.print("Nothing deleted.")
                return 1
        except EOFError:
            return 1
    done = [i for i in chosen if remove(i, console)]
    console.print(Text.assemble(("Freed ", "bold"), (human(total(done)), "bold #ff6ec7"),
                                f" ({len(done)} of {len(chosen)} removed)."))
    return 0 if len(done) == len(chosen) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="modelhog", description="Find the AI models (Hugging Face, Ollama, LM Studio…) "
                                 "and developer caches eating your disk, and delete them safely.")
    ap.add_argument("command", nargs="?", choices=["list", "clean"], default="list")
    ap.add_argument("numbers", nargs="*", type=int, help="with clean: item numbers from the list")
    ap.add_argument("--projects", nargs="*", type=Path, metavar="DIR",
                    help="also find node_modules, .venv, target… under these folders (default ~/code if given alone)")
    ap.add_argument("--older-than", type=parse_age, metavar="AGE", help="with clean: everything unused for AGE (90d, 6m…)")
    ap.add_argument("--only", choices=["ai", "caches", "projects"], help="limit to one group")
    ap.add_argument("--all", action="store_true", help="list everything, not just the biggest 40")
    ap.add_argument("-y", "--yes", action="store_true", help="don't ask before deleting")
    ap.add_argument("--dry-run", action="store_true", help="show what clean would delete")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("-V", "--version", action="version", version=f"modelhog {__version__}")
    a = ap.parse_args(argv)
    console = Console(stderr=a.json, highlight=False)
    projects = None
    if a.projects is not None:
        projects = a.projects or [Path("~/code")]
    with console.status("Measuring caches…") if not a.json else _null():
        items = scan(projects)
    group = {"ai": AI, "caches": CACHE, "projects": PROJECT}.get(a.only or "")
    if group:
        items = [i for i in items if i.group == group]
    if a.json:
        print(json.dumps([{"n": n, "group": i.group, "source": i.source, "name": i.name, "path": str(i.path),
                           "bytes": i.size, "last_used": i.last_used, "remove_with": i.how_to_remove or None}
                          for n, i in enumerate(items, 1)], indent=2))
        return 0
    if a.command == "clean":
        return clean(items, a.numbers, a.older_than, group, a.yes, a.dry_run, console)
    render(items, console, limit=10**6 if a.all else 40)
    return 0


class _null:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


if __name__ == "__main__":
    sys.exit(main())
