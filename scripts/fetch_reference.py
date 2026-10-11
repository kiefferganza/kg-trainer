#!/usr/bin/env python3
"""Vendor Liftosaur's reference documents into references/.

The Liftosaur MCP server exposes reference tools that need no authentication,
so this runs without an API key and without a subscription. Re-run it when
Liftosaur updates its exercise list or its syntax docs.

Usage:
  fetch_reference.py                 # write every reference file
  fetch_reference.py --check         # fail if a vendored file is out of date
  fetch_reference.py --fedb PATH     # seed muscles from a local free-exercise-db JSON

Without --fedb the free-exercise-db is downloaded and cached at FEDB_CACHE.
Muscle assignments already present in references/exercise-muscles.json are
kept, so hand-mapped entries survive a re-run; only new exercises are seeded,
and any left 'unknown' are listed for hand-mapping.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.request

MCP = "https://www.liftosaur.com/mcp"
FEDB_URL = "https://cdn.jsdelivr.net/gh/yuhonas/free-exercise-db@main/dist/exercises.json"
REFS = pathlib.Path(__file__).resolve().parent.parent / "references"
FEDB_CACHE = (pathlib.Path(__file__).resolve().parent.parent / ".superpowers" / "sdd"
              / "2026-10-11-liftosaur-migration" / "free-exercise-db.json")

TOOLS = {
    "exercises.txt": "list_exercises",
    "liftohistory.md": "get_liftohistory_reference",
    "liftoscript.md": "get_liftoscript_reference",
}

# 'unknown' is deliberately absent: it is only the runtime fallback of muscle_for(),
# never a stored value in exercise-muscles.json.
MUSCLES = ("chest shoulders triceps biceps lats upper_back traps lower_back "
           "abdominals quadriceps hamstrings glutes calves adductors forearms").split()

ALIASES = {
    "quadriceps": "quadriceps", "hamstrings": "hamstrings", "glutes": "glutes",
    "calves": "calves", "chest": "chest", "shoulders": "shoulders",
    "triceps": "triceps", "biceps": "biceps", "lats": "lats", "traps": "traps",
    "middle back": "upper_back", "lower back": "lower_back",
    "abdominals": "abdominals", "adductors": "adductors", "abductors": "glutes",
    "forearms": "forearms", "neck": "traps",
}


def mcp_tool(name: str) -> str:
    """Call one no-auth MCP reference tool and return its text content."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": name, "arguments": {}}}).encode()
    req = urllib.request.Request(MCP, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(req, timeout=90) as response:
        payload = json.loads(response.read())
    if "error" in payload:
        sys.exit(f"{name} failed: {payload['error']}")
    return payload["result"]["content"][0]["text"]


def normalise(title: str) -> str:
    return " ".join(title.lower().replace(",", " ").split())


def muscle_for(title: str, table: dict) -> str:
    """Primary muscle group for an exercise title, or 'unknown'."""
    if title in table:
        return table[title]
    key = normalise(title)
    for candidate, muscle in table.items():
        if normalise(candidate) == key:
            return muscle
    # 'Bench Press, Dumbbell' falls back to 'Bench Press'
    base = title.split(",")[0].strip()
    if base != title and base in table:
        return table[base]
    return "unknown"


def exercise_names(text: str) -> list[str]:
    """Exercise titles from list_exercises output, dropping the intro line."""
    return [n.strip() for n in text.splitlines() if n.strip() and not n.rstrip().endswith(":")]


def load_fedb(path: pathlib.Path | None) -> list:
    if path is None:
        path = FEDB_CACHE
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(FEDB_URL, timeout=90) as response:
                path.write_bytes(response.read())
    return json.loads(path.read_text())


def build_muscle_map(names: list[str], fedb: list, existing: dict | None = None) -> dict:
    """Seed the map from free-exercise-db primaryMuscles; keep prior valid entries."""
    existing = existing or {}
    by_name = {}
    for entry in fedb:
        primary = (entry.get("primaryMuscles") or [None])[0]
        if primary and ALIASES.get(primary):
            by_name[normalise(entry.get("name", ""))] = ALIASES[primary]
    out = {}
    for name in names:
        if existing.get(name) in MUSCLES:
            out[name] = existing[name]
            continue
        key = normalise(name)
        base = normalise(name.split(",")[0])
        out[name] = by_name.get(key) or by_name.get(base) or "unknown"
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--fedb", type=pathlib.Path, help="local free-exercise-db JSON")
    args = parser.parse_args()

    REFS.mkdir(parents=True, exist_ok=True)
    stale = []
    for filename, tool in TOOLS.items():
        text = mcp_tool(tool)
        target = REFS / filename
        if args.check:
            if not target.exists() or target.read_text() != text:
                stale.append(filename)
            continue
        target.write_text(text)
        print(f"wrote {target.relative_to(REFS.parent)}  ({len(text)} chars)")

    if args.check:
        if stale:
            sys.exit("stale vendored references: " + ", ".join(stale))
        print("vendored references are current")
        return

    names = exercise_names((REFS / "exercises.txt").read_text())
    muscles_path = REFS / "exercise-muscles.json"
    existing = json.loads(muscles_path.read_text()) if muscles_path.exists() else {}
    table = build_muscle_map(names, load_fedb(args.fedb), existing)
    unknown = sorted(k for k, v in table.items() if v == "unknown")
    muscles_path.write_text(json.dumps(table, indent=2, sort_keys=True) + "\n")
    print(f"wrote references/exercise-muscles.json  ({len(table)} exercises, "
          f"{len(unknown)} unknown)")
    if unknown:
        print("unmapped -- assign these by hand before trusting the balance table:")
        for name in unknown:
            print(f"  {name}")


if __name__ == "__main__":
    main()
