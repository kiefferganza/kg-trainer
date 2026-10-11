#!/usr/bin/env python3
"""Parse Liftosaur's Liftohistory text into kg-trainer's internal workout shape.

Liftosaur returns workout history as text, not JSON. Everything downstream --
analyze.py, dashboard.py -- already speaks a dict shape inherited from the
previous backend, so this module's only job is to produce that exact shape.
Keeping it an adapter is deliberate: the analysis math stays untouched.
"""

from __future__ import annotations

LB_TO_KG = 0.45359237

_HEADER_KEYS = {
    "program": ("program", str),
    "dayName": ("day_name", str),
    "week": ("week", int),
    "dayInWeek": ("day_in_week", int),
    "duration": ("duration_seconds", lambda v: int(float(v.rstrip("s")))),
}


def parse_liftohistory(text: str) -> list[dict]:
    """Every workout record in `text`, in the order it appears."""
    return [_record(block) for block in _blocks(text)]


def _blocks(text: str) -> list[list[str]]:
    """Group lines into records. A record ends at a closing brace on its own line."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        current.append(line)
        if line == "}":
            blocks.append(current)
            current = []
    if current:  # unterminated final record -- take what is there
        blocks.append(current)
    return blocks


def _split_sections(s: str) -> list[str]:
    """Split on '/' but never inside double quotes.

    The program name in a header may itself contain slashes -- "5/3/1 For
    Beginners" is a real built-in program -- so a plain split corrupts it.
    """
    out: list[str] = []
    buf: list[str] = []
    in_quotes = False
    for ch in s:
        if ch == '"':
            in_quotes = not in_quotes
            buf.append(ch)
        elif ch == "/" and not in_quotes:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return [part.strip() for part in out if part.strip()]


def _record(lines: list[str]) -> dict:
    notes: list[str] = []
    i = 0
    while i < len(lines) and lines[i].startswith("//"):
        notes.append(lines[i][2:].strip())
        i += 1

    record = _header(lines[i])
    record["note"] = " ".join(notes) or None
    i += 1

    exercises: list[dict] = []
    pending: list[str] = []
    while i < len(lines) and lines[i] != "}":
        line = lines[i]
        i += 1
        if line.startswith("//"):
            pending.append(line[2:].strip())
            continue
        exercise = _exercise(line)
        exercise["note"] = " ".join(pending) or None
        pending = []
        exercises.append(exercise)
    record["exercises"] = exercises
    return record


def _header(line: str) -> dict:
    body = line.split("exercises:")[0]
    parts = _split_sections(body)
    record: dict = {"start_time": parts[0] if parts else None, "program": None,
                    "day_name": None, "week": None, "day_in_week": None,
                    "duration_seconds": None}
    for part in parts[1:]:
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        mapped = _HEADER_KEYS.get(key.strip())
        if not mapped:
            continue
        field, cast = mapped
        try:
            record[field] = cast(value.strip().strip('"'))
        except ValueError:
            record[field] = None
    return record


def _exercise(line: str) -> dict:
    sections = _split_sections(line)
    exercise: dict = {"title": sections[0], "target": None, "sets": []}
    warmups: list[dict] = []
    working: list[dict] = []
    for section in sections[1:]:
        if section.startswith("warmup:"):
            warmups = _sets(section[len("warmup:"):], "warmup")
        elif section.startswith("target:"):
            exercise["target"] = section[len("target:"):].strip() or None
        else:
            working = _sets(section, "normal")
    exercise["sets"] = warmups + working
    return exercise


def _sets(text: str, kind: str) -> list[dict]:
    out: list[dict] = []
    for group in text.split(","):
        group = group.strip()
        if group:
            out.extend(_group(group, kind))
    return out


def _group(group: str, kind: str) -> list[dict]:
    """Expand one set group. '3x8 185lb @7' is three sets, not one."""
    tokens = group.split()
    count, reps, reps_left, amrap = _scheme(tokens[0]) if tokens else (1, None, None, False)
    weight_kg = rpe = duration = None
    rpe_logged = False
    for token in tokens[1:]:
        if token.startswith("@"):
            value = token[1:]
            rpe_logged = value.endswith("+")
            rpe = _number(value.rstrip("+"))
        elif token.endswith("lb") or token.endswith("kg"):
            weight_kg = _weight(token)
        elif token.endswith("s") or "s|" in token:
            duration = token
    return [{"type": kind, "reps": reps, "reps_left": reps_left, "amrap": amrap,
             "weight_kg": weight_kg, "rpe": rpe, "rpe_logged": rpe_logged,
             "duration": duration} for _ in range(count)]


def _scheme(token: str) -> tuple[int, int | None, int | None, bool]:
    """'3x8' -> (3, 8, None, False).  '3x8|7' -> (3, 8, 7, False).  '1x5+' -> (1, 5, None, True)."""
    if "x" not in token:
        return 1, None, None, False
    count_s, reps_s = token.split("x", 1)
    amrap = reps_s.endswith("+")
    reps_s = reps_s.rstrip("+")
    reps_left = None
    if "|" in reps_s:
        reps_s, left_s = reps_s.split("|", 1)
        reps_left = _int(left_s)
    if "-" in reps_s:  # a rep range belongs to a target; take its low end
        reps_s = reps_s.split("-", 1)[0]
    return (_int(count_s) or 1), _int(reps_s), reps_left, amrap


def _weight(token: str) -> float | None:
    unit, raw = token[-2:], token[:-2]
    if "|" in raw:  # unilateral load -- take the first side
        raw = raw.split("|", 1)[0]
    value = _number(raw)
    if value is None:
        return None
    return round(value * LB_TO_KG, 2) if unit == "lb" else round(value, 2)


def _int(s: str) -> int | None:
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def _number(s: str) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None
