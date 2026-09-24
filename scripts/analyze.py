#!/usr/bin/env python3
"""Baseline analysis of a Hevy training history.

Caches the full log locally, then computes the numbers a coach needs. Every
figure here comes from logged sets — nothing is estimated or remembered.

State (store root resolved by paths.py):
  kg-trainer-data/data/workouts.jsonl      every logged workout
  kg-trainer-data/data/measurements.json   body measurements
  kg-trainer-data/data/sync.json           {"last_pull": ISO} for incremental updates
  kg-trainer-data/exercise-templates.jsonl the catalog (hevy.py catalog)

Usage:
  analyze.py pull [--full]        # incremental by default; --full re-reads everything
  analyze.py report [--json]      # frequency, bests, trend, balance, RPE
  analyze.py deep-dive <pattern>  # one lift, every logged set, via exercise_history
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import hevy  # noqa: E402  — reuse the one API client
import paths  # noqa: E402

STATE = paths.state_dir()
DATA = STATE / "data"
WORKOUTS = DATA / "workouts.jsonl"
MEASUREMENTS = DATA / "measurements.json"
SYNC = DATA / "sync.json"

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


def load_catalog() -> dict[str, dict]:
    p = STATE / "exercise-templates.jsonl"
    if not p.exists():
        sys.exit("No exercise catalog. Run: hevy.py catalog")
    return {json.loads(l)["id"]: json.loads(l) for l in p.open() if l.strip()}


def save_workouts(ws: list[dict]):
    DATA.mkdir(parents=True, exist_ok=True)
    with WORKOUTS.open("w") as f:
        for w in sorted(ws, key=lambda w: w["start_time"]):
            f.write(json.dumps(w) + "\n")


def cmd_pull(a):
    DATA.mkdir(parents=True, exist_ok=True)
    existing = {w["id"]: w for w in load_workouts()}
    last = None
    if SYNC.exists() and not a.full:
        last = json.loads(SYNC.read_text()).get("last_pull")

    if last and existing:
        # Incremental: /workouts/events reports both edits and deletions.
        page, updated, deleted = 1, 0, 0
        while True:
            body = hevy.ok(*hevy.call("GET", "/v1/workouts/events",
                                      {"since": last, "page": page, "pageSize": 10}),
                           "workout events")
            for ev in body.get("events", []):
                if ev.get("type") == "deleted":
                    if existing.pop(ev["id"], None):
                        deleted += 1
                else:
                    existing[ev["workout"]["id"]] = ev["workout"]
                    updated += 1
            if page >= body.get("page_count", 1):
                break
            page += 1
        print(f"incremental: {updated} added/updated, {deleted} deleted since {last}")
    else:
        rows = hevy.paginate("/v1/workouts", "workouts", 10, None)
        existing = {w["id"]: w for w in rows}
        print(f"full pull: {len(rows)} workouts")

    save_workouts(list(existing.values()))
    # Measurements come back newest-first and pageSize caps at 10, so a full
    # re-read costs a page per 10 weigh-ins. Read everything once, then only
    # the recent pages — merging on date, which is the natural key.
    prior = {m["date"]: m for m in (json.loads(MEASUREMENTS.read_text())
                                    if MEASUREMENTS.exists() else [])}
    pages = None if (a.full or not prior) else 3
    for m in hevy.paginate("/v1/body_measurements", "body_measurements", 10, pages):
        prior[m["date"]] = m
    meas = sorted(prior.values(), key=lambda m: m["date"])
    MEASUREMENTS.write_text(json.dumps(meas, indent=2))
    SYNC.write_text(json.dumps({"last_pull": now_iso()}))
    print(f"{len(existing)} workouts, {len(meas)} measurements -> {DATA}")


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


def build_report(ws: list[dict], cat: dict) -> dict:
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
        counts[e["exercise_template_id"]] = counts.get(e["exercise_template_id"], 0) + 1
    top = sorted(counts, key=counts.get, reverse=True)[:6]

    bests, trend = [], []
    for tid in top:
        title = cat.get(tid, {}).get("title") or next(
            (e["title"] for _, e, _ in working_sets(ws) if e["exercise_template_id"] == tid), tid)
        sets = [(w, s) for w, e, s in working_sets(ws) if e["exercise_template_id"] == tid]
        scored = [(e1, w, s) for w, s in sets
                  if (e1 := epley(s.get("weight_kg"), s.get("reps")))]
        if not scored:
            bests.append({"exercise": title, "sets_logged": counts[tid],
                          "note": "no weight x reps <=10 sets — bodyweight or timed work"})
            continue
        best = max(scored, key=lambda x: x[0])
        heaviest = max((s for _, s in sets if s.get("weight_kg")),
                       key=lambda s: s["weight_kg"], default=None)
        bests.append({
            "exercise": title, "sets_logged": counts[tid],
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
    vague: dict[str, int] = {}  # what lands in Hevy's catch-all groups
    for _, e, _ in working_sets(ws, since=w8):
        mg = cat.get(e["exercise_template_id"], {}).get("primary_muscle_group") or "unknown"
        by_muscle[mg] = by_muscle.get(mg, 0) + 1
        if mg in ("other", "unknown", "full_body"):
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
        # Hevy files a lot of machine work under "other", which makes the
        # balance table lie by omission. Name the exercises so the coach can
        # reassign them by hand instead of trusting a catch-all bucket.
        "unclassified_exercises": dict(sorted(vague.items(), key=lambda x: -x[1])),
    }

    # --- RPE, if logged at all. Top set = heaviest working set of that exercise that day.
    rpes = []
    for w in ws:
        if parse_ts(w["start_time"]) < w8:
            continue
        for e in w.get("exercises", []):
            cand = [s for s in e.get("sets", [])
                    if s.get("type") != "warmup" and s.get("rpe") is not None]
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
        L += [f"{tot} of those sets are filed under a catch-all group by Hevy, so the table "
              f"above undercounts whatever they really train:", ""]
        L += [f"- {k} — {v} sets" for k, v in list(b["unclassified_exercises"].items())[:12]]
        L += [""]
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
    ws, cat = load_workouts(), load_catalog()
    rep = build_report(ws, cat)
    print(json.dumps(rep, indent=2) if a.json else fmt(rep))


def cmd_deep_dive(a):
    cat = load_catalog()
    hits = [t for t in cat.values() if a.pattern.lower() in t["title"].lower()]
    if not hits:
        sys.exit(f"No catalog exercise matches {a.pattern!r}")
    t = hits[0]
    body = hevy.ok(*hevy.call("GET", f"/v1/exercise_history/{t['id']}"), "exercise history")
    rows = body.get("exercise_history", [])
    work = [r for r in rows if r.get("set_type") != "warmup"]
    print(f"{t['title']} ({t['id']}): {len(rows)} logged sets, {len(work)} working")
    scored = [(epley(r.get("weight_kg"), r.get("reps")), r) for r in work]
    scored = [x for x in scored if x[0]]
    for e, r in sorted(scored, key=lambda x: -x[0])[:10]:
        print(f"  {r['workout_start_time'][:10]}  {r['weight_kg']}kg x {r['reps']}"
              f"  rpe {r.get('rpe')}  -> e1RM {e:.1f}")


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
