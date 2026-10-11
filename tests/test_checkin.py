import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import checkin


@pytest.fixture
def log(tmp_path, monkeypatch):
    monkeypatch.setattr(checkin, "HOME", tmp_path)
    monkeypatch.setattr(checkin, "WEIGHTS", tmp_path / "weight-log.csv")
    return tmp_path / "weight-log.csv"


def _serve(monkeypatch, values):
    monkeypatch.setattr(checkin.liftosaur, "paginate", lambda *a, **k: values)


# The live shape: timestamp is epoch milliseconds, the date is ISO UTC, and the
# value carries its unit as a suffix. Newest first, as the API returns them.
def test_import_liftosaur_writes_dates_and_kilograms(log, monkeypatch):
    _serve(monkeypatch, [
        {"timestamp": 1791450000000, "date": "2026-10-02T07:00:00.000Z", "value": "70.2kg"},
        {"timestamp": 1791360000000, "date": "2026-10-01T07:00:00.000Z", "value": "70.5kg"},
    ])

    checkin.cmd_import_liftosaur(type("A", (), {})())

    rows = checkin.load_weights()
    assert [str(d) for d, _ in rows] == ["2026-10-01", "2026-10-02"]
    assert [w for _, w in rows] == [70.5, 70.2]


def test_import_converts_pounds_to_kilograms(log, monkeypatch):
    _serve(monkeypatch, [{"timestamp": 1, "date": "2026-10-01T07:00:00Z", "value": "155lb"}])

    checkin.cmd_import_liftosaur(type("A", (), {})())

    _, weight = checkin.load_weights()[0]
    assert weight == pytest.approx(155 * 0.45359237, abs=0.01)


def test_import_is_idempotent(log, monkeypatch):
    _serve(monkeypatch, [{"timestamp": 1, "date": "2026-10-01T07:00:00Z", "value": "70.5kg"}])

    checkin.cmd_import_liftosaur(type("A", (), {})())
    checkin.cmd_import_liftosaur(type("A", (), {})())

    assert len(checkin.load_weights()) == 1


def test_import_keeps_the_latest_weigh_in_of_a_day(log, monkeypatch):
    _serve(monkeypatch, [
        {"timestamp": 20, "date": "2026-10-01T20:00:00Z", "value": "70.9kg"},
        {"timestamp": 10, "date": "2026-10-01T07:00:00Z", "value": "70.1kg"},
    ])

    checkin.cmd_import_liftosaur(type("A", (), {})())

    assert checkin.load_weights() == [(checkin.dt.date(2026, 10, 1), 70.9)]


def test_import_skips_unreadable_values(log, monkeypatch, capsys):
    _serve(monkeypatch, [{"timestamp": 1, "date": "2026-10-01T07:00:00Z", "value": "70"}])

    checkin.cmd_import_liftosaur(type("A", (), {})())

    assert checkin.load_weights() == []
    assert "1 unreadable" in capsys.readouterr().out


def test_hand_logged_day_is_not_overwritten(log, monkeypatch):
    log.write_text("date,weight_kg,fat_percent,waist_cm\n2026-10-01,71.0,,80\n")
    _serve(monkeypatch, [{"timestamp": 1, "date": "2026-10-01T07:00:00Z", "value": "70.5kg"}])

    checkin.cmd_import_liftosaur(type("A", (), {})())

    assert checkin.load_weights() == [(checkin.dt.date(2026, 10, 1), 71.0)]
