#!/usr/bin/env python3
"""The one CLI over the Active App's Adapter.

Usage:
  app.py apps                  # shipped Adapters and their Capabilities
  app.py use <name>            # make <name> the Active App (writes profile.json)
  app.py ping                  # validate the key; show account and units
  app.py pull                  # replace the local Workout Log and bodyweight cache
  app.py bodyweight            # print the app's weigh-ins, one per day
  app.py exercises <pattern>   # canonical names matching the pattern
  app.py compile <plan> [--week N]          # the app's payload for one week (local)
  app.py push <plan> --name NAME [--week N] [--yes]   # create the plan in the app
  app.py update <plan> --id ID [--week N] [--yes] [--force]   # rewrite an existing plan
  app.py get <id|current>      # the app's current plan
  app.py check <plan>          # local stats beside the app's server-side check, if it has one
Writes (push, update) ask first; --yes skips the prompt.
An app that runs Progression Rules itself (NATIVE_PROGRESSION) is fetched first by
`update`: if its current week differs from the Canonical Plan (the app progressed
weights the plan has not recorded), update refuses and shows both. Run
`plan.py progress` to record the progression, or pass --force to overwrite anyway.

An operation the Active App does not declare prints its Fallback and exits
non-zero. A store that has never chosen an app is told to run `app.py use <name>`.
`check` exits 1 when the app rejects the plan and 2 when its set counts differ from
the local ones; it never picks a winner.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from adapters import contract  # noqa: E402

def active():
    """The Active App's Adapter; a store that has not chosen one is told to run `use`."""
    return contract.active_adapter()


def cmd_apps(_):
    chosen = contract.active_app()
    for adapter in contract.all_adapters():
        mark = "*" if adapter.NAME == chosen else " "
        print(f"{mark} {adapter.NAME:<12} {', '.join(contract.capabilities(adapter))}")
        missing = [op for op in contract.OPERATIONS if op not in adapter.CAPABILITIES]
        for op in missing:
            print(f"    {op}: not supported -> {contract.FALLBACKS[op]}")
    if chosen is None:
        print("\nNo Active App chosen yet: run `app.py use <name>`.")


def cmd_use(a):
    adapter = contract.load(a.name)
    previous = contract.active_app()
    contract.set_active_app(adapter.NAME)
    print(f"Active App: {adapter.NAME}")
    if previous and previous != adapter.NAME:
        print(f"Switched from {previous}. The data/ cache is replaced on the next pull; "
              "checkins.jsonl, weight-log.csv and plans/ are untouched.")


def cmd_ping(_):
    adapter = active()
    info = contract.require(adapter, "ping")()
    print(json.dumps(info, indent=2))
    if info.get("units") not in (None, "kg"):
        print(f"note: the account reports units={info['units']}; "
              "kg-trainer works in kilograms and converts on read.", file=sys.stderr)


def cmd_pull(_):
    import analyze  # lazy: it resolves the store at import
    analyze.cmd_pull(argparse.Namespace(full=True))


def cmd_bodyweight(_):
    adapter = active()
    for day, kg in contract.require(adapter, "pull_bodyweight")():
        print(f"{day.isoformat()}\t{kg}")


def cmd_exercises(a):
    adapter = active()
    hits = contract.require(adapter, "find_exercise")(a.pattern)
    if not hits:
        sys.exit(f"No exercise matches {a.pattern!r} in {adapter.NAME}.")
    for hit in hits:
        print(hit["canonical"] + (f"\t{hit['app_id']}" if hit.get("app_id") else ""))


def load_plan(path):
    """A validated Canonical Plan dict (plan.py's loader and validator)."""
    import plan  # lazy: it resolves the store at import
    return plan._checked(path)[1]


def confirm(question, yes):
    """Every write asks first (some apps have no DELETE). `--yes` skips, for tests."""
    if yes:
        return
    if not sys.stdin.isatty():
        sys.exit("Refusing to write without confirmation: run interactively, or pass --yes.")
    if input(f"{question} [y/N] ").strip().lower() not in ("y", "yes"):
        sys.exit("Cancelled. Nothing was written.")


def _compiled(a):
    adapter = active()
    compile_ = contract.require(adapter, "compile")
    return adapter, compile_(load_plan(a.plan), a.week)


def cmd_compile(a):
    _, compiled = _compiled(a)
    print(compiled if isinstance(compiled, str) else json.dumps(compiled, indent=2))


def cmd_push(a):
    adapter = active()
    contract.require(adapter, "push_plan")
    _, compiled = _compiled(a)
    confirm(f"Create '{a.name}' in {adapter.NAME} from {a.plan}? "
            f"{adapter.NAME} cannot delete it afterwards.", a.yes)
    plan_id = adapter.push_plan(compiled, a.name)
    if not plan_id:
        sys.exit("The push returned no plan id; treat it as NOT done.")
    contract.set_program_id(adapter.NAME, plan_id)
    print(f"Pushed to {adapter.NAME}. Plan id: {plan_id} (recorded as app.program_id)")


def drift_guard(adapter, canonical, plan_id):
    """Rows where a native-progression app's current week differs from the Canonical
    Plan (empty when the app has no native progression, or nothing differs)."""
    if not contract.native_progression(adapter):
        return []
    payload = contract.require(adapter, "get_plan")(plan_id)
    week = canonical["current_week"]
    app_sets = adapter.prescriptions(payload, week)
    return contract.drift(app_sets, contract.plan_prescriptions(canonical, week))


def print_drift(adapter, canonical, rows):
    week = canonical["current_week"]
    print(f"{adapter.NAME} week {week} differs from the Canonical Plan in {len(rows)} place(s):")
    head = ("day", "exercise", adapter.NAME, "Canonical Plan")
    widths = [max(len(r[i]) for r in [head, *rows]) for i in range(4)]
    for row in [head, *rows]:
        print("  " + "  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip())


def cmd_update(a):
    adapter = active()
    contract.require(adapter, "update_plan")
    _, compiled = _compiled(a)
    canonical = load_plan(a.plan)
    rows = drift_guard(adapter, canonical, a.id)
    if rows:
        print_drift(adapter, canonical, rows)
        if not getattr(a, "force", False):
            sys.exit(f"Refusing to overwrite: {adapter.NAME} runs Progression Rules itself and "
                     "its current week no longer matches the Canonical Plan, so this update "
                     "would undo what the app progressed. Run `plan.py progress` to record the "
                     "app's progression, or pass --force to overwrite the app anyway. "
                     "Nothing was written.")
        print("--force: overwriting anyway.")
    confirm(f"Overwrite plan {a.id} in {adapter.NAME} with {a.plan}? "
            f"{adapter.NAME} cannot undo it.", a.yes)
    plan_id = adapter.update_plan(compiled, a.id)
    if not plan_id:
        sys.exit("The update returned no plan id; treat it as NOT done.")
    print(f"Updated {adapter.NAME} plan {plan_id}")


def cmd_get(a):
    adapter = active()
    print(json.dumps(contract.require(adapter, "get_plan")(a.id), indent=2))


def _same(x, y) -> bool:
    return abs(x - y) < 0.01


def compare_sets(local: dict, remote: dict, groups: dict) -> list[tuple]:
    """Rows (muscle, local sets, app sets, differs) over every muscle either side
    names. An app group (e.g. back) is compared with the sum of the local muscles it
    spans, and those muscles are then left out of the one-to-one rows."""
    rows, spanned = [], set()
    for group, members in groups.items():
        if group in remote:
            spanned |= set(members)
            rows.append((f"{group} ({'+'.join(members)})",
                         sum(local.get(m, 0) for m in members), remote[group]))
    for muscle in sorted(set(local) | set(remote),
                         key=lambda m: (-(local.get(m) or remote.get(m, 0)), m)):
        if muscle in groups and muscle in remote:
            continue
        if muscle in spanned and muscle not in remote:
            continue
        rows.append((muscle, local.get(muscle, 0), remote.get(muscle, 0)))
    return [(m, lo, rem, not _same(lo, rem)) for m, lo, rem in rows]


def cmd_check(a):
    """Local `plan.py stats` always; the app's own check beside it when it has one."""
    import plan as planmod  # lazy: it resolves the store at import
    adapter = active()
    check = contract.require(adapter, "check")
    canonical = load_plan(a.plan)
    week = canonical["current_week"]
    local = planmod.weekly_sets(canonical, planmod.load_muscles())[week]
    result = check(contract.require(adapter, "compile")(canonical))
    remote = result.get("weekly_sets") or {}
    print(f"Weekly working sets, week {week}: local plan.py stats vs {adapter.NAME} check")
    differs = 0
    if remote:
        rows = compare_sets(local, remote, getattr(adapter, "MUSCLE_GROUPS", {}))
        print(f"  {'muscle':<34}{'local':>8}{adapter.NAME:>12}")
        for muscle, lo, rem, bad in rows:
            differs += bad
            print(f"  {muscle:<34}{lo:>8g}{rem:>12g}" + ("   <-- DIFFERS" if bad else ""))
        print(f"{differs} muscle(s) differ. Neither side is assumed right: report both to the "
              "athlete and decide which count to trust." if differs else "Counts agree.")
    else:
        print("  (the app returned no set counts to compare)")
        for muscle, n in local.items():
            print(f"  {muscle:<34}{n:>8g}")
    for error in result.get("errors", []):
        print(f"ERROR: {error}", file=sys.stderr)
    print(f"{adapter.NAME} check: {'ok' if result.get('ok') else 'FAILED'}")
    if not result.get("ok"):
        sys.exit(1)
    if differs:
        sys.exit(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("apps").set_defaults(fn=cmd_apps)
    c = sub.add_parser("use"); c.add_argument("name"); c.set_defaults(fn=cmd_use)
    sub.add_parser("ping").set_defaults(fn=cmd_ping)
    sub.add_parser("pull").set_defaults(fn=cmd_pull)
    sub.add_parser("bodyweight").set_defaults(fn=cmd_bodyweight)
    c = sub.add_parser("exercises"); c.add_argument("pattern", nargs="?", default="")
    c.set_defaults(fn=cmd_exercises)
    c = sub.add_parser("compile"); c.add_argument("plan"); c.add_argument("--week", type=int)
    c.set_defaults(fn=cmd_compile)
    c = sub.add_parser("push"); c.add_argument("plan"); c.add_argument("--name", required=True)
    c.add_argument("--week", type=int); c.add_argument("--yes", action="store_true")
    c.set_defaults(fn=cmd_push)
    c = sub.add_parser("update"); c.add_argument("plan"); c.add_argument("--id", required=True)
    c.add_argument("--week", type=int); c.add_argument("--yes", action="store_true")
    c.add_argument("--force", action="store_true",
                   help="write even when a native-progression app has drifted from the plan")
    c.set_defaults(fn=cmd_update)
    c = sub.add_parser("get"); c.add_argument("id"); c.set_defaults(fn=cmd_get)
    c = sub.add_parser("check"); c.add_argument("plan"); c.set_defaults(fn=cmd_check)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
