"""Byte-identical guard for a Liftosaur store.

Drives analyze.py pull / report / deep-dive and the weigh-in import against a
fixture store with only the HTTP layer faked, and compares every output with
golden files captured before Liftosaur moved behind the Adapter Contract.
Regenerate (deliberately) with KG_TRAINER_REGEN_GOLDEN=1.
"""

import argparse
import json
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import analyze
import checkin
from adapters import contract
from adapters import http as apphttp
from contract_fixtures.liftosaur import PAGES, WEIGHTS  # noqa: E402

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"
GOLDEN = FIX / "golden"

def fake_request(app, base, method, path, params=None, body=None, **kw):
    assert app == "liftosaur" and base.startswith("https://www.liftosaur.com/")
    if path == "/history":
        i = 1 if (params or {}).get("cursor") else 0
        records, more, cursor = PAGES[i]
        data = {"records": records, "hasMore": more}
        if more:
            data["nextCursor"] = cursor
        return 200, {"data": data}
    if path == "/measurements/weight":
        return 200, {"data": {"values": WEIGHTS, "hasMore": False}}
    if path == "/settings":
        return 200, {"data": {"units": "kg"}}
    raise AssertionError(f"unexpected request {method} {path}")


@pytest.fixture
def store(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(analyze, "DATA", data)
    monkeypatch.setattr(analyze, "WORKOUTS", data / "workouts.jsonl")
    monkeypatch.setattr(analyze, "SYNC", data / "sync.json")
    monkeypatch.setattr(analyze, "MEASUREMENTS", data / "measurements.json")
    monkeypatch.setattr(analyze, "PERSONAL_MUSCLE_MAP", tmp_path / "no-personal-map.json")
    monkeypatch.setattr(checkin, "HOME", tmp_path)
    monkeypatch.setattr(checkin, "WEIGHTS", tmp_path / "weight-log.csv")
    # An existing Liftosaur store: the old profile block, no `app` yet.
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"name": "K", "liftosaur": {
        "connected": True, "key_source": "env", "program_id": "p1"}}))
    monkeypatch.setattr(contract, "profile_path", lambda: profile)
    monkeypatch.setattr(apphttp, "request", fake_request)
    return tmp_path


def check(name, text, store=None):
    if store is not None:
        text = text.replace(str(store), "<STORE>")
    path = GOLDEN / name
    if os.environ.get("KG_TRAINER_REGEN_GOLDEN"):
        GOLDEN.mkdir(exist_ok=True)
        path.write_text(text)
    assert text == path.read_text(), f"{name} differs from the golden file"


def test_pull_report_deep_dive_and_weigh_in_import_are_byte_identical(store, capsys):
    analyze.cmd_pull(argparse.Namespace(full=True))
    pull_out = capsys.readouterr().out.replace(str(store), "<STORE>")
    check("pull.out", pull_out)  # summary line reworded for the contract; see the git history
    check("workouts.jsonl", (store / "data" / "workouts.jsonl").read_text())
    check("measurements.json", (store / "data" / "measurements.json").read_text())

    analyze.cmd_report(argparse.Namespace(json=False))
    check("report.txt", capsys.readouterr().out, store)
    analyze.cmd_report(argparse.Namespace(json=True))
    check("report.json", capsys.readouterr().out)
    analyze.cmd_deep_dive(argparse.Namespace(pattern="bench"))
    check("deep-dive.txt", capsys.readouterr().out)

    (store / "weight-log.csv").write_text(
        "date,weight_kg,fat_percent,waist_cm\n2026-10-12,71.0,,\n")  # hand-logged day
    importer = getattr(checkin, "cmd_import_app", None) or checkin.cmd_import_liftosaur
    importer(argparse.Namespace())
    capsys.readouterr()
    check("weight-log.csv", (store / "weight-log.csv").read_text())
