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
