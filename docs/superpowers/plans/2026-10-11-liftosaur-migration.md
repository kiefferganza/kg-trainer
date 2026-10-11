# Liftosaur Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the kg-trainer skill's Hevy backend with Liftosaur, so that a mesocycle is one Liftoscript program carrying its own weeks and native progression instead of four static routines a coach hand-edits mid-block.

**Architecture:** A new REST client (`scripts/liftosaur.py`) talks to `https://www.liftosaur.com/api/v1`. A new parser (`scripts/liftohistory.py`) converts Liftosaur's Liftohistory workout text into the **exact internal workout dict shape `analyze.py` already consumes** — so the parser is an adapter and the analysis, dashboard and check-in code change as little as possible. Hevy is deleted outright. Program correctness is verified by Liftosaur's own `/playground` and `/program-stats` endpoints before a push is reported as done.

**Tech Stack:** Python 3.11+, standard library only at runtime (`urllib`, `json`, `re`, `argparse`). `pytest` as a development dependency. No new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-10-11-liftosaur-migration-spec.md`

## Global Constraints

- Python 3.11+. Runtime code uses the standard library only; `pytest` is dev-only.
- Base URL is exactly `https://www.liftosaur.com/api/v1`.
- Auth header is exactly `Authorization: Bearer lftsk_...`.
- Success payloads are wrapped as `{"data": ...}`; errors as `{"error": {code, message, details}}`.
- `1 lb = 0.45359237 kg` exactly. All internal weights are kilograms.
- `/history` and `/measurements/:key` paginate with `limit` (default 50, **maximum 200**) and `cursor`; continue while `hasMore` is true, passing `nextCursor` back as `cursor`.
- Bodyweight measurement key is `weight`; body fat is `bodyfat`. The keys `sleep`, `calories` and `protein` are read-only and return 400 on write.
- **The API key is never printed, logged, committed, written into a plan or dashboard, or included in an error message.** Failures report the HTTP status only.
- `scripts/paths.py` state-directory resolution is unchanged.
- The repository has no test suite today. Task 1 creates it; every later task adds to it.

---

### Task 1: Test scaffolding and the Liftohistory parser

The keystone. Liftosaur returns workout history as text, so nothing else can be trusted until this parses correctly. It needs no network and no API key, so it is fully unit-testable.

**Files:**
- Create: `scripts/liftohistory.py`
- Create: `tests/test_liftohistory.py`
- Create: `tests/fixtures/liftohistory_reference.txt`
- Create: `pytest.ini`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `parse_liftohistory(text: str) -> list[dict]` — every record in `text`.
  - `LB_TO_KG: float` — the constant `0.45359237`.
  - Record shape, which later tasks depend on exactly:
    ```python
    {"start_time": "2026-02-28T10:45:30Z", "program": str | None,
     "day_name": str | None, "week": int | None, "day_in_week": int | None,
     "duration_seconds": int | None, "note": str | None,
     "exercises": [
        {"title": "Bench Press, Barbell", "note": str | None,
         "target": "3x8-12 185lb @8 90s" | None,
         "sets": [{"type": "warmup" | "normal", "reps": int | None,
                   "reps_left": int | None, "amrap": bool,
                   "weight_kg": float | None, "rpe": float | None,
                   "rpe_logged": bool, "duration": str | None}]}]}
    ```
  - `id` is **not** set by the parser; the caller fills it from the API record.

- [ ] **Step 1: Save the authoritative format reference as a fixture**

The reference below is the verbatim example from Liftosaur's `get_liftohistory_reference`. Write it to `tests/fixtures/liftohistory_reference.txt`:

```
// Optional workout note
2026-02-28T10:45:30Z / program: "5/3/1 For Beginners" / dayName: "Push Day" / week: 1 / dayInWeek: 5 / duration: 1235s / exercises: {
  // Optional exercise note
  Bench Press, Barbell / 3x8 185lb @7, 1x6 185lb @9 / warmup: 1x10 95lb, 1x5 135lb / target: 3x8-12 185lb @8 90s
  OHP / 3x10 95lb / target: 3x10 95lb 60s
  Pull Ups / 3x8|7 0lb / target: 3x10 0lb 60s
  Wall Sit / 2x1 0kg 60s, 1x1 0kg 52s / target: 3x1 0kg 60s|45s
}
```

- [ ] **Step 2: Add `pytest.ini` so `scripts/` is importable from tests**

```ini
[pytest]
testpaths = tests
pythonpath = scripts
```

- [ ] **Step 3: Write the failing tests**

Write `tests/test_liftohistory.py`. Note the fourth test: the program name in the reference is `"5/3/1 For Beginners"`, which contains the same `/` character that separates sections. A naive `line.split("/")` corrupts it. That is the subtlest bug in this task and it has its own test.

```python
import pathlib
import pytest
from liftohistory import parse_liftohistory, LB_TO_KG

REF = (pathlib.Path(__file__).parent / "fixtures" / "liftohistory_reference.txt").read_text()


@pytest.fixture
def record():
    records = parse_liftohistory(REF)
    assert len(records) == 1
    return records[0]


def test_header_metadata(record):
    assert record["start_time"] == "2026-02-28T10:45:30Z"
    assert record["day_name"] == "Push Day"
    assert record["week"] == 1
    assert record["day_in_week"] == 5
    assert record["duration_seconds"] == 1235
    assert record["note"] == "Optional workout note"


def test_quoted_program_name_containing_slashes_survives(record):
    assert record["program"] == "5/3/1 For Beginners"


def test_exercise_titles_keep_equipment_suffix(record):
    assert [e["title"] for e in record["exercises"]] == [
        "Bench Press, Barbell", "OHP", "Pull Ups", "Wall Sit"]


def test_exercise_note_attaches_to_following_exercise(record):
    assert record["exercises"][0]["note"] == "Optional exercise note"
    assert record["exercises"][1]["note"] is None


def test_set_group_expands_to_one_dict_per_set(record):
    bench = record["exercises"][0]
    working = [s for s in bench["sets"] if s["type"] == "normal"]
    # "3x8 185lb @7, 1x6 185lb @9" is four sets, not two.
    assert len(working) == 4
    assert [s["reps"] for s in working] == [8, 8, 8, 6]
    assert [s["rpe"] for s in working] == [7.0, 7.0, 7.0, 9.0]


def test_pounds_convert_to_kilograms(record):
    bench = record["exercises"][0]
    working = [s for s in bench["sets"] if s["type"] == "normal"]
    assert working[0]["weight_kg"] == pytest.approx(185 * LB_TO_KG, abs=0.01)


def test_warmups_are_typed_and_kept_separate(record):
    bench = record["exercises"][0]
    warmups = [s for s in bench["sets"] if s["type"] == "warmup"]
    assert [s["reps"] for s in warmups] == [10, 5]
    assert warmups[0]["weight_kg"] == pytest.approx(95 * LB_TO_KG, abs=0.01)


def test_target_section_is_kept_as_raw_text(record):
    assert record["exercises"][0]["target"] == "3x8-12 185lb @8 90s"


def test_unilateral_reps_split_right_and_left(record):
    pullups = record["exercises"][2]
    first = [s for s in pullups["sets"] if s["type"] == "normal"][0]
    assert first["reps"] == 8
    assert first["reps_left"] == 7


def test_completed_hold_duration_is_captured(record):
    wall_sit = record["exercises"][3]
    working = [s for s in wall_sit["sets"] if s["type"] == "normal"]
    assert len(working) == 3           # "2x1 ... , 1x1 ..."
    assert working[0]["duration"] == "60s"
    assert working[2]["duration"] == "52s"


def test_amrap_marker_is_flagged():
    text = ('2026-03-01T08:00:00Z / exercises: {\n'
            '  Squat / 1x5+ 100kg\n'
            '}\n')
    s = parse_liftohistory(text)[0]["exercises"][0]["sets"][0]
    assert s["amrap"] is True
    assert s["reps"] == 5
    assert s["weight_kg"] == pytest.approx(100.0)


def test_logged_rpe_is_distinguished_from_prescribed():
    text = ('2026-03-01T08:00:00Z / exercises: {\n'
            '  Squat / 1x5 100kg @8+\n'
            '}\n')
    s = parse_liftohistory(text)[0]["exercises"][0]["sets"][0]
    assert s["rpe"] == 8.0
    assert s["rpe_logged"] is True


def test_minimal_record_without_optional_metadata():
    text = ('2026-03-02T09:30:00Z / exercises: {\n'
            '  Deadlift / 3x5 150kg\n'
            '}\n')
    r = parse_liftohistory(text)[0]
    assert r["program"] is None and r["week"] is None and r["note"] is None
    assert len(r["exercises"][0]["sets"]) == 3


def test_multiple_records_separated_by_blank_lines():
    text = REF + "\n\n" + (
        '2026-03-03T09:00:00Z / exercises: {\n'
        '  Squat / 1x5 100kg\n'
        '}\n')
    assert len(parse_liftohistory(text)) == 2


def test_empty_text_yields_no_records():
    assert parse_liftohistory("") == []
    assert parse_liftohistory("\n\n  \n") == []
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_liftohistory.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'liftohistory'`.

- [ ] **Step 5: Write the parser**

Write `scripts/liftohistory.py`. The section splitter is quote-aware, which is what makes `"5/3/1 For Beginners"` survive.

```python
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
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_liftohistory.py -v`
Expected: 15 passed.

- [ ] **Step 7: Commit**

```bash
git add pytest.ini scripts/liftohistory.py tests/
git commit -m "feat: parse Liftohistory workout text into the internal workout shape"
```

---

### Task 2: The Liftosaur REST client

Replaces `scripts/hevy.py`. Tested against a fake transport, so it needs neither network nor an API key to verify.

**Files:**
- Create: `scripts/liftosaur.py`
- Create: `tests/test_liftosaur.py`

**Interfaces:**
- Consumes: `paths.state_dir()` from `scripts/paths.py`; nothing from Task 1.
- Produces, for Tasks 4, 5, 6 and 8:
  - `BASE: str` — `"https://www.liftosaur.com/api/v1"`
  - `api_key() -> str`
  - `call(method: str, path: str, params: dict | None = None, body: dict | None = None) -> tuple[int, object]`
  - `ok(status: int, payload, what: str)` — returns `payload["data"]`, exits non-zero on HTTP >= 400
  - `paginate(path: str, key: str, params: dict | None = None, limit: int = 200) -> list[dict]`
  - CLI subcommands: `ping`, `exercises`, `settings`, `history`, `measurements`, `measurement`, `programs`, `program-get`, `program-push`, `program-stats`, `playground`

- [ ] **Step 1: Write the failing tests**

Write `tests/test_liftosaur.py`:

```python
import json
import pytest
import liftosaur


def test_base_url_is_the_documented_one():
    assert liftosaur.BASE == "https://www.liftosaur.com/api/v1"


def test_ok_unwraps_the_data_envelope():
    assert liftosaur.ok(200, {"data": {"units": "kg"}}, "GET /settings") == {"units": "kg"}


def test_ok_exits_on_error_and_never_leaks_the_key(monkeypatch, capsys):
    monkeypatch.setenv("LIFTOSAUR_API_KEY", "lftsk_supersecret")
    payload = {"error": {"code": "unauthorized", "message": "bad key"}}
    with pytest.raises(SystemExit) as exc:
        liftosaur.ok(401, payload, "GET /settings")
    message = str(exc.value) + capsys.readouterr().err
    assert "401" in message
    assert "lftsk_supersecret" not in message


def test_api_key_prefers_the_environment(monkeypatch):
    monkeypatch.setenv("LIFTOSAUR_API_KEY", "lftsk_fromenv")
    assert liftosaur.api_key() == "lftsk_fromenv"


def test_api_key_falls_back_to_the_state_file(monkeypatch, tmp_path):
    monkeypatch.delenv("LIFTOSAUR_API_KEY", raising=False)
    (tmp_path / "liftosaur-key").write_text("lftsk_fromfile\n")
    monkeypatch.setattr(liftosaur.paths, "state_dir", lambda: tmp_path)
    assert liftosaur.api_key() == "lftsk_fromfile"


def test_missing_key_exits_with_guidance(monkeypatch, tmp_path):
    monkeypatch.delenv("LIFTOSAUR_API_KEY", raising=False)
    monkeypatch.setattr(liftosaur.paths, "state_dir", lambda: tmp_path)
    with pytest.raises(SystemExit) as exc:
        liftosaur.api_key()
    assert "API Keys" in str(exc.value)


def test_paginate_follows_the_cursor_until_has_more_is_false(monkeypatch):
    pages = [
        (200, {"data": {"records": [{"id": "a"}, {"id": "b"}],
                        "hasMore": True, "nextCursor": "cur1"}}),
        (200, {"data": {"records": [{"id": "c"}], "hasMore": False}}),
    ]
    seen = []

    def fake_call(method, path, params=None, body=None):
        seen.append(dict(params or {}))
        return pages[len(seen) - 1]

    monkeypatch.setattr(liftosaur, "call", fake_call)
    out = liftosaur.paginate("/history", "records")
    assert [r["id"] for r in out] == ["a", "b", "c"]
    assert seen[0]["limit"] == 200          # the documented maximum
    assert "cursor" not in seen[0]
    assert seen[1]["cursor"] == "cur1"


def test_paginate_stops_when_cursor_is_missing_despite_has_more(monkeypatch):
    def fake_call(method, path, params=None, body=None):
        return 200, {"data": {"records": [{"id": "a"}], "hasMore": True}}

    monkeypatch.setattr(liftosaur, "call", fake_call)
    assert len(liftosaur.paginate("/history", "records")) == 1


def test_paginate_passes_extra_params_through(monkeypatch):
    seen = {}

    def fake_call(method, path, params=None, body=None):
        seen.update(params or {})
        return 200, {"data": {"records": [], "hasMore": False}}

    monkeypatch.setattr(liftosaur, "call", fake_call)
    liftosaur.paginate("/history", "records", params={"startDate": "2026-09-01"})
    assert seen["startDate"] == "2026-09-01"


def test_call_sends_bearer_auth_and_json_body(monkeypatch):
    monkeypatch.setenv("LIFTOSAUR_API_KEY", "lftsk_abc")
    captured = {}

    class FakeResponse:
        status = 201

        def read(self):
            return json.dumps({"data": {"id": "p1"}}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.method
        captured["auth"] = req.get_header("Authorization")
        captured["body"] = json.loads(req.data)
        return FakeResponse()

    monkeypatch.setattr(liftosaur.urllib.request, "urlopen", fake_urlopen)
    status, payload = liftosaur.call("POST", "/programs", body={"name": "M1", "text": "# Week 1"})
    assert status == 201
    assert payload["data"]["id"] == "p1"
    assert captured["method"] == "POST"
    assert captured["auth"] == "Bearer lftsk_abc"
    assert captured["body"]["name"] == "M1"
    assert captured["url"] == liftosaur.BASE + "/programs"


def test_call_encodes_query_params(monkeypatch):
    monkeypatch.setenv("LIFTOSAUR_API_KEY", "lftsk_abc")
    captured = {}

    class FakeResponse:
        status = 200

        def read(self):
            return b'{"data": {}}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return FakeResponse()

    monkeypatch.setattr(liftosaur.urllib.request, "urlopen", fake_urlopen)
    liftosaur.call("GET", "/history", params={"limit": 200, "startDate": "2026-09-01"})
    assert "limit=200" in captured["url"]
    assert "startDate=2026-09-01" in captured["url"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_liftosaur.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'liftosaur'`.

- [ ] **Step 3: Write the client**

Write `scripts/liftosaur.py`:

```python
#!/usr/bin/env python3
"""Liftosaur REST client.

Usage:
  liftosaur.py ping                        # validate the key, show account units
  liftosaur.py exercises [pattern]         # match the built-in exercise list
  liftosaur.py settings
  liftosaur.py history [--since YYYY-MM-DD] [--until YYYY-MM-DD]
  liftosaur.py measurements                # overview of tracked metrics
  liftosaur.py measurement weight          # one full series
  liftosaur.py programs
  liftosaur.py program-get <id|current> [--out FILE]
  liftosaur.py program-push <file> [--name NAME] [--id ID]
  liftosaur.py program-stats <file>
  liftosaur.py playground <file> [--week N] [--day N]

The key is read from $LIFTOSAUR_API_KEY, else from <state>/liftosaur-key.
It is never printed, and a failure reports the HTTP status, not the key.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import paths  # noqa: E402

BASE = "https://www.liftosaur.com/api/v1"
EXERCISES = pathlib.Path(__file__).resolve().parent.parent / "references" / "exercises.txt"


def api_key() -> str:
    env = os.environ.get("LIFTOSAUR_API_KEY")
    if env and env.strip():
        return env.strip()
    key_file = paths.state_dir() / "liftosaur-key"
    if key_file.exists():
        value = key_file.read_text().strip()
        if value:
            return value
    sys.exit("No Liftosaur API key. Set $LIFTOSAUR_API_KEY, or save it to "
             f"{key_file} and chmod 600 it. Create one in the Liftosaur app "
             "under Settings -> API Keys. A premium subscription is required.")


def call(method: str, path: str, params: dict | None = None, body: dict | None = None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {api_key()}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"null")
        except json.JSONDecodeError:
            return exc.code, None


def ok(status: int, payload, what: str):
    """Unwrap {"data": ...}, or exit with the status. Never echoes the key."""
    if status >= 400:
        error = (payload or {}).get("error") or {}
        detail = " ".join(str(x) for x in (error.get("code"), error.get("message")) if x)
        hint = ""
        if status == 401:
            hint = "  (check the key, and that the subscription is active)"
        elif status == 422:
            hint = "  (invalid Liftoscript -- run program-stats or playground to locate it)"
        sys.exit(f"{what} failed: HTTP {status} {detail}{hint}".rstrip())
    return (payload or {}).get("data")


def paginate(path: str, key: str, params: dict | None = None, limit: int = 200) -> list[dict]:
    """Follow Liftosaur's cursor pagination. `limit` maxes out at 200."""
    query = dict(params or {})
    query["limit"] = limit
    out: list[dict] = []
    while True:
        data = ok(*call("GET", path, params=query), f"GET {path}") or {}
        out.extend(data.get(key, []))
        if not data.get("hasMore"):
            return out
        cursor = data.get("nextCursor")
        if not cursor:
            return out
        query["cursor"] = cursor


# --- commands -------------------------------------------------------------

def cmd_ping(_):
    data = ok(*call("GET", "/settings"), "GET /settings")
    print(json.dumps(data, indent=2))
    if (data or {}).get("units") not in ("kg", None):
        print(f"note: the account reports units={data['units']}; "
              "kg-trainer works in kilograms and converts on read.", file=sys.stderr)


def cmd_settings(_):
    print(json.dumps(ok(*call("GET", "/settings"), "GET /settings"), indent=2))


def cmd_exercises(a):
    if not EXERCISES.exists():
        sys.exit(f"No exercise list at {EXERCISES}. Run scripts/fetch_reference.py.")
    names = [n.strip() for n in EXERCISES.read_text().splitlines() if n.strip()]
    needle = (a.pattern or "").lower()
    hits = [n for n in names if needle in n.lower()]
    if not hits:
        sys.exit(f"No built-in exercise matches {a.pattern!r}. "
                 "Pick the closest built-in name, or create a custom exercise in the app.")
    for name in hits:
        print(name)


def cmd_history(a):
    params = {}
    if a.since:
        params["startDate"] = a.since
    if a.until:
        params["endDate"] = a.until
    print(json.dumps(paginate("/history", "records", params=params), indent=2))


def cmd_measurements(_):
    print(json.dumps(ok(*call("GET", "/measurements"), "GET /measurements"), indent=2))


def cmd_measurement(a):
    data = paginate(f"/measurements/{a.key}", "values")
    print(json.dumps(data, indent=2))


def cmd_programs(_):
    print(json.dumps(ok(*call("GET", "/programs"), "GET /programs"), indent=2))


def cmd_program_get(a):
    data = ok(*call("GET", f"/programs/{a.id}"), f"GET /programs/{a.id}")
    if a.out:
        pathlib.Path(a.out).write_text(data["text"])
        print(f"wrote {a.out}  ({data['name']}, id {data['id']})")
    else:
        print(data["text"])


def cmd_program_push(a):
    text = pathlib.Path(a.file).read_text()
    if a.id:
        body = {"text": text}
        if a.name:
            body["name"] = a.name
        data = ok(*call("PUT", f"/programs/{a.id}", body=body), f"PUT /programs/{a.id}")
    else:
        if not a.name:
            sys.exit("--name is required when creating a program.")
        data = ok(*call("POST", "/programs", body={"name": a.name, "text": text}),
                  "POST /programs")
    print(json.dumps({"id": data["id"], "name": data["name"]}, indent=2))


def cmd_program_stats(a):
    text = pathlib.Path(a.file).read_text()
    data = ok(*call("POST", "/program-stats", body={"programText": text}),
              "POST /program-stats")
    print(json.dumps(data, indent=2))


def cmd_playground(a):
    text = pathlib.Path(a.file).read_text()
    body = {"programText": text}
    if a.week:
        body["week"] = a.week
    if a.day:
        body["day"] = a.day
    print(json.dumps(ok(*call("POST", "/playground", body=body), "POST /playground"), indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ping").set_defaults(fn=cmd_ping)
    sub.add_parser("settings").set_defaults(fn=cmd_settings)
    c = sub.add_parser("exercises"); c.add_argument("pattern", nargs="?", default="")
    c.set_defaults(fn=cmd_exercises)
    c = sub.add_parser("history"); c.add_argument("--since"); c.add_argument("--until")
    c.set_defaults(fn=cmd_history)
    sub.add_parser("measurements").set_defaults(fn=cmd_measurements)
    c = sub.add_parser("measurement"); c.add_argument("key"); c.set_defaults(fn=cmd_measurement)
    sub.add_parser("programs").set_defaults(fn=cmd_programs)
    c = sub.add_parser("program-get"); c.add_argument("id"); c.add_argument("--out")
    c.set_defaults(fn=cmd_program_get)
    c = sub.add_parser("program-push"); c.add_argument("file")
    c.add_argument("--name"); c.add_argument("--id"); c.set_defaults(fn=cmd_program_push)
    c = sub.add_parser("program-stats"); c.add_argument("file"); c.set_defaults(fn=cmd_program_stats)
    c = sub.add_parser("playground"); c.add_argument("file")
    c.add_argument("--week", type=int); c.add_argument("--day", type=int)
    c.set_defaults(fn=cmd_playground)

    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_liftosaur.py -v`
Expected: 11 passed.

- [ ] **Step 5: Run the whole suite**

Run: `python3 -m pytest -v`
Expected: 26 passed.

- [ ] **Step 6: Commit**

```bash
git add scripts/liftosaur.py tests/test_liftosaur.py
git commit -m "feat: add Liftosaur REST client with cursor pagination"
```

---

### Task 3: Vendor the Liftosaur references and build the muscle map

`list_exercises` returns names with no muscle data, but the balance table keys off a primary muscle group. This task vendors the reference documents and builds the name-to-muscle map from the `free-exercise-db.json` the dashboard already caches.

The Liftosaur MCP reference tools need **no authentication**, so this runs without a key.

**Files:**
- Create: `scripts/fetch_reference.py`
- Create: `references/exercises.txt`
- Create: `references/liftohistory.md`
- Create: `references/liftoscript.md`
- Create: `references/liftosaur-api.md`
- Create: `references/exercise-muscles.json`
- Create: `tests/test_exercise_muscles.py`

**Interfaces:**
- Consumes: `tests/fixtures/liftohistory_reference.txt` from Task 1 (as a cross-check that the vendored reference still matches).
- Produces, for Task 4:
  - `references/exercise-muscles.json` — `{"<exercise title>": "<muscle group>"}`, titles exactly as Liftosaur writes them including any `, Equipment` suffix.
  - `muscle_for(title: str, table: dict) -> str` in `scripts/fetch_reference.py`, which normalises a title and falls back to `"unknown"`.
  - Muscle group vocabulary, matching what `analyze.py` already uses: `chest`, `shoulders`, `triceps`, `biceps`, `lats`, `upper_back`, `traps`, `lower_back`, `abdominals`, `quadriceps`, `hamstrings`, `glutes`, `calves`, `adductors`, `forearms`, `unknown`.

- [ ] **Step 1: Write `scripts/fetch_reference.py`**

```python
#!/usr/bin/env python3
"""Vendor Liftosaur's reference documents into references/.

The Liftosaur MCP server exposes reference tools that need no authentication,
so this runs without an API key and without a subscription. Re-run it when
Liftosaur updates its exercise list or its syntax docs.

Usage:
  fetch_reference.py            # write every reference file
  fetch_reference.py --check    # fail if a vendored file is out of date
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.request

MCP = "https://www.liftosaur.com/mcp"
REFS = pathlib.Path(__file__).resolve().parent.parent / "references"

TOOLS = {
    "exercises.txt": "list_exercises",
    "liftohistory.md": "get_liftohistory_reference",
    "liftoscript.md": "get_liftoscript_reference",
}

MUSCLES = ("chest shoulders triceps biceps lats upper_back traps lower_back "
           "abdominals quadriceps hamstrings glutes calves adductors forearms").split()


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


def build_muscle_map(names: list[str]) -> dict:
    """Seed the map from the cached free-exercise-db, which carries primaryMuscles."""
    fedb_path = REFS.parent / "scripts" / "free-exercise-db.json"
    if not fedb_path.exists():
        import paths  # local store holds the dashboard's cached copy
        fedb_path = paths.state_dir() / "data" / "free-exercise-db.json"
    fedb = json.loads(fedb_path.read_text()) if fedb_path.exists() else []
    by_name = {}
    for entry in fedb:
        primary = (entry.get("primaryMuscles") or [None])[0]
        if primary:
            by_name[normalise(entry.get("name", ""))] = ALIASES.get(primary, primary)
    out = {}
    for name in names:
        key = normalise(name)
        base = normalise(name.split(",")[0])
        out[name] = by_name.get(key) or by_name.get(base) or "unknown"
    return out


ALIASES = {
    "quadriceps": "quadriceps", "hamstrings": "hamstrings", "glutes": "glutes",
    "calves": "calves", "chest": "chest", "shoulders": "shoulders",
    "triceps": "triceps", "biceps": "biceps", "lats": "lats", "traps": "traps",
    "middle back": "upper_back", "lower back": "lower_back",
    "abdominals": "abdominals", "adductors": "adductors", "abductors": "glutes",
    "forearms": "forearms", "neck": "traps",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
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

    names = [n.strip() for n in (REFS / "exercises.txt").read_text().splitlines()
             if n.strip() and not n.endswith(":")]
    table = build_muscle_map(names)
    unknown = sorted(k for k, v in table.items() if v == "unknown")
    if args.check:
        if stale:
            sys.exit("stale vendored references: " + ", ".join(stale))
        print("vendored references are current")
        return
    (REFS / "exercise-muscles.json").write_text(json.dumps(table, indent=2, sort_keys=True))
    print(f"wrote references/exercise-muscles.json  ({len(table)} exercises, "
          f"{len(unknown)} unknown)")
    if unknown:
        print("unmapped -- add these by hand before trusting the balance table:")
        for name in unknown[:40]:
            print(f"  {name}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it and inspect the unmapped list**

Run: `python3 scripts/fetch_reference.py`
Expected: four files written, plus a printed list of exercises the free-exercise-db could not match.

- [ ] **Step 3: Hand-map every remaining `unknown`**

Edit `references/exercise-muscles.json` directly and assign each `"unknown"` a value from the vocabulary in the Interfaces block above. Do not leave any `"unknown"` for an exercise that appears in the user's own history — an unmapped exercise silently undercounts a muscle, which is the exact failure mode the previous backend had with its catch-all group.

- [ ] **Step 4: Write `references/liftosaur-api.md`**

Copy the "Liftosaur API facts" and "Liftohistory" sections from the spec at `docs/superpowers/specs/2026-10-11-liftosaur-migration-spec.md` into `references/liftosaur-api.md`, as the runtime reference the skill reads before making a call. It must state, at minimum: the base URL, the `Bearer lftsk_...` header, the `{"data": ...}` envelope, the error envelope, the endpoint table, `limit` max 200 with `cursor`/`hasMore` pagination, 401 on a bad key, 422 on invalid Liftoscript, 409 on a duplicate measurement timestamp, that `sleep`/`calories`/`protein` are read-only, and that the `/workout/*` endpoints need extra headers and are not used.

- [ ] **Step 5: Write the failing test**

Write `tests/test_exercise_muscles.py`:

```python
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from fetch_reference import MUSCLES, muscle_for

REFS = pathlib.Path(__file__).resolve().parent.parent / "references"
TABLE = json.loads((REFS / "exercise-muscles.json").read_text())


def test_every_muscle_value_is_in_the_vocabulary():
    bad = {name: m for name, m in TABLE.items() if m not in MUSCLES}
    assert bad == {}, f"unmapped or misspelled muscle groups: {sorted(bad)[:20]}"


def test_the_table_covers_the_built_in_exercise_list():
    names = [n.strip() for n in (REFS / "exercises.txt").read_text().splitlines()
             if n.strip() and not n.endswith(":")]
    missing = [n for n in names if n not in TABLE]
    assert missing == [], f"exercises with no muscle entry: {missing[:20]}"


def test_equipment_variants_fall_back_to_the_base_exercise():
    assert muscle_for("Bench Press, Dumbbell", {"Bench Press": "chest"}) == "chest"


def test_unknown_exercise_reports_unknown_rather_than_guessing():
    assert muscle_for("Totally Made Up Lift", {"Bench Press": "chest"}) == "unknown"
```

- [ ] **Step 6: Run the tests**

Run: `python3 -m pytest tests/test_exercise_muscles.py -v`
Expected: 4 passed. If `test_every_muscle_value_is_in_the_vocabulary` fails, return to Step 3 — the failure names the entries still to map.

- [ ] **Step 7: Commit**

```bash
git add scripts/fetch_reference.py references/exercises.txt references/liftohistory.md \
        references/liftoscript.md references/liftosaur-api.md \
        references/exercise-muscles.json tests/test_exercise_muscles.py
git commit -m "feat: vendor Liftosaur references and build the exercise muscle map"
```

---

### Task 4: Migrate `analyze.py` to Liftosaur

The analysis math does not change. What changes is where workouts come from and how a muscle group is resolved: the old code looked up `e["exercise_template_id"]` in a cached Hevy catalog, and there are no template ids in Liftosaur.

**Files:**
- Modify: `scripts/analyze.py` — replace `load_catalog`, `cmd_pull`; change the muscle lookup in `build_report`; drop the `unclassified_exercises` catch-all wording
- Create: `tests/test_analyze.py`
- Create: `tests/fixtures/history_sample.txt`

**Interfaces:**
- Consumes: `parse_liftohistory` and `LB_TO_KG` from Task 1; `liftosaur.paginate` from Task 2; `references/exercise-muscles.json` and `muscle_for` from Task 3.
- Produces, for Task 6: unchanged `build_report(ws, muscles) -> dict` report keys — `frequency`, `best_sets`, `trend`, `balance`, `rpe`, `bodyweight` — and an unchanged `data/workouts.jsonl` record shape, now with `"id"` set from the API record.

- [ ] **Step 1: Write the fixture**

Write `tests/fixtures/history_sample.txt` — two records, a week apart, with a warmup, an RPE, and an exercise that is deliberately absent from the muscle map:

```
2026-09-01T09:00:00Z / program: "M1" / dayName: "Upper A" / week: 1 / dayInWeek: 1 / exercises: {
  Bench Press / 3x8 80kg @8 / warmup: 1x10 40kg
  Lat Pulldown, Cable / 3x10 50kg @7
  Mystery Machine / 2x12 30kg
}

2026-09-08T09:00:00Z / program: "M1" / dayName: "Upper A" / week: 2 / dayInWeek: 1 / exercises: {
  Bench Press / 3x8 82.5kg @9 / warmup: 1x10 40kg
  Lat Pulldown, Cable / 3x10 52.5kg @8
}
```

- [ ] **Step 2: Write the failing tests**

Write `tests/test_analyze.py`:

```python
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import analyze
from liftohistory import parse_liftohistory

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "history_sample.txt"
MUSCLES = {"Bench Press": "chest", "Lat Pulldown, Cable": "lats"}


@pytest.fixture
def workouts():
    return parse_liftohistory(FIX.read_text())


def test_warmups_are_excluded_from_working_sets(workouts):
    sets = list(analyze.working_sets(workouts))
    assert len(sets) == 15          # (3+3+2) + (3+3), warmups dropped
    assert all(s["type"] != "warmup" for _, _, s in sets)


def test_epley_matches_the_logged_set(workouts):
    assert analyze.epley(80, 8) == pytest.approx(80 * (1 + 8 / 30))


def test_epley_refuses_high_rep_sets():
    assert analyze.epley(30, 12) is None


def test_balance_counts_sets_per_muscle(workouts):
    report = analyze.build_report(workouts, MUSCLES)
    by_muscle = report["balance"]["sets_by_muscle"]
    assert by_muscle["chest"] == 6
    assert by_muscle["lats"] == 6


def test_unmapped_exercise_is_named_not_silently_dropped(workouts):
    report = analyze.build_report(workouts, MUSCLES)
    assert report["balance"]["sets_by_muscle"].get("unknown") == 2
    assert report["balance"]["unclassified_exercises"] == {"Mystery Machine": 2}


def test_average_top_set_rpe_uses_the_heaviest_working_set(workouts):
    report = analyze.build_report(workouts, MUSCLES)
    # top sets: bench @8, pulldown @7, bench @9, pulldown @8  (Mystery has no RPE)
    assert report["rpe"]["top_sets_with_rpe"] == 4
    assert report["rpe"]["average_top_set_rpe"] == pytest.approx(8.0)


def test_pull_writes_records_with_their_api_id(tmp_path, monkeypatch):
    monkeypatch.setattr(analyze, "DATA", tmp_path)
    monkeypatch.setattr(analyze, "WORKOUTS", tmp_path / "workouts.jsonl")
    monkeypatch.setattr(analyze, "SYNC", tmp_path / "sync.json")
    records = [{"id": "r1", "text": FIX.read_text()}]
    monkeypatch.setattr(analyze.liftosaur, "paginate", lambda *a, **k: records)

    analyze.cmd_pull(type("A", (), {"full": True})())

    lines = [json.loads(l) for l in (tmp_path / "workouts.jsonl").read_text().splitlines() if l.strip()]
    assert len(lines) == 2
    assert {w["id"] for w in lines} == {"r1"}
    assert lines[0]["start_time"] < lines[1]["start_time"]
    assert json.loads((tmp_path / "sync.json").read_text())["last_pull"]


def test_incremental_pull_asks_for_only_new_workouts(tmp_path, monkeypatch):
    monkeypatch.setattr(analyze, "DATA", tmp_path)
    monkeypatch.setattr(analyze, "WORKOUTS", tmp_path / "workouts.jsonl")
    monkeypatch.setattr(analyze, "SYNC", tmp_path / "sync.json")
    (tmp_path / "workouts.jsonl").write_text(
        json.dumps({"id": "old", "start_time": "2026-09-01T09:00:00Z", "exercises": []}) + "\n")
    seen = {}

    def fake_paginate(path, key, params=None, **kw):
        seen.update(params or {})
        return []

    monkeypatch.setattr(analyze.liftosaur, "paginate", fake_paginate)
    analyze.cmd_pull(type("A", (), {"full": False})())
    assert seen["startDate"] == "2026-09-01"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_analyze.py -v`
Expected: failures — `analyze` still imports `hevy`, `build_report` still takes a catalog keyed by template id, and `cmd_pull` still calls Hevy's page-number pagination.

- [ ] **Step 4: Replace the imports and the muscle source in `scripts/analyze.py`**

Delete the `hevy` import and `load_catalog`. Add:

```python
import liftosaur
from liftohistory import parse_liftohistory
from fetch_reference import muscle_for

MUSCLE_MAP = pathlib.Path(__file__).resolve().parent.parent / "references" / "exercise-muscles.json"


def load_muscles() -> dict[str, str]:
    if not MUSCLE_MAP.exists():
        sys.exit(f"No muscle map at {MUSCLE_MAP}. Run: scripts/fetch_reference.py")
    return json.loads(MUSCLE_MAP.read_text())
```

- [ ] **Step 5: Replace `cmd_pull`**

Liftosaur paginates by cursor with `limit` up to 200, so a full pull is a handful of calls rather than one per ten workouts. Incremental pulls pass `startDate`.

```python
def cmd_pull(a):
    params = {}
    existing = [] if a.full else load_workouts()
    if existing:
        params["startDate"] = max(w["start_time"] for w in existing)[:10]
    records = liftosaur.paginate("/history", "records", params=params)

    parsed: list[dict] = []
    for record in records:
        for workout in parse_liftohistory(record.get("text") or ""):
            workout["id"] = record.get("id")
            parsed.append(workout)

    merged = {(w["id"], w["start_time"]): w for w in existing}
    merged.update({(w["id"], w["start_time"]): w for w in parsed})
    save_workouts(list(merged.values()))

    DATA.mkdir(parents=True, exist_ok=True)
    SYNC.write_text(json.dumps({"last_pull": now_iso()}))
    print(f"{len(parsed)} workouts parsed from {len(records)} records; "
          f"{len(merged)} total in {WORKOUTS}")
```

- [ ] **Step 6: Change the muscle lookup in `build_report`**

Replace the signature `build_report(ws, cat)` with `build_report(ws, muscles)`, and replace the lookup line:

```python
    for _, e, _ in working_sets(ws, since=w8):
        mg = muscle_for(e["title"], muscles)
        by_muscle[mg] = by_muscle.get(mg, 0) + 1
        if mg == "unknown":
            vague[e["title"]] = vague.get(e["title"], 0) + 1
```

Update the comment above `unclassified_exercises` — Liftosaur has no catch-all group, so an entry there now means the exercise is genuinely missing from `references/exercise-muscles.json` and the fix is to add it, not to reassign a bucket by hand:

```python
        # An entry here is an exercise with no row in exercise-muscles.json, so
        # its sets are counted under "unknown" and every muscle it trains is
        # undercounted. The fix is to add it to the map, not to adjust the table.
```

Update `cmd_report` and `cmd_deep_dive` to call `load_muscles()` where they called `load_catalog()`.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_analyze.py -v`
Expected: 8 passed.

- [ ] **Step 8: Run the whole suite**

Run: `python3 -m pytest -v`
Expected: 38 passed.

- [ ] **Step 9: Commit**

```bash
git add scripts/analyze.py tests/test_analyze.py tests/fixtures/history_sample.txt
git commit -m "feat: pull and analyse Liftosaur history instead of Hevy workouts"
```

---

### Task 5: Migrate `checkin.py` to Liftosaur measurements

The weight log, the trend verdicts and the check-in record are untouched. Only the importer changes: it reads `GET /measurements/weight` instead of shelling out to `hevy.py measurements`.

**Files:**
- Modify: `scripts/checkin.py` — replace `cmd_import_hevy` with `cmd_import_liftosaur`, rename the subcommand, update the usage docstring
- Create: `tests/test_checkin.py`

**Interfaces:**
- Consumes: `liftosaur.paginate` from Task 2.
- Produces: unchanged `weight-log.csv` (`date,weight_kg,fat_percent,waist_cm`) and unchanged `trend` verdict strings `INSUFFICIENT_DATA`, `WITHIN_NOISE`, `OK`, which Task 7's documentation refers to by name.

- [ ] **Step 1: Write the failing tests**

Write `tests/test_checkin.py`:

```python
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import checkin


def test_import_liftosaur_writes_dates_and_kilograms(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(checkin, "WEIGHTS", tmp_path / "weight-log.csv")
    values = [
        {"timestamp": "2026-10-01T07:00:00Z", "value": "70.5kg"},
        {"timestamp": "2026-10-02T07:00:00Z", "value": "70.2kg"},
    ]
    monkeypatch.setattr(checkin.liftosaur, "paginate", lambda *a, **k: values)

    checkin.cmd_import_liftosaur(type("A", (), {})())

    rows = checkin.load_weights()
    assert [str(d) for d, _ in rows] == ["2026-10-01", "2026-10-02"]
    assert [w for _, w in rows] == [70.5, 70.2]


def test_import_converts_pounds_to_kilograms(tmp_path, monkeypatch):
    monkeypatch.setattr(checkin, "WEIGHTS", tmp_path / "weight-log.csv")
    monkeypatch.setattr(checkin.liftosaur, "paginate", lambda *a, **k: [
        {"timestamp": "2026-10-01T07:00:00Z", "value": "155lb"}])

    checkin.cmd_import_liftosaur(type("A", (), {})())

    _, weight = checkin.load_weights()[0]
    assert weight == pytest.approx(155 * 0.45359237, abs=0.01)


def test_import_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(checkin, "WEIGHTS", tmp_path / "weight-log.csv")
    values = [{"timestamp": "2026-10-01T07:00:00Z", "value": "70.5kg"}]
    monkeypatch.setattr(checkin.liftosaur, "paginate", lambda *a, **k: values)

    checkin.cmd_import_liftosaur(type("A", (), {})())
    checkin.cmd_import_liftosaur(type("A", (), {})())

    assert len(checkin.load_weights()) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_checkin.py -v`
Expected: `AttributeError: module 'checkin' has no attribute 'cmd_import_liftosaur'`.

- [ ] **Step 3: Replace the importer in `scripts/checkin.py`**

Delete `cmd_import_hevy` and its `subprocess` call. Add:

```python
import liftosaur

LB_TO_KG = 0.45359237


def _as_kg(value: str) -> float | None:
    """Measurement values carry their unit as a suffix: '70.5kg', '155lb'."""
    raw = str(value).strip()
    for unit, factor in (("kg", 1.0), ("lb", LB_TO_KG)):
        if raw.endswith(unit):
            try:
                return round(float(raw[: -len(unit)]) * factor, 2)
            except ValueError:
                return None
    try:
        return round(float(raw), 2)   # unit-less: the account's own unit, assume kg
    except ValueError:
        return None


def cmd_import_liftosaur(_):
    values = liftosaur.paginate("/measurements/weight", "values")
    existing = {d for d, _ in load_weights()}
    added = 0
    for entry in values:
        stamp = str(entry.get("timestamp") or "")[:10]
        weight = _as_kg(entry.get("value"))
        if not stamp or weight is None:
            continue
        date = dt.date.fromisoformat(stamp)
        if date in existing:
            continue
        _append_weight(date, weight, None, None)
        existing.add(date)
        added += 1
    print(f"imported {added} new weigh-ins from Liftosaur ({len(values)} values seen)")
```

Factor the row-appending logic out of `cmd_log_weight` into `_append_weight(date, weight_kg, fat_percent, waist_cm)` so both commands share it. Then rename the subcommand:

```python
    sub.add_parser("import-liftosaur").set_defaults(fn=cmd_import_liftosaur)
```

Update the module docstring's usage line from `checkin.py import-hevy` to `checkin.py import-liftosaur  # pull weigh-ins from Liftosaur into the log`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_checkin.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/checkin.py tests/test_checkin.py
git commit -m "feat: import weigh-ins from Liftosaur measurements"
```

---

### Task 6: Point the dashboard at Liftosaur programs

`dashboard.py` imports `hevy` and calls `hevy.paginate("/v1/routines", "routines", 10, None)` to show the live plan. Liftosaur has programs, not routines, and a program is one Liftoscript document rather than a list of exercise objects.

**Files:**
- Modify: `scripts/dashboard.py:28` (the import) and `scripts/dashboard.py:249` (the routines call), plus the plan-tab rendering that consumes it
- Modify: `scripts/dashboard_template.html` — the two Hevy references
- Create: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `liftosaur.ok`, `liftosaur.call` from Task 2; the report shape from Task 4.
- Produces: `program_sessions(program_text: str) -> list[dict]` — `[{"week": str, "day": str, "exercises": [str]}]`, used by the plan tabs.

- [ ] **Step 1: Write the failing test**

Write `tests/test_dashboard.py`:

```python
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import dashboard

PROGRAM = """# Week 1
## Upper A
Bench Press / 3x8 80kg / progress: lp(2.5kg)
Lat Pulldown, Cable / 3x10 50kg

## Lower A
Squat / 3x5 100kg / progress: lp(5kg)

# Week 2
## Upper A
Bench Press / 3x8 82.5kg
"""


def test_program_sessions_splits_weeks_and_days():
    sessions = dashboard.program_sessions(PROGRAM)
    assert [(s["week"], s["day"]) for s in sessions] == [
        ("Week 1", "Upper A"), ("Week 1", "Lower A"), ("Week 2", "Upper A")]


def test_program_sessions_lists_exercise_lines_without_the_progress_section():
    sessions = dashboard.program_sessions(PROGRAM)
    assert sessions[0]["exercises"] == ["Bench Press / 3x8 80kg",
                                        "Lat Pulldown, Cable / 3x10 50kg"]


def test_program_sessions_ignores_comments_and_blank_lines():
    assert dashboard.program_sessions("# Week 1\n\n// a note\n## Day\n\nSquat / 1x5 100kg\n") == [
        {"week": "Week 1", "day": "Day", "exercises": ["Squat / 1x5 100kg"]}]


def test_no_hevy_references_remain():
    for name in ("dashboard.py", "dashboard_template.html"):
        text = (pathlib.Path(dashboard.__file__).parent / name).read_text().lower()
        assert "hevy" not in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_dashboard.py -v`
Expected: `AttributeError: module 'dashboard' has no attribute 'program_sessions'`, and the Hevy assertion fails.

- [ ] **Step 3: Replace the import and add the parser**

In `scripts/dashboard.py`, change `import hevy` to `import liftosaur` and add:

```python
def program_sessions(program_text: str) -> list[dict]:
    """Split a Liftoscript program into its weeks and days for the plan tabs.

    '#' prefixes a week, '##' a day, '//' a comment. Everything else is an
    exercise line; the sections after the sets are dropped so the tab stays
    readable at the rack.
    """
    sessions: list[dict] = []
    week = ""
    for raw in program_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("## "):
            sessions.append({"week": week, "day": line[3:].strip(), "exercises": []})
        elif line.startswith("# "):
            week = line[2:].strip()
        elif sessions:
            parts = [p.strip() for p in line.split("/")]
            keep = [parts[0]] + [p for p in parts[1:]
                                 if not any(p.startswith(s) for s in
                                            ("progress:", "update:", "warmup:",
                                             "used:", "id:", "superset:"))]
            sessions[-1]["exercises"].append(" / ".join(p for p in keep if p))
    return sessions
```

- [ ] **Step 4: Replace the live-plan fetch at the old line 249**

```python
        program = liftosaur.ok(*liftosaur.call("GET", "/programs/current"),
                               "GET /programs/current")
        live = program_sessions((program or {}).get("text") or "")
```

Update the plan-tab rendering to read `week`, `day` and `exercises` from each session instead of Hevy routine objects.

- [ ] **Step 5: Remove the Hevy strings from `scripts/dashboard_template.html`**

Replace both occurrences with Liftosaur equivalents — the footer attribution and the empty-state message that tells the user where the plan comes from.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_dashboard.py -v`
Expected: 4 passed.

- [ ] **Step 7: Commit**

```bash
git add scripts/dashboard.py scripts/dashboard_template.html tests/test_dashboard.py
git commit -m "feat: render Liftosaur programs in the dashboard plan tabs"
```

---

### Task 7: Delete Hevy and rewrite the skill documentation

The code now works; the instructions still describe Hevy. `SKILL.md` has 28 Hevy references and `README.md` has 39, and the seven-step workflow itself changes because a Liftoscript program carries its own weeks.

**Files:**
- Delete: `scripts/hevy.py`, `references/hevy-api.md`, `references/hevy-openapi.json`
- Modify: `SKILL.md`, `README.md`, `references/state.md`, `references/programming.md:1` (one Hevy mention), `scripts/paths.py` (the `hevy-key` comment), `.gitignore`
- Create: `tests/test_no_hevy.py`

**Interfaces:**
- Consumes: every command signature produced by Tasks 2 through 6.
- Produces: the documented workflow Task 8 verifies.

- [ ] **Step 1: Write the failing test**

Write `tests/test_no_hevy.py`:

```python
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_no_tracked_file_mentions_hevy():
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                           text=True, check=True).stdout.split()
    offenders = {}
    for name in files:
        if name.startswith("docs/superpowers/"):
            continue          # the spec and this plan describe the migration itself
        path = ROOT / name
        if not path.is_file():
            continue
        hits = [i for i, line in enumerate(path.read_text(errors="ignore").splitlines(), 1)
                if "hevy" in line.lower()]
        if hits:
            offenders[name] = hits[:5]
    assert offenders == {}, f"Hevy references remain: {offenders}"


def test_hevy_files_are_gone():
    for name in ("scripts/hevy.py", "references/hevy-api.md", "references/hevy-openapi.json"):
        assert not (ROOT / name).exists(), f"{name} still present"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_no_hevy.py -v`
Expected: both fail, and the first prints every remaining file and line.

- [ ] **Step 3: Delete the Hevy files**

```bash
git rm scripts/hevy.py references/hevy-api.md references/hevy-openapi.json
```

- [ ] **Step 4: Rewrite the quick-reference table in `SKILL.md`**

| Need | Command |
|---|---|
| Validate key | `scripts/liftosaur.py ping` |
| Match an exercise name | `scripts/liftosaur.py exercises "lat pulldown"` |
| Full history pull | `scripts/analyze.py pull --full` |
| Incremental pull | `scripts/analyze.py pull` |
| Baseline tables | `scripts/analyze.py report` |
| One lift in depth | `scripts/analyze.py deep-dive "squat"` |
| Check a program's volume | `scripts/liftosaur.py program-stats plan.liftoscript` |
| Simulate a program | `scripts/liftosaur.py playground plan.liftoscript --week 1 --day 1` |
| Create a program | `scripts/liftosaur.py program-push plan.liftoscript --name "Meso 1"` |
| Update a program | `scripts/liftosaur.py program-push plan.liftoscript --id <id>` |
| Fetch a program to edit | `scripts/liftosaur.py program-get current --out plan.liftoscript` |
| Log a weigh-in | `scripts/checkin.py log-weight 2026-10-11 70.3` |
| Import weigh-ins | `scripts/checkin.py import-liftosaur` |
| Trend and observed TDEE | `scripts/checkin.py trend --calories 1705` |
| Record the check-in | `scripts/checkin.py record ...` |
| Dashboard | `scripts/dashboard.py build --open` |
| Refresh vendored references | `scripts/fetch_reference.py` |

- [ ] **Step 5: Rewrite STEP 1 in `SKILL.md`**

Replace the Hevy pull block with:

```bash
scripts/liftosaur.py ping && scripts/analyze.py pull --full
scripts/analyze.py report
```

Delete the sentence about `pageSize` capping at 10 and a pull taking a minute or two — Liftosaur pages 200 records at a time, so a full pull is a handful of calls. Replace the paragraph about reading `unclassified_exercises` because Hevy files machine work under a catch-all: under Liftosaur an entry in that list means the exercise has no row in `references/exercise-muscles.json`, and the fix is to add it to the map and re-run, not to reassign a bucket by hand.

- [ ] **Step 6: Rewrite STEP 4 and STEP 5 in `SKILL.md`**

This is the substantive change, not a find-and-replace. The old STEP 4 said "one routine per distinct session, not one per calendar day" and "Hevy routines are not dated, so the week lives in the title and the exercise notes." That constraint is gone. Write instead:

- The block is **one Liftoscript program** with `# Week N` and `## Day Name` headings covering every week, deload weeks included.
- Progression is written into the program with `progress:`, not left for the coach to apply by hand. `lp(2.5kg)` for linear progression, `dp(2.5kg, 8, 12)` for double progression on a rep range, `sum(30, 2.5kg)` for total-rep thresholds, `custom(...) {~ ~}` for anything else, `none` to disable it for a day or week.
- RIR and cues go in exercise notes as before, but the week-to-week set ramp is expressed in the program text itself rather than in a note telling the client to add a set.
- Loads still come from the client's own logged data and still round to something loadable.

Replace STEP 5's template-id mapping ritual — Liftosaur has no template ids — with:

1. **Check every exercise name** against the built-ins: `scripts/liftosaur.py exercises "<pattern>"`. Liftosaur names carry equipment as a suffix (`Bicep Curl, Cable`). No match means say so and propose a custom exercise created in the app.
2. **Verify the volume before pushing:** `scripts/liftosaur.py program-stats plan.liftoscript`. It returns per-day content and weekly set totals per muscle group, computed by Liftosaur. Compare those against the volume landmarks in `references/programming.md` and report the real numbers.
3. **Simulate it:** `scripts/liftosaur.py playground plan.liftoscript --week 1 --day 1`. A program that will not resolve is a program the client cannot run.
4. **Push it:** `scripts/liftosaur.py program-push plan.liftoscript --name "<name>"`, and report the returned id.
5. **Verify:** `scripts/liftosaur.py programs` and confirm it is there. Then: "Open Liftosaur and the program is in your list."

- [ ] **Step 7: Rewrite STEP 7's `PROGRESS IT` row**

The Hevy workflow was `pull-routine`, hand-edit loads, `push-routine --id`, because a PUT replaced the whole exercise list and `index` was read-only. Liftosaur has none of those traps. Replace with: `scripts/liftosaur.py program-get current --out plan.liftoscript`, edit the text, run `program-stats` and `playground` to check it, show before and after, get a yes, then `program-push plan.liftoscript --id <id>`.

Delete the "PUT replaces the entire exercise list" warning and the `400 Unrecognized key(s) in object: 'index'` note entirely — both are Hevy facts.

- [ ] **Step 8: Update the red flags and rationalizations in `SKILL.md`**

Drop these rows, which no longer describe anything real: the `<ID:...>` placeholder flag, "hand-build a PUT body instead of using pull-routine", "I don't have a Hevy connector", "I'll use a placeholder id", and "GET the routine, change the load, PUT it back".

Add:

| Excuse | Reality |
|---|---|
| "I'll count the weekly sets myself" | `program-stats` counts them server-side. Use it and report its numbers. |
| "The Liftoscript looks right to me" | Then `playground` will confirm it. A program that will not resolve is a program they cannot run. |
| "I'll write the progression in the notes" | Liftosaur runs `progress:` for them. A note is a plan nobody applies. |

- [ ] **Step 9: Update `references/state.md`**

Rename `hevy-key` to `liftosaur-key` in the layout tree and the API-key section. Delete `exercise-templates.jsonl` — there is no catalog to cache. In the `profile.json` schema, replace the `"hevy"` object with:

```jsonc
  "liftosaur": { "connected": true, "key_source": "env", "program_id": "..." },
```

Change the plans directory description: a block is now `plans/meso-01.md` plus `plans/meso-01.liftoscript`, the exact program text pushed, instead of a directory of per-session JSON. Change the key-handling paragraph to read `$LIFTOSAUR_API_KEY` and `<state>/liftosaur-key`, and note that the REST API requires a premium subscription.

- [ ] **Step 10: Update `README.md`, `scripts/paths.py` and `.gitignore`**

Rewrite `README.md`'s setup and command sections against the new table from Step 4, and state the premium-subscription requirement in the prerequisites. In `scripts/paths.py`, change the `_warn_if_unignored` docstring from "it can hold the hevy-key" to "it can hold the liftosaur-key". Add `liftosaur-key` to `.gitignore` if the old `hevy-key` was listed there.

- [ ] **Step 11: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_no_hevy.py -v`
Expected: 2 passed. The first test names any file still mentioning Hevy, so iterate until it is clean.

- [ ] **Step 12: Run the whole suite**

Run: `python3 -m pytest -v`
Expected: 47 passed.

- [ ] **Step 13: Commit**

```bash
git add -A
git commit -m "feat!: replace Hevy with Liftosaur across the skill and its docs"
```

---

### Task 8: Verify against the live account

Every previous task was verified against fixtures and fakes. This one needs the user's real key and their imported history, and it is the only task that can confirm the migration actually works.

**Prerequisite:** the user has imported their Hevy history into Liftosaur, and `$LIFTOSAUR_API_KEY` is set (or `<state>/liftosaur-key` exists, `chmod 600`).

**Files:**
- Create: `docs/superpowers/plans/2026-10-11-liftosaur-migration-verification.md` — the recorded results

**Interfaces:**
- Consumes: every command from Tasks 2 through 7.
- Produces: a written verification record, and a list of any exercise names needing a muscle-map entry.

- [ ] **Step 1: Confirm auth and units**

Run: `scripts/liftosaur.py ping`
Expected: a JSON settings object. Record the `units` value — if it is `lb`, confirm that reported weights are still kilograms, because the parser converts on read.

If this returns `HTTP 401`, stop: the key is wrong or the subscription is inactive. Do not continue.

- [ ] **Step 2: Pull the full history and check the count**

Run: `scripts/analyze.py pull --full`
Expected: a workout count close to the 334 records that were in Hevy. Record the actual number.

A count far below 334 means the user's import did not bring everything across — report the number and stop rather than producing a report over partial data.

- [ ] **Step 3: Check for unmapped exercises**

Run: `scripts/analyze.py report`
Expected: the same six tables as before. **Read `unclassified_exercises` first.** Every name there needs a row in `references/exercise-muscles.json`; add them, re-run, and confirm the list is empty before trusting the balance table.

- [ ] **Step 4: Confirm whether RPE survived the import**

In the report's RPE section, expect either an average top-set RPE across a few hundred sets, or `"client does not log RPE"`.

The Hevy baseline was **8.6 across 194 top sets**. If the RPE section is now empty, the user's import dropped the `@` annotations. Say so plainly — it is a real loss of data, the report degrades honestly rather than inventing numbers, and future sessions will rebuild it.

- [ ] **Step 5: Import the weigh-ins**

Run: `scripts/checkin.py import-liftosaur`
Then: `scripts/checkin.py trend --calories 1705 --window 30`
Expected: a verdict string. The Hevy baseline on 2026-09-18 was `WITHIN_NOISE` over 30 days with 50 imported weigh-ins; the number should be comparable plus whatever has been logged since.

- [ ] **Step 6: Round-trip the live program**

```bash
scripts/liftosaur.py programs
scripts/liftosaur.py program-get current --out /tmp/current.liftoscript
scripts/liftosaur.py program-stats /tmp/current.liftoscript
scripts/liftosaur.py playground /tmp/current.liftoscript --week 1 --day 1
```

Expected: a program list with ids, the program text on disk, per-muscle weekly set totals, and a resolved workout. **Do not push anything in this step** — reading and verifying is the whole test.

- [ ] **Step 7: Build the dashboard**

Run: `scripts/dashboard.py build --name "Kieffer" --goal "Fat loss to sub-68 kg" --open`
Expected: it builds, the plan tabs show the Liftoscript program's weeks and days, and the photo match rate prints. Confirm the output contains no API key:

```bash
grep -ric "lftsk_" ~/.kg-trainer/dashboard/ || echo "clean: no key in dashboard output"
```

- [ ] **Step 8: Write the verification record**

Record, in `docs/superpowers/plans/2026-10-11-liftosaur-migration-verification.md`: the account units, the workout count pulled against the 334 expected, any exercises added to the muscle map, whether RPE survived, the weigh-in count and trend verdict, the live program id, and the dashboard photo match rate. Note anything that did not match the Hevy baseline.

- [ ] **Step 9: Commit**

```bash
git add docs/superpowers/plans/2026-10-11-liftosaur-migration-verification.md references/exercise-muscles.json
git commit -m "test: record live Liftosaur verification results"
```

---

## Self-Review

**Spec coverage.** Every functional requirement maps to a task: the client and its subcommands to Task 2; history pull and the report to Tasks 1 and 4; program read, write, `program-stats` and `playground` to Tasks 2 and 7; `import-liftosaur` to Task 5; the dashboard to Task 6; push verification to Task 7 Step 6 and Task 8 Step 6. The non-functional key-handling rule is enforced by a test in Task 2 and a grep in Task 8 Step 7. The muscle-group gap the spec identifies is Task 3. The three named risks are each addressed: the parser by Task 1's fifteen tests plus Task 8 Step 2, RPE loss by Task 8 Step 4, unit mismatch by Task 8 Step 1. The four decisions are honoured — REST not MCP, hard replace in Task 7, no backfill tool anywhere.

**Placeholder scan.** No TBDs, no "add error handling", no "similar to Task N". Every code step carries the actual code; every doc step in Task 7 names the specific text to change and what to replace it with rather than saying "update the docs". The one judgement call left to the implementer is Task 3 Step 3, hand-mapping unmatched exercises, which is irreducibly manual and is gated by a test that fails until it is done.

**Type consistency.** `parse_liftohistory` returns the record shape declared in Task 1's Interfaces and consumed unchanged in Task 4. `paginate(path, key, params=None, limit=200)` has one signature, used identically in Tasks 4 and 5. `muscle_for(title, table)` is defined in Task 3 and called in Task 4 Step 6. `build_report` changes its second parameter from `cat` to `muscles` in Task 4 and Task 6 consumes only its report keys, which do not change. `program_sessions` is defined and consumed within Task 6. `LB_TO_KG` is defined in `liftohistory.py` and redeclared in `checkin.py` — a deliberate duplication of one constant to avoid `checkin.py` importing the parser for a single float.

**One known gap, stated rather than hidden.** Task 3's `build_muscle_map` seeds from `free-exercise-db.json`, whose exercise names come from a different project than Liftosaur's, so the match rate is unknown until Step 2 runs. The design handles this by printing every unmatched name and gating the commit behind a test, so a poor match rate costs manual effort in Task 3 Step 3 — it does not produce a silently wrong balance table.
