#!/usr/bin/env python3
"""Generate a static training dashboard from the cached Liftosaur log.

Writes kg-trainer-data/dashboard/{index.html,data.js,img/*}. It opens straight
from the filesystem — no server, and no API key ever reaches the output.

Exercise photos come from the public-domain free-exercise-db. Each exercise has
a start and a finish frame; the page crossfades them so a card reads as a rep in
motion. An exercise with no confident match gets a text tile, never a wrong photo.

Usage:
  dashboard.py build [--name NAME] [--goal TEXT] [--open] [--no-images]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import subprocess
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import analyze  # noqa: E402
import liftosaur  # noqa: E402
import paths  # noqa: E402

STATE = paths.state_dir()
OUT = STATE / "dashboard"
IMG = OUT / "img"
TEMPLATE = pathlib.Path(__file__).parent / "dashboard_template.html"

FEDB_JSON = "https://cdn.jsdelivr.net/gh/yuhonas/free-exercise-db@main/dist/exercises.json"
FEDB_IMG = "https://cdn.jsdelivr.net/gh/yuhonas/free-exercise-db@main/exercises/"
FEDB_CACHE = STATE / "data" / "free-exercise-db.json"

# Equipment and grip words carry little matching signal on their own but wreck
# token overlap, so they are down-weighted rather than dropped.
NOISE = {"machine", "cable", "barbell", "dumbbell", "smith", "plate", "loaded",
         "band", "resistance", "bodyweight", "assisted", "weighted", "sa",
         "medium", "grip", "close", "wide", "neutral", "standing", "seated",
         "lying", "alternating", "single", "arm", "one", "with", "the", "and"}


def norm(s: str) -> list[str]:
    s = re.sub(r"\(.*?\)", " ", s.lower())
    return [t for t in re.split(r"[^a-z]+", s) if t]


def score(a: str, b: str) -> float:
    ta, tb = norm(a), norm(b)
    if not ta or not tb:
        return 0.0
    ca = {t for t in ta if t not in NOISE} or set(ta)
    cb = {t for t in tb if t not in NOISE} or set(tb)
    core = len(ca & cb) / max(1, len(ca | cb))          # the meaningful words
    noisy = len(set(ta) & set(tb)) / max(1, len(set(ta) | set(tb)))  # everything
    return 0.75 * core + 0.25 * noisy


def load_fedb() -> list[dict]:
    FEDB_CACHE.parent.mkdir(parents=True, exist_ok=True)
    if not FEDB_CACHE.exists():
        with urllib.request.urlopen(FEDB_JSON) as r:
            FEDB_CACHE.write_bytes(r.read())
    return json.loads(FEDB_CACHE.read_text())


def fetch_images(title: str, fedb: list[dict], want_images: bool,
                 cache: dict) -> list[str]:
    """Best-effort photo pair for one exercise title. [] means 'show a text tile'."""
    if title in cache:
        return cache[title]
    best, bs = None, 0.0
    for e in fedb:
        s = score(title, e["name"])
        if s > bs:
            best, bs = e, s
    if not best or bs < 0.5 or not best.get("images"):
        cache[title] = []
        return []
    out = []
    if want_images:
        IMG.mkdir(parents=True, exist_ok=True)
        for i, rel in enumerate(best["images"][:2]):
            dest = IMG / f"{re.sub(r'[^A-Za-z0-9]+', '-', title).strip('-').lower()}-{i}.jpg"
            if not dest.exists():
                try:
                    with urllib.request.urlopen(FEDB_IMG + rel) as r:
                        dest.write_bytes(r.read())
                except Exception:
                    continue
            out.append("img/" + dest.name)
    cache[title] = out
    return out


def kg(v) -> str:
    return f"{v:g}"


_DROPPED_SECTIONS = ("progress:", "update:", "warmup:", "used:", "id:", "superset:")


def program_sessions(program_text: str) -> list[dict]:
    """Split a Liftoscript program into its weeks and days for the plan tabs.

    '#' prefixes a week, '##' a day, '//' a comment, and '///' starts a trailing
    comment. Everything else is an exercise line; the sections after the sets
    are dropped, and so are the lines of a multi-line {~ ~} script block, so the
    tab stays readable at the rack.
    """
    sessions: list[dict] = []
    week = ""
    in_script = False
    for raw in program_text.splitlines():
        line = raw.split("///", 1)[0].strip()
        if in_script:
            in_script = "~}" not in line
            continue
        if "{~" in line and "~}" not in line.split("{~", 1)[1]:
            in_script = True
            line = line.split("{~", 1)[0].strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("## "):
            sessions.append({"week": week, "day": line[3:].strip(), "exercises": []})
        elif line.startswith("# "):
            week = line[2:].strip()
        elif sessions:
            parts = [p.strip() for p in line.split("/")]
            keep = [parts[0]] + [p for p in parts[1:] if not p.startswith(_DROPPED_SECTIONS)]
            sessions[-1]["exercises"].append(" / ".join(p for p in keep if p))
    return sessions


def build(a):
    ws = analyze.load_workouts()
    if not ws:
        sys.exit("No cached workouts. Run: analyze.py pull --full")
    rep = analyze.build_report(ws, analyze.load_muscles())
    fedb = load_fedb()
    icache: dict[str, list[str]] = {}
    imgs = lambda t: fetch_images(t, fedb, not a.no_images, icache)  # noqa: E731

    profile = {}
    p = STATE / "profile.json"
    if p.exists():
        profile = json.loads(p.read_text())

    now = analyze.parse_ts(ws[-1]["start_time"])
    d30 = now - dt.timedelta(days=30)

    # --- tiles
    # Liftohistory records a duration only when the session was timed.
    timed = [w for w in ws if w.get("duration_seconds")]
    secs = sum(w["duration_seconds"] for w in timed)
    vol = sum((s.get("weight_kg") or 0) * (s.get("reps") or 0)
              for _, _, s in analyze.working_sets(ws))
    this_year = [w for w in ws if w["start_time"][:4] == str(now.year)]
    last30 = [w for w in ws if analyze.parse_ts(w["start_time"]) >= d30]
    tiles = [
        {"label": "Workouts logged", "value": str(len(ws)),
         "tip": f"<b>{len(ws)}</b> workouts, {rep['log_spans']['first']} to {rep['log_spans']['last']}"},
        {"label": f"In {now.year}", "value": str(len(this_year)),
         "tip": f"<b>{len(this_year)}</b> workouts so far in {now.year}"},
        {"label": "Last 30 days", "value": str(len(last30)),
         "tip": f"<b>{len(last30)}</b> workouts since {d30.date()}"
                f"<br>{rep['frequency']['sessions_per_week']}/week over 12 weeks"},
        {"label": "Hours under the bar", "value": str(round(secs / 3600)) if timed else "—",
         "tip": (f"<b>{secs/3600:,.1f}</b> hours across {len(timed)} timed sessions"
                 f"<br>average {secs/60/len(timed):.0f} min per session"
                 + (f"<br>{len(ws) - len(timed)} sessions have no recorded duration"
                    if len(timed) < len(ws) else ""))
                if timed else "No session has a recorded duration"},
        {"label": "Total volume", "value": f"{vol/1000:,.0f}t",
         "tip": f"<b>{vol:,.0f} kg</b> lifted (weight × reps, warmups excluded)"},
    ]

    # --- consistency: one square per day for the last 52 weeks, Monday-first
    end = now.date()
    start = end - dt.timedelta(days=363)
    start -= dt.timedelta(days=start.weekday())
    per_day: dict[dt.date, dict] = {}
    for w in ws:
        d = analyze.parse_ts(w["start_time"]).date()
        if d < start:
            continue
        mins = (w.get("duration_seconds") or 0) / 60
        sets = sum(1 for e in w.get("exercises", []) for s in e.get("sets", [])
                   if s.get("type") != "warmup")
        rec = per_day.setdefault(d, {"minutes": 0, "sets": 0, "titles": []})
        rec["minutes"] += round(mins)
        rec["sets"] += sets
        rec["titles"].append(w.get("day_name") or w.get("program") or "Workout")
    days = []
    d = start
    while d <= end:
        r = per_day.get(d)
        days.append({"date": d.isoformat(), "minutes": r["minutes"] if r else 0,
                     "sets": r["sets"] if r else 0,
                     "titles": " · ".join(r["titles"]) if r else ""})
        d += dt.timedelta(days=1)

    # --- estimated 1RM series for the lifts with the most working sets
    counts: dict[str, int] = {}
    for _, e, _ in analyze.working_sets(ws):
        counts[e["title"]] = counts.get(e["title"], 0) + 1
    e1rm = []
    for title in sorted(counts, key=counts.get, reverse=True)[:6]:
        by_day: dict[str, tuple[float, dict]] = {}
        for w, e, s in analyze.working_sets(ws):
            if e["title"] != title:
                continue
            v = analyze.epley(s.get("weight_kg"), s.get("reps"))
            if not v:
                continue
            day = w["start_time"][:10]
            if day not in by_day or v > by_day[day][0]:
                by_day[day] = (v, s)
        if len(by_day) < 2:
            continue
        pts = [{"date": k, "t": int(dt.date.fromisoformat(k).toordinal()),
                "v": round(v, 1), "from": f"{kg(s['weight_kg'])} kg × {s['reps']}"}
               for k, (v, s) in sorted(by_day.items())]
        # Thin to at most ~40 points so markers stay >=8px apart and readable.
        if len(pts) > 40:
            step = len(pts) / 40
            pts = [pts[min(len(pts) - 1, int(i * step))] for i in range(40)]
        first, last = pts[0]["v"], pts[-1]["v"]
        e1rm.append({"exercise": title, "points": pts,
                     "sub": f"{first:g} → {last:g} kg  ({last-first:+.1f})"})

    # --- muscle bars
    bal = rep["balance"]
    muscle = [{"muscle": k, "sets": v,
               "flag": "under half average" if k in bal["under_half_average"] else "",
               "tip": f"<b>{v}</b> working sets on {k} in the last 8 weeks"
                      f"<br>average across muscles is {bal['average_sets']}"}
              for k, v in bal["sets_by_muscle"].items()]
    note = (f"Average {bal['average_sets']} sets per muscle. "
            f"Push {bal['push_sets']} : pull {bal['pull_sets']} = {bal['push_pull_ratio']}:1"
            + ("  — above 1.5:1." if bal["push_pull_flag"] else "."))
    if bal["unclassified_exercises"]:
        tot = sum(bal["unclassified_exercises"].values())
        top = ", ".join(list(bal["unclassified_exercises"])[:4])
        note += (f" {tot} sets are from exercises missing from exercise-muscles.json"
                 f" ({top}…), so these counts understate whatever those really train.")

    # --- PRs
    prs = [{"exercise": b["exercise"], "set": b["heaviest_set"], "e1rm": b["best_e1rm_kg"],
            "date": b["best_e1rm_date"],
            "tip": f"Best estimated 1RM <b>{b['best_e1rm_kg']} kg</b>"
                   f"<br>from {b['best_e1rm_from']} on {b['best_e1rm_date']}"
                   f"<br>{b['sets_logged']} working sets logged all-time"}
           for b in rep["best_sets"] if b.get("best_e1rm_kg")]
    prs.sort(key=lambda x: -x["e1rm"])

    # --- hero: the single best estimated 1RM
    hero = None
    if prs:
        top = prs[0]
        hero = {"exercise": top["exercise"], "value": f"{top['e1rm']:g} kg",
                "caption": f"estimated 1RM · {top['exercise']} · {top['date']}",
                "images": imgs(top["exercise"])}

    # --- the plan, from the current Liftosaur program
    routines = []
    try:
        program = liftosaur.ok(*liftosaur.call("GET", "/programs/current"),
                               "GET /programs/current")
        live = program_sessions((program or {}).get("text") or "")
    except SystemExit:
        live = []
    last_lift: dict[str, tuple[str, dict]] = {}
    for w in ws:
        for e in w.get("exercises", []):
            for s in e.get("sets", []):
                if s.get("type") != "warmup" and s.get("weight_kg"):
                    last_lift[e["title"]] = (w["start_time"][:10], s)
    for session in live:
        exs = []
        for line in session["exercises"]:
            title, _, rx = (p.strip() for p in line.partition(" / "))
            prev = last_lift.get(title)
            exs.append({
                "name": title, "rx": rx or "—",
                "last": (f"last did {kg(prev[1]['weight_kg'])} kg × {prev[1].get('reps') or '?'}"
                         f" on {prev[0]}") if prev else "no logged history",
                "cue": "",
                "images": imgs(title),
                "tip": f"<b>{title}</b><br>{rx or '—'}",
            })
        heading = " · ".join(x for x in (session["week"], session["day"]) if x)
        routines.append({"title": heading, "exercises": exs})

    data = {
        "name": a.name or profile.get("name") or "Athlete",
        "goal": a.goal or profile.get("goal") or "",
        "hero": hero, "tiles": tiles, "routines": routines, "days": days,
        "e1rm": e1rm, "muscle": muscle, "muscleNote": note, "prs": prs,
        "workoutCount": len(ws),
        "span": f"{rep['log_spans']['first']} to {rep['log_spans']['last']}",
        "built": dt.date.today().isoformat(),
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "data.js").write_text("window.KG = " + json.dumps(data, indent=1) + ";\n")
    html = TEMPLATE.read_text().replace("__TITLE__", f"{data['name']} — Training")
    (OUT / "index.html").write_text(html)
    n_img = len(list(IMG.glob("*.jpg"))) if IMG.exists() else 0
    print(f"built {OUT}/index.html\n"
          f"  {len(ws)} workouts · {len(routines)} program days · {len(e1rm)} lift charts · "
          f"{len(days)} days · {n_img} photos")
    matched = sum(1 for v in icache.values() if v)
    print(f"  photos matched for {matched}/{len(icache)} exercises "
          f"({len(icache)-matched} shown as text tiles)")
    if a.open:
        subprocess.run(["open", str(OUT / "index.html")], check=False)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("build")
    c.add_argument("--name"); c.add_argument("--goal")
    c.add_argument("--open", action="store_true")
    c.add_argument("--no-images", action="store_true")
    c.set_defaults(fn=build)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
