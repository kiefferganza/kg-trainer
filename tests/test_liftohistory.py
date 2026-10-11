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


def _sets_of(body):
    text = '2026-03-01T08:00:00Z / exercises: {\n  Squat / ' + body + '\n}\n'
    return parse_liftohistory(text)[0]["exercises"][0]["sets"]


def test_unterminated_record_does_not_absorb_the_next_one():
    text = ('2026-03-01T08:00:00Z / exercises: {\n'
            '  Squat / 3x5 100kg\n'
            '2026-03-02T08:00:00Z / exercises: {\n'
            '  Deadlift / 1x5 150kg\n'
            '}\n')
    records = parse_liftohistory(text)
    assert len(records) == 2
    assert [e["title"] for e in records[0]["exercises"]] == ["Squat"]
    assert records[1]["start_time"] == "2026-03-02T08:00:00Z"
    assert [e["title"] for e in records[1]["exercises"]] == ["Deadlift"]


def test_one_line_empty_exercises_record_is_closed():
    text = ('2026-03-01T08:00:00Z / exercises: {}\n'
            '2026-03-02T08:00:00Z / exercises: {\n'
            '  Deadlift / 1x5 150kg\n'
            '}\n')
    records = parse_liftohistory(text)
    assert len(records) == 2
    assert records[0]["exercises"] == []
    assert len(records[1]["exercises"]) == 1


def test_comment_only_block_is_skipped():
    assert parse_liftohistory("// trailing note\n") == []
    text = REF + "\n// dangling note\n"
    assert len(parse_liftohistory(text)) == 1


def test_malformed_set_token_yields_no_sets():
    assert _sets_of("garbage") == []


def test_weight_without_unit_is_not_guessed():
    assert _sets_of("3x5 150") == []


def test_pipe_in_unexpected_position_yields_no_sets():
    assert _sets_of("3x|7 100kg") == []
    assert _sets_of("|3x5 100kg") == []


def test_rep_range_where_set_count_expected_yields_no_sets():
    assert _sets_of("8-12x3 100kg") == []


def test_zero_count_yields_no_sets():
    assert _sets_of("0x5 100kg") == []


def test_group_without_scheme_keeps_its_weight():
    sets = _sets_of("185lb @8")
    assert len(sets) == 1
    assert sets[0]["weight_kg"] == pytest.approx(185 * LB_TO_KG, abs=0.01)
    assert sets[0]["rpe"] == 8.0
