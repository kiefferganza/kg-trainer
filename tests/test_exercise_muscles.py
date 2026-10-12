import io
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import fetch_reference
from fetch_reference import build_muscle_map, mcp_tool, muscle_for

# Pinned literally on purpose: importing the script's own tuple would let anyone
# defeat this gate by adding 'unknown' to it.
MUSCLES = ("chest shoulders triceps biceps lats upper_back traps lower_back "
           "abdominals quadriceps hamstrings glutes calves adductors forearms").split()

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


def test_script_vocabulary_matches_the_pinned_list():
    assert list(fetch_reference.MUSCLES) == MUSCLES


FEDB = [{"name": "Barbell Curl", "primaryMuscles": ["biceps"]}]


def test_rerun_preserves_hand_mapped_entries():
    out = build_muscle_map(["Bicep Curl, Cable", "Hip Thrust"], FEDB,
                           {"Hip Thrust": "glutes", "Bicep Curl, Cable": "forearms"})
    assert out == {"Hip Thrust": "glutes", "Bicep Curl, Cable": "forearms"}


def test_unmatched_new_exercise_is_omitted_not_stored_as_unknown():
    out = build_muscle_map(["Barbell Curl", "Brand New Lift"], FEDB, {})
    assert out == {"Barbell Curl": "biceps"}
    assert "unknown" not in out.values()


def _respond(monkeypatch, body: bytes):
    monkeypatch.setattr(fetch_reference.urllib.request, "urlopen",
                        lambda *a, **k: io.BytesIO(body))


def _rpc(result):
    return json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode()


def test_mcp_tool_returns_text(monkeypatch):
    _respond(monkeypatch, _rpc({"content": [{"type": "text", "text": "hello"}]}))
    assert mcp_tool("t") == "hello"


@pytest.mark.parametrize("body", [
    _rpc({"isError": True, "content": [{"type": "text", "text": "boom"}]}),
    _rpc({"content": [{"type": "text", "text": ""}]}),
    _rpc({"content": []}),
    _rpc({}),
    b"event: message\ndata: {}\n\n",
])
def test_mcp_tool_refuses_bad_responses(monkeypatch, body):
    _respond(monkeypatch, body)
    with pytest.raises(SystemExit):
        mcp_tool("t")
