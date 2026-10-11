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
