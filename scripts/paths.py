#!/usr/bin/env python3
"""Where kg-trainer keeps its state.

Resolution order:
  1. $KG_TRAINER_HOME, if set (absolute or ~-relative).
  2. Walking up from the current directory: the first existing kg-trainer-data/,
     or kg-trainer-data/ at the first git root — whichever is reached first.
  3. kg-trainer-data/ in the current directory.

Step 2 means a script run from a project subdirectory reuses the project's store
instead of quietly starting an empty one. The store is never allowed inside the
skill itself unless $KG_TRAINER_HOME asks for it explicitly: personal data does
not belong in the skill's source tree.

Usage:
  paths.py      # print the resolved store and why it was chosen
"""

from __future__ import annotations

import functools
import os
import pathlib
import subprocess
import sys

DIRNAME = "kg-trainer-data"
SKILL_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _locate() -> tuple[pathlib.Path, str]:
    env = os.environ.get("KG_TRAINER_HOME")
    if env:
        return pathlib.Path(env).expanduser().resolve(), "$KG_TRAINER_HOME"
    cwd = pathlib.Path.cwd().resolve()
    for d in (cwd, *cwd.parents):
        if (d / DIRNAME).is_dir():
            return d / DIRNAME, "existing store"
        if (d / ".git").exists():  # a file, not a dir, inside a worktree
            return d / DIRNAME, "git root"
    return cwd / DIRNAME, "current directory"


def _warn_if_unignored(store: pathlib.Path) -> None:
    """A store inside a git repo must be ignored — it can hold the hevy-key."""
    try:
        inside = subprocess.run(["git", "-C", str(store.parent), "rev-parse", "--is-inside-work-tree"],
                                capture_output=True, text=True)
        if inside.stdout.strip() != "true":
            return
        ignored = subprocess.run(["git", "-C", str(store.parent), "check-ignore", "-q", f"{DIRNAME}/"])
    except FileNotFoundError:  # no git on PATH
        return
    if ignored.returncode == 1:
        print(f"warning: {store} is not gitignored. Add '{DIRNAME}/' to the repo's .gitignore "
              "before saving anything personal in it.", file=sys.stderr)


@functools.cache
def state_dir() -> pathlib.Path:
    store, why = _locate()
    if why != "$KG_TRAINER_HOME" and store.is_relative_to(SKILL_ROOT):
        sys.exit(f"Refusing to keep personal data inside the skill ({SKILL_ROOT}). "
                 "Run from your project directory, or set KG_TRAINER_HOME.")
    _warn_if_unignored(store)
    return store


if __name__ == "__main__":
    store = state_dir()
    print(f"{store}  ({_locate()[1]}{', exists' if store.is_dir() else ', not created yet'})")
