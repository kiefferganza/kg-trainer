#!/usr/bin/env python3
"""Weight-trend math and check-in persistence for kg-trainer.

State lives in kg-trainer-data (store root resolved by paths.py):
  profile.json        the intake answers
  weight-log.csv      date,weight_kg  (one row per weigh-in)
  checkins.jsonl      one record per weekly check-in, append only

Usage:
  checkin.py log-weight <YYYY-MM-DD> <KG> [--fat PCT] [--waist CM]
  checkin.py import-liftosaur              # pull weigh-ins from Liftosaur into the log
  checkin.py trend [--window 7] [--calories N]
  checkin.py record --calories N --protein G --carbs G --fat G [--notes TEXT]
  checkin.py history [--last N]

`trend` refuses to report a rate it cannot support and says why. Read its
`verdict` field before changing anything.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import statistics
import sys

import liftosaur
import paths

HOME = paths.state_dir()
PROFILE = HOME / "profile.json"
WEIGHTS = HOME / "weight-log.csv"
CHECKINS = HOME / "checkins.jsonl"
KCAL_PER_KG = 7700  # energy density of body-mass change, the standard working figure
MIN_READINGS = 4  # below this a window average is not trustworthy


def load_weights() -> list[tuple[dt.date, float]]:
    if not WEIGHTS.exists():
        return []
    rows = []
    with WEIGHTS.open() as f:
        for r in csv.DictReader(f):
            try:
                rows.append((dt.date.fromisoformat(r["date"]), float(r["weight_kg"])))
            except (ValueError, KeyError):
                continue
    return sorted(rows)


def _append_weight(date: str, weight_kg: float, fat_percent, waist_cm):
    HOME.mkdir(parents=True, exist_ok=True)
    new = not WEIGHTS.exists()
    with WEIGHTS.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "weight_kg", "fat_percent", "waist_cm"])
        w.writerow([date, weight_kg, fat_percent if fat_percent is not None else "",
                    waist_cm if waist_cm is not None else ""])


def cmd_log_weight(a):
    existing = {d.isoformat() for d, _ in load_weights()}
    if a.date in existing:
        sys.exit(f"{a.date} already logged. Edit {WEIGHTS} directly to correct it.")
    _append_weight(a.date, a.weight, a.fat, a.waist)
    print(f"logged {a.date}: {a.weight} kg")


def cmd_import_liftosaur(a):
    """Pull weigh-ins from Liftosaur so the local log is not the only source.

    Days already in the log are left alone, so a hand-logged entry wins. When
    a day has several weigh-ins, the latest one is imported.
    """
    values = liftosaur.paginate("/measurements/weight", "values")
    latest: dict[dt.date, tuple] = {}
    unreadable = 0
    for v in values:
        read = liftosaur.weigh_in(v)
        if read is None:
            unreadable += 1
            continue
        ts = v.get("timestamp") or 0
        if read[0] not in latest or ts > latest[read[0]][0]:
            latest[read[0]] = (ts, read[1])
    existing = {d for d, _ in load_weights()}
    added = 0
    for day in sorted(latest):
        if day in existing:
            continue
        _append_weight(day.isoformat(), latest[day][1], None, None)
        added += 1
    print(f"imported {added} new weigh-ins from Liftosaur ({len(values)} values seen"
          + (f", {unreadable} unreadable and skipped" if unreadable else "") + ")")


def window_avg(rows, end: dt.date, days: int):
    start = end - dt.timedelta(days=days - 1)
    vals = [w for d, w in rows if start <= d <= end]
    return vals


def cmd_trend(a):
    rows = load_weights()
    if not rows:
        print(json.dumps({"verdict": "NO_DATA",
                          "message": f"No weigh-ins in {WEIGHTS}. Log some before adjusting anything."},
                         indent=2))
        return
    end = rows[-1][0]
    cur = window_avg(rows, end, a.window)
    prev = window_avg(rows, end - dt.timedelta(days=a.window), a.window)

    out = {
        "latest_weigh_in": {"date": end.isoformat(), "weight_kg": rows[-1][1]},
        "window_days": a.window,
        "current_window": {"n": len(cur), "avg_kg": round(statistics.fmean(cur), 2) if cur else None,
                           "sd_kg": round(statistics.stdev(cur), 2) if len(cur) > 1 else None},
        "previous_window": {"n": len(prev), "avg_kg": round(statistics.fmean(prev), 2) if prev else None,
                            "sd_kg": round(statistics.stdev(prev), 2) if len(prev) > 1 else None},
    }

    if len(cur) < MIN_READINGS or len(prev) < MIN_READINGS:
        out["verdict"] = "INSUFFICIENT_DATA"
        out["message"] = (
            f"Need >={MIN_READINGS} weigh-ins in each {a.window}-day window; have "
            f"{len(cur)} current and {len(prev)} previous. Do not change calories on this. "
            "Hold current targets, ask for daily weigh-ins, reassess next week."
        )
        print(json.dumps(out, indent=2))
        return

    rate = statistics.fmean(cur) - statistics.fmean(prev)
    out["rate_kg_per_week"] = round(rate, 3)
    out["rate_pct_bw_per_week"] = round(100 * rate / statistics.fmean(cur), 2)
    out["noise_kg"] = round(max(out["current_window"]["sd_kg"] or 0,
                                out["previous_window"]["sd_kg"] or 0), 2)
    out["signal_exceeds_noise"] = abs(rate) > (out["noise_kg"] or 0)

    if a.calories:
        # Observed maintenance beats any BMR formula once there are two clean windows.
        daily_delta = rate * KCAL_PER_KG / 7
        out["observed_tdee_kcal"] = round(a.calories - daily_delta)
        out["current_intake_kcal"] = a.calories
        out["note"] = ("observed_tdee assumes the reported intake was actually eaten and that "
                       "the whole weight change is tissue; water and glycogen shifts inflate it "
                       "over short windows.")

    out["verdict"] = "OK" if out["signal_exceeds_noise"] else "WITHIN_NOISE"
    if out["verdict"] == "WITHIN_NOISE":
        out["message"] = ("Change is smaller than day-to-day variation. Treat as flat rather than "
                          "as movement in either direction.")
    print(json.dumps(out, indent=2))


def cmd_record(a):
    HOME.mkdir(parents=True, exist_ok=True)
    rows = load_weights()
    end = rows[-1][0] if rows else dt.date.today()
    cur = window_avg(rows, end, 7)
    prev = window_avg(rows, end - dt.timedelta(days=7), 7)
    rec = {
        "date": dt.date.today().isoformat(),
        "avg_weight_kg": round(statistics.fmean(cur), 2) if cur else None,
        "prev_avg_weight_kg": round(statistics.fmean(prev), 2) if prev else None,
        "rate_kg_per_week": (round(statistics.fmean(cur) - statistics.fmean(prev), 3)
                             if cur and prev else None),
        "calories": a.calories,
        "macros_g": {"protein": a.protein, "carbs": a.carbs, "fat": a.fat},
        "sessions_completed": a.sessions,
        "sessions_planned": a.planned,
        "notes": a.notes or "",
    }
    with CHECKINS.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps(rec, indent=2))


def cmd_history(a):
    if not CHECKINS.exists():
        print(json.dumps({"checkins": [], "message": f"No check-ins recorded yet in {CHECKINS}."},
                         indent=2))
        return
    recs = [json.loads(l) for l in CHECKINS.open() if l.strip()]
    print(json.dumps(recs[-a.last:], indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("log-weight"); c.add_argument("date"); c.add_argument("weight", type=float)
    c.add_argument("--fat", type=float); c.add_argument("--waist", type=float)
    c.set_defaults(fn=cmd_log_weight)

    sub.add_parser("import-liftosaur").set_defaults(fn=cmd_import_liftosaur)

    c = sub.add_parser("trend"); c.add_argument("--window", type=int, default=7)
    c.add_argument("--calories", type=int); c.set_defaults(fn=cmd_trend)

    c = sub.add_parser("record")
    c.add_argument("--calories", type=int, required=True)
    c.add_argument("--protein", type=int, required=True)
    c.add_argument("--carbs", type=int, required=True)
    c.add_argument("--fat", type=int, required=True)
    c.add_argument("--sessions", type=int); c.add_argument("--planned", type=int)
    c.add_argument("--notes"); c.set_defaults(fn=cmd_record)

    c = sub.add_parser("history"); c.add_argument("--last", type=int, default=6)
    c.set_defaults(fn=cmd_history)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
