#!/usr/bin/env python3
"""Baseline analysis of a Liftosaur training history.

Caches the full log locally, then computes the numbers a coach needs. Every
figure here comes from logged sets — nothing is estimated or remembered.

State (store root resolved by paths.py):
  kg-trainer-data/data/workouts.jsonl      every logged workout
  kg-trainer-data/data/measurements.json   bodyweight, [{"date", "weight_kg"}]
  kg-trainer-data/data/sync.json           {"last_pull": ISO}, when the cache was last refreshed
  references/exercise-muscles.json         exercise -> primary muscle (fetch_reference.py)

Usage:
  analyze.py pull                 # re-read the whole history (--full is accepted, same thing)
  analyze.py report [--json]      # frequency, bests, trend, balance, RPE
  analyze.py deep-dive <pattern>  # one lift, every logged set, from the local log
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import liftosaur  # noqa: E402  — reuse the one API client
import paths  # noqa: E402
from fetch_reference import MUSCLES, muscle_for  # noqa: E402
from liftohistory import parse_liftohistory  # noqa: E402

STATE = paths.state_dir()
DATA = STATE / "data"
WORKOUTS = DATA / "workouts.jsonl"
MEASUREMENTS = DATA / "measurements.json"
SYNC = DATA / "sync.json"
MUSCLE_MAP = pathlib.Path(__file__).resolve().parent.parent / "references" / "exercise-muscles.json"
# The user's own custom exercises. It lives in the store, not the skill, because
# the names are personal and fetch_reference.py rewrites the shipped map.
PERSONAL_MUSCLE_MAP = STATE / "exercise-muscles.json"

# Muscle groups that a push/pull ratio is computed over. Legs, core and
# everything else are deliberately excluded — they belong to neither side.
PUSH = {"chest", "shoulders", "triceps"}
PULL = {"lats", "upper_back", "traps", "biceps"}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_workouts() -> list[dict]:
    if not WORKOUTS.exists():
        return []
    ws = [json.loads(l) for l in WORKOUTS.open() if l.strip()]
    return sorted(ws, key=lambda w: w["start_time"])


def load_muscles() -> dict[str, str]:
    """The shipped built-in map, with the personal map's entries layered on top."""
    if not MUSCLE_MAP.exists():
        sys.exit(f"No muscle map at {MUSCLE_MAP}. Run: scripts/fetch_reference.py")
    table = json.loads(MUSCLE_MAP.read_text())
    if PERSONAL_MUSCLE_MAP.exists():
        personal = json.loads(PERSONAL_MUSCLE_MAP.read_text())
        bad = (sorted(f"{k}: {v}" for k, v in personal.items() if v not in MUSCLES)
               if isinstance(personal, dict) else ["not a JSON object"])
        if bad:
            sys.exit(f"{PERSONAL_MUSCLE_MAP} has entries outside the muscle vocabulary "
                     f"({', '.join(bad)}). Use one of: {', '.join(MUSCLES)}")
        table.update(personal)
    return table


def save_workouts(ws: list[dict]):
    DATA.mkdir(parents=True, exist_ok=True)
    with WORKOUTS.open("w") as f:
        for w in sorted(ws, key=lambda w: w["start_time"]):
            f.write(json.dumps(w) + "\n")


def pull_bodyweight() -> int:
    """Rewrite MEASUREMENTS from the full weight series: one row per day, latest wins."""
    by_day: dict[str, tuple] = {}
    for v in liftosaur.paginate("/measurements/weight", "values"):
        read = liftosaur.weigh_in(v)
        if read is None:
            continue
        day, kg = read[0].isoformat(), read[1]
        ts = v.get("timestamp") or 0
        if day not in by_day or ts > by_day[day][0]:
            by_day[day] = (ts, kg)
    rows = [{"date": d, "weight_kg": kg} for d, (_, kg) in sorted(by_day.items())]
    MEASUREMENTS.write_text(json.dumps(rows, indent=2))
    return len(rows)


def cmd_pull(a):
    """Re-read the whole history and replace the cache with it.

    Liftosaur pages 200 records at a time, so a full read is a few requests.
    Replacing rather than merging is what keeps the cache honest: a session
    logged late with an earlier date, a session whose time was edited, a
    deleted session, and rows left by an older backend all come out right.
    `--full` is accepted for compatibility; every pull is full.
    """
    records = liftosaur.paginate("/history", "records")

    parsed: list[dict] = []
    for record in records:
        for workout in parse_liftohistory(record.get("text") or ""):
            workout["id"] = record.get("id")
            parsed.append(workout)

    previous = len(load_workouts())
    save_workouts(parsed)

    DATA.mkdir(parents=True, exist_ok=True)
    weigh_ins = pull_bodyweight()
    SYNC.write_text(json.dumps({"last_pull": now_iso()}))
    print(f"{len(parsed)} workouts parsed from {len(records)} records "
          f"(cache held {previous}); {weigh_ins} weigh-in days -> {DATA}")


def epley(weight, reps):
    """Estimated 1RM. Only meaningful at low reps, so callers cap at 10."""
    if not weight or not reps or reps > 10:
        return None
    return weight * (1 + reps / 30)


def working_sets(ws: list[dict], since: dt.datetime | None = None):
    """Yield (workout, exercise, set) for every non-warmup set."""
    for w in ws:
        if since and parse_ts(w["start_time"]) < since:
            continue
        for e in w.get("exercises", []):
            for s in e.get("sets", []):
                if s.get("type") == "warmup":
                    continue
                yield w, e, s


def build_report(ws: list[dict], muscles: dict) -> dict:
    if not ws:
        return {"workout_count": 0,
                "message": "No logged workouts. Ask the client for a paste, an export, "
                           "or treat this as a fresh start — and label every number's source."}
    now = parse_ts(ws[-1]["start_time"])
    w12 = now - dt.timedelta(weeks=12)
    w8 = now - dt.timedelta(weeks=8)
    w16 = now - dt.timedelta(weeks=16)

    rep = {"workout_count": len(ws),
           "log_spans": {"first": ws[0]["start_time"][:10], "last": ws[-1]["start_time"][:10]}}

    # --- Frequency over the last 12 weeks, plus the longest gap.
    recent = [w for w in ws if parse_ts(w["start_time"]) >= w12]
    dates = sorted({parse_ts(w["start_time"]).date() for w in recent})
    gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    rep["frequency"] = {
        "sessions_last_12wk": len(recent),
        "sessions_per_week": round(len(recent) / 12, 2),
        "distinct_training_days": len(dates),
        "longest_gap_days": max(gaps) if gaps else None,
        "longest_gap_between": (f"{dates[gaps.index(max(gaps))]} -> "
                                f"{dates[gaps.index(max(gaps)) + 1]}") if gaps else None,
    }

    # --- Rank lifts by working-set count, then best set + estimated 1RM.
    counts: dict[str, int] = {}
    for _, e, _ in working_sets(ws):
        counts[e["title"]] = counts.get(e["title"], 0) + 1
    top = sorted(counts, key=counts.get, reverse=True)[:6]

    bests, trend = [], []
    for title in top:
        sets = [(w, s) for w, e, s in working_sets(ws) if e["title"] == title]
        scored = [(e1, w, s) for w, s in sets
                  if (e1 := epley(s.get("weight_kg"), s.get("reps")))]
        if not scored:
            bests.append({"exercise": title, "sets_logged": counts[title],
                          "note": "no weight x reps <=10 sets — bodyweight or timed work"})
            continue
        best = max(scored, key=lambda x: x[0])
        heaviest = max((s for _, s in sets if s.get("weight_kg")),
                       key=lambda s: s["weight_kg"], default=None)
        bests.append({
            "exercise": title, "sets_logged": counts[title],
            "heaviest_set": (f"{heaviest['weight_kg']}kg x {heaviest.get('reps')}"
                             if heaviest else None),
            "best_e1rm_kg": round(best[0], 1),
            "best_e1rm_from": f"{best[2]['weight_kg']}kg x {best[2]['reps']}",
            "best_e1rm_date": best[1]["start_time"][:10],
        })
        # Trend: best e1RM in the last 8 weeks vs the 8 weeks before that.
        def window_best(lo, hi):
            v = [e for e, w, s in scored if lo <= parse_ts(w["start_time"]) < hi]
            return round(max(v), 1) if v else None
        recent_b, prior_b = window_best(w8, now + dt.timedelta(days=1)), window_best(w16, w8)
        trend.append({"exercise": title, "e1rm_now": recent_b, "e1rm_8wk_ago": prior_b,
                      "change_kg": (round(recent_b - prior_b, 1)
                                    if recent_b and prior_b else None),
                      "note": None if (recent_b and prior_b) else
                              "not enough data in one window to compare"})
    rep["best_sets"], rep["trend"] = bests, trend

    # --- Balance: working sets by primary muscle group over the last 8 weeks.
    by_muscle: dict[str, int] = {}
    vague: dict[str, int] = {}  # exercises missing from the muscle map
    for _, e, _ in working_sets(ws, since=w8):
        mg = muscle_for(e["title"], muscles)
        by_muscle[mg] = by_muscle.get(mg, 0) + 1
        if mg == "unknown":
            vague[e["title"]] = vague.get(e["title"], 0) + 1
    avg = statistics.fmean(by_muscle.values()) if by_muscle else 0
    push = sum(v for k, v in by_muscle.items() if k in PUSH)
    pull = sum(v for k, v in by_muscle.items() if k in PULL)
    rep["balance"] = {
        "window": "last 8 weeks",
        "sets_by_muscle": dict(sorted(by_muscle.items(), key=lambda x: -x[1])),
        "average_sets": round(avg, 1),
        "under_half_average": sorted(k for k, v in by_muscle.items() if v < avg / 2),
        "push_sets": push, "pull_sets": pull,
        "push_pull_ratio": round(push / pull, 2) if pull else None,
        "push_pull_flag": bool(pull and push / pull > 1.5),
        # An entry here is an exercise with no row in either muscle map, so its
        # sets are counted under "unknown" and every muscle it trains is
        # undercounted. The fix is to add it to a map, not to adjust the table.
        "unclassified_exercises": dict(sorted(vague.items(), key=lambda x: -x[1])),
    }

    # --- RPE, if logged at all. Top set = heaviest working set of that exercise that day.
    rpes = []
    for w in ws:
        if parse_ts(w["start_time"]) < w8:
            continue
        for e in w.get("exercises", []):
            # '@8+' is RPE the athlete logged; a bare '@8' is the prescription
            # carried onto the set, which says nothing about how it felt.
            cand = [s for s in e.get("sets", [])
                    if s.get("type") != "warmup" and s.get("rpe") is not None
                    and s.get("rpe_logged")]
            if cand:
                rpes.append(max(cand, key=lambda s: s.get("weight_kg") or 0)["rpe"])
    rep["rpe"] = ({"top_sets_with_rpe": len(rpes), "average_top_set_rpe": round(statistics.fmean(rpes), 2)}
                  if rpes else {"top_sets_with_rpe": 0, "note": "client does not log RPE"})

    if MEASUREMENTS.exists():
        m = [x for x in json.loads(MEASUREMENTS.read_text()) if x.get("weight_kg")]
        m.sort(key=lambda x: x["date"])
        rep["bodyweight"] = ({"n": len(m), "latest": m[-1]["date"] + f" {m[-1]['weight_kg']}kg",
                              "earliest": m[0]["date"] + f" {m[0]['weight_kg']}kg"}
                             if m else {"n": 0})
    return rep


def fmt(rep: dict) -> str:
    if rep.get("workout_count") == 0:
        return rep["message"]
    L = [f"## Baseline — {rep['workout_count']} logged workouts "
         f"({rep['log_spans']['first']} to {rep['log_spans']['last']})", ""]
    f = rep["frequency"]
    L += ["### Frequency (last 12 weeks)", "",
          "| Metric | Value |", "|---|---|",
          f"| Sessions | {f['sessions_last_12wk']} |",
          f"| Per week | {f['sessions_per_week']} |",
          f"| Longest gap | {f['longest_gap_days']} days ({f['longest_gap_between']}) |", ""]
    L += ["### Best sets (Epley, sets of <=10 reps, warmups excluded)", "",
          "| Exercise | Sets | Heaviest | Est. 1RM | From | Date |", "|---|---|---|---|---|---|"]
    for b in rep["best_sets"]:
        if b.get("note"):
            L.append(f"| {b['exercise']} | {b['sets_logged']} | — | — | {b['note']} | — |")
        else:
            L.append(f"| {b['exercise']} | {b['sets_logged']} | {b['heaviest_set']} | "
                     f"**{b['best_e1rm_kg']} kg** | {b['best_e1rm_from']} | {b['best_e1rm_date']} |")
    L += ["", "### Trend (est. 1RM, last 8 weeks vs the 8 before)", "",
          "| Exercise | Now | 8 wk ago | Change |", "|---|---|---|---|"]
    for t in rep["trend"]:
        chg = (f"{t['change_kg']:+} kg" if t.get("change_kg") is not None else t.get("note") or "—")
        L.append(f"| {t['exercise']} | {t['e1rm_now'] or '—'} | {t['e1rm_8wk_ago'] or '—'} | {chg} |")
    b = rep["balance"]
    L += ["", f"### Balance — working sets by muscle, {b['window']}", "",
          "| Muscle | Sets |", "|---|---|"]
    for k, v in b["sets_by_muscle"].items():
        flag = "  ⚠ under half average" if k in b["under_half_average"] else ""
        L.append(f"| {k} | {v}{flag} |")
    L += ["", f"Average {b['average_sets']} sets/muscle. "
              f"Push {b['push_sets']} : pull {b['pull_sets']} = "
              f"{b['push_pull_ratio']}"
              + ("  ⚠ above 1.5:1" if b["push_pull_flag"] else ""), ""]
    if b["unclassified_exercises"]:
        tot = sum(b["unclassified_exercises"].values())
        L += [f"{tot} of those sets are from exercises with no muscle mapping, so the "
              f"table above undercounts whatever they really train:", ""]
        L += [f"- {k} — {v} sets" for k, v in list(b["unclassified_exercises"].items())[:12]]
        L += ["", f"Map custom exercises in {PERSONAL_MUSCLE_MAP}, "
                  f'e.g. {{"bayesian curl": "biceps"}}.', ""]
    r = rep["rpe"]
    L += ["### RPE", "",
          r.get("note") or (f"Average top-set RPE over the last 8 weeks: "
                            f"**{r['average_top_set_rpe']}** across "
                            f"{r['top_sets_with_rpe']} top sets.")]
    if rep.get("bodyweight", {}).get("n"):
        L += ["", "### Bodyweight", "",
              f"{rep['bodyweight']['n']} weigh-ins, {rep['bodyweight']['earliest']} "
              f"-> {rep['bodyweight']['latest']}"]
    return "\n".join(L)


def cmd_report(a):
    rep = build_report(load_workouts(), load_muscles())
    print(json.dumps(rep, indent=2) if a.json else fmt(rep))


def deep_dive(ws: list[dict], pattern: str):
    """Every logged set of the first exercise whose title contains `pattern`."""
    needle = pattern.lower()
    title = next((e["title"] for w in ws for e in w.get("exercises", [])
                  if needle in e["title"].lower()), None)
    if title is None:
        sys.exit(f"No logged exercise matches {pattern!r}. Run: analyze.py pull")
    rows = [(w, s) for w in ws for e in w.get("exercises", []) if e["title"] == title
            for s in e.get("sets", [])]
    work = [(w, s) for w, s in rows if s.get("type") != "warmup"]
    print(f"{title}: {len(rows)} logged sets, {len(work)} working")
    scored = [(e1, w, s) for w, s in work if (e1 := epley(s.get("weight_kg"), s.get("reps")))]
    for e1, w, s in sorted(scored, key=lambda x: -x[0])[:10]:
        print(f"  {w['start_time'][:10]}  {s['weight_kg']}kg x {s['reps']}"
              f"  rpe {s.get('rpe')}  -> e1RM {e1:.1f}")


def cmd_deep_dive(a):
    deep_dive(load_workouts(), a.pattern)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("pull"); c.add_argument("--full", action="store_true"); c.set_defaults(fn=cmd_pull)
    c = sub.add_parser("report"); c.add_argument("--json", action="store_true"); c.set_defaults(fn=cmd_report)
    c = sub.add_parser("deep-dive"); c.add_argument("pattern"); c.set_defaults(fn=cmd_deep_dive)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
