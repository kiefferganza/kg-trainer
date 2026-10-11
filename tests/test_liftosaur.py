import argparse
import email.message
import io
import json
import pathlib
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import liftosaur

KEY = "lftsk_supersecret"


def _http_error(code, body: bytes):
    return urllib.error.HTTPError(liftosaur.BASE + "/x", code, "err",
                                  email.message.Message(), io.BytesIO(body))


class _Resp:
    def __init__(self, status, body: bytes):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_base_url_is_the_documented_one():
    assert liftosaur.BASE == "https://www.liftosaur.com/api/v1"


def test_ok_unwraps_the_data_envelope():
    assert liftosaur.ok(200, {"data": {"units": "kg"}}, "GET /settings") == {"units": "kg"}


@pytest.mark.parametrize("code,body", [
    (401, json.dumps({"error": {"code": "unauthorized", "message": "bad key"}}).encode()),
    (503, b""),
])
def test_failed_request_never_leaks_the_key(monkeypatch, capsys, code, body):
    monkeypatch.setenv("LIFTOSAUR_API_KEY", KEY)

    def fake_urlopen(req, timeout=None):
        raise _http_error(code, body)

    monkeypatch.setattr(liftosaur._OPENER, "open", fake_urlopen)
    with pytest.raises(SystemExit) as exc:
        liftosaur.ok(*liftosaur.call("GET", "/settings"), "GET /settings")
    message = str(exc.value) + capsys.readouterr().err
    assert f"HTTP {code}" in message
    assert KEY not in message


def test_network_error_names_the_exception_and_never_leaks_the_key(monkeypatch):
    monkeypatch.setenv("LIFTOSAUR_API_KEY", KEY)

    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError(f"no route, token={KEY}")

    monkeypatch.setattr(liftosaur._OPENER, "open", fake_urlopen)
    with pytest.raises(SystemExit) as exc:
        liftosaur.call("GET", "/settings")
    assert "URLError" in str(exc.value)
    assert KEY not in str(exc.value)


def test_timeout_names_the_exception(monkeypatch):
    monkeypatch.setenv("LIFTOSAUR_API_KEY", KEY)

    def fake_urlopen(req, timeout=None):
        raise TimeoutError("timed out")

    monkeypatch.setattr(liftosaur._OPENER, "open", fake_urlopen)
    with pytest.raises(SystemExit) as exc:
        liftosaur.call("GET", "/settings")
    assert "TimeoutError" in str(exc.value)


def test_non_json_success_body_exits_naming_the_exception(monkeypatch):
    monkeypatch.setenv("LIFTOSAUR_API_KEY", KEY)
    monkeypatch.setattr(liftosaur._OPENER, "open",
                        lambda req, timeout=None: _Resp(200, b"<html>captive portal</html>"))
    with pytest.raises(SystemExit) as exc:
        liftosaur.call("GET", "/settings")
    assert "JSONDecodeError" in str(exc.value)
    assert "200" in str(exc.value)


def test_non_json_error_body_reports_the_status(monkeypatch):
    monkeypatch.setenv("LIFTOSAUR_API_KEY", KEY)

    def fake_urlopen(req, timeout=None):
        raise _http_error(500, b"<html>oops</html>")

    monkeypatch.setattr(liftosaur._OPENER, "open", fake_urlopen)
    assert liftosaur.call("GET", "/settings") == (500, None)
    with pytest.raises(SystemExit) as exc:
        liftosaur.ok(500, None, "GET /settings")
    assert "HTTP 500" in str(exc.value)


def test_422_hint_points_at_liftoscript():
    with pytest.raises(SystemExit) as exc:
        liftosaur.ok(422, {"error": {"code": "invalid", "message": "bad line 3"}},
                     "POST /program-stats")
    assert "HTTP 422" in str(exc.value)
    assert "Liftoscript" in str(exc.value)


def test_refused_redirect_with_html_body_says_so_without_the_url(monkeypatch):
    monkeypatch.setenv("LIFTOSAUR_API_KEY", KEY)

    def fake_urlopen(req, timeout=None):
        raise _http_error(302, b"<html>moved</html>")

    monkeypatch.setattr(liftosaur._OPENER, "open", fake_urlopen)
    assert liftosaur.call("GET", "/settings") == (302, None)
    with pytest.raises(SystemExit) as exc:
        liftosaur.ok(302, None, "GET /settings")
    message = str(exc.value)
    assert "HTTP 302" in message
    assert "redirect not followed" in message
    assert "not JSON" not in message
    assert "://" not in message
    assert KEY not in message


@pytest.mark.parametrize("missing", ["id", "name"])
def test_program_push_exits_when_response_lacks_a_field(monkeypatch, tmp_path, missing):
    f = tmp_path / "program.txt"
    f.write_text("# Week 1")
    fields = {"id": "p9", "name": "M1"}
    del fields[missing]
    monkeypatch.setattr(liftosaur, "call", lambda *a, **k: (200, {"data": fields}))
    with pytest.raises(SystemExit) as exc:
        liftosaur.cmd_program_push(argparse.Namespace(file=str(f), name="M1", id=None))
    assert f"no '{missing}'" in str(exc.value)


def test_importing_does_not_change_urllibs_global_opener():
    installed = urllib.request._opener
    assert installed is None or not any(
        isinstance(h, liftosaur._NoRedirect) for h in installed.handlers)


def test_redirects_are_refused_so_the_key_is_not_forwarded():
    handler = liftosaur._NoRedirect()
    req = urllib.request.Request(liftosaur.BASE + "/settings")
    assert handler.redirect_request(req, None, 302, "Found", {},
                                    "https://evil.example/steal") is None


def test_redirect_is_not_followed_end_to_end(monkeypatch):
    monkeypatch.setenv("LIFTOSAUR_API_KEY", KEY)
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            hits.append((self.path, self.headers.get("Authorization")))
            if self.path == "/api/v1/settings":
                self.send_response(302)
                self.send_header("Location", "/elsewhere")
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"data": {}}')

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(liftosaur, "BASE", f"http://127.0.0.1:{server.server_port}/api/v1")
        status, _ = liftosaur.call("GET", "/settings")
    finally:
        server.shutdown()
        server.server_close()
    assert status == 302
    assert [p for p, _ in hits] == ["/api/v1/settings"]
    assert hits[0][1] == f"Bearer {KEY}"


def test_paginate_stops_when_the_cursor_repeats(monkeypatch):
    def fake_call(method, path, params=None, body=None):
        return 200, {"data": {"records": [{"id": "a"}], "hasMore": True, "nextCursor": "same"}}

    monkeypatch.setattr(liftosaur, "call", fake_call)
    with pytest.raises(SystemExit) as exc:
        liftosaur.paginate("/history", "records")
    assert "repeated a cursor" in str(exc.value)


def test_paginate_rejects_a_payload_without_a_data_object(monkeypatch):
    monkeypatch.setattr(liftosaur, "call", lambda *a, **k: (200, {"data": None}))
    with pytest.raises(SystemExit) as exc:
        liftosaur.paginate("/history", "records")
    assert "no data object" in str(exc.value)


def test_measurement_key_is_quoted_into_the_path(monkeypatch):
    captured = {}

    def fake_call(method, path, params=None, body=None):
        captured["path"] = path
        return 200, {"data": {"values": []}}

    monkeypatch.setattr(liftosaur, "call", fake_call)
    liftosaur.cmd_measurement(argparse.Namespace(key="../x?y"))
    assert captured["path"] == "/measurements/..%2Fx%3Fy"


def test_program_get_exits_when_text_is_missing(monkeypatch):
    monkeypatch.setattr(liftosaur, "call", lambda *a, **k: (200, {"data": {"name": "M1"}}))
    with pytest.raises(SystemExit) as exc:
        liftosaur.cmd_program_get(argparse.Namespace(id="p1", out=None))
    assert "no 'text'" in str(exc.value)


def _push(monkeypatch, tmp_path, **kwargs):
    f = tmp_path / "program.txt"
    f.write_text("# Week 1")
    calls = []

    def fake_call(method, path, params=None, body=None):
        calls.append((method, path, body))
        return 200, {"data": {"id": "p9", "name": "M1"}}

    monkeypatch.setattr(liftosaur, "call", fake_call)
    liftosaur.cmd_program_push(argparse.Namespace(file=str(f), name=kwargs.get("name"),
                                                  id=kwargs.get("id")))
    return calls


def test_program_push_with_id_uses_put(monkeypatch, tmp_path, capsys):
    calls = _push(monkeypatch, tmp_path, id="p9", name="M1")
    assert calls[0][0] == "PUT"
    assert calls[0][1] == "/programs/p9"
    assert calls[0][2] == {"text": "# Week 1", "name": "M1"}


def test_program_push_without_id_uses_post(monkeypatch, tmp_path):
    calls = _push(monkeypatch, tmp_path, name="M1")
    assert calls[0][0] == "POST"
    assert calls[0][1] == "/programs"


def test_program_push_without_id_or_name_exits(monkeypatch, tmp_path):
    with pytest.raises(SystemExit) as exc:
        _push(monkeypatch, tmp_path)
    assert "--name is required" in str(exc.value)


def test_empty_key_file_falls_through_to_the_no_key_exit(monkeypatch, tmp_path):
    monkeypatch.delenv("LIFTOSAUR_API_KEY", raising=False)
    (tmp_path / "liftosaur-key").write_text("  \n")
    monkeypatch.setattr(liftosaur.paths, "state_dir", lambda: tmp_path)
    with pytest.raises(SystemExit) as exc:
        liftosaur.api_key()
    assert "No Liftosaur API key" in str(exc.value)


def test_ok_error_message_has_status_and_server_detail_only(capsys):
    payload = {"error": {"code": "unauthorized", "message": "bad key"}}
    with pytest.raises(SystemExit) as exc:
        liftosaur.ok(401, payload, "GET /settings")
    assert str(exc.value) == "GET /settings failed: HTTP 401 unauthorized bad key  (check the key, and that the subscription is active)"


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


def test_paginate_follows_a_numeric_cursor(monkeypatch):
    # The live API returns nextCursor as a millisecond timestamp, not a string.
    pages = [
        (200, {"data": {"records": [{"id": "a"}], "hasMore": True,
                        "nextCursor": 1791752524694}}),
        (200, {"data": {"records": [{"id": "b"}], "hasMore": False}}),
    ]
    seen = []

    def fake_call(method, path, params=None, body=None):
        seen.append(dict(params or {}))
        return pages[len(seen) - 1]

    monkeypatch.setattr(liftosaur, "call", fake_call)
    out = liftosaur.paginate("/history", "records")
    assert [r["id"] for r in out] == ["a", "b"]
    assert seen[1]["cursor"] == 1791752524694


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

    monkeypatch.setattr(liftosaur._OPENER, "open", fake_urlopen)
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

    monkeypatch.setattr(liftosaur._OPENER, "open", fake_urlopen)
    liftosaur.call("GET", "/history", params={"limit": 200, "startDate": "2026-09-01"})
    assert "limit=200" in captured["url"]
    assert "startDate=2026-09-01" in captured["url"]


# --- .env key resolution ---------------------------------------------------

def test_dotenv_supplies_the_key_when_nothing_is_exported(monkeypatch, tmp_path):
    monkeypatch.delenv("LIFTOSAUR_API_KEY", raising=False)
    (tmp_path / ".env").write_text("LIFTOSAUR_API_KEY=lftsk_fromdotenv\n")
    monkeypatch.chdir(tmp_path)
    assert liftosaur.api_key() == "lftsk_fromdotenv"


def test_exported_key_wins_over_dotenv(monkeypatch, tmp_path):
    monkeypatch.setenv("LIFTOSAUR_API_KEY", "lftsk_exported")
    (tmp_path / ".env").write_text("LIFTOSAUR_API_KEY=lftsk_fromdotenv\n")
    monkeypatch.chdir(tmp_path)
    assert liftosaur.api_key() == "lftsk_exported"


def test_dotenv_is_found_from_a_nested_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("LIFTOSAUR_API_KEY", raising=False)
    (tmp_path / ".env").write_text("LIFTOSAUR_API_KEY=lftsk_atroot\n")
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert liftosaur.api_key() == "lftsk_atroot"


def test_walk_continues_past_a_dotenv_that_lacks_the_key(monkeypatch, tmp_path):
    monkeypatch.delenv("LIFTOSAUR_API_KEY", raising=False)
    (tmp_path / ".env").write_text("LIFTOSAUR_API_KEY=lftsk_atroot\n")
    nested = tmp_path / "child"
    nested.mkdir()
    (nested / ".env").write_text("# no key here\nSOMETHING_ELSE=1\n")
    monkeypatch.chdir(nested)
    assert liftosaur.api_key() == "lftsk_atroot"


def test_dotenv_parsing_handles_quotes_comments_and_blanks(monkeypatch, tmp_path):
    monkeypatch.delenv("LIFTOSAUR_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        '\n# a comment\n\n  LIFTOSAUR_API_KEY = "lftsk_quoted"  \nOTHER=x\n')
    monkeypatch.chdir(tmp_path)
    assert liftosaur.api_key() == "lftsk_quoted"


def test_placeholder_only_dotenv_is_treated_as_no_key(monkeypatch, tmp_path):
    """An unfilled .env must not be mistaken for a configured key."""
    monkeypatch.delenv("LIFTOSAUR_API_KEY", raising=False)
    (tmp_path / ".env").write_text("LIFTOSAUR_API_KEY=\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(liftosaur.paths, "state_dir", lambda: tmp_path / "store")
    with pytest.raises(SystemExit) as exc:
        liftosaur.api_key()
    assert "API Keys" in str(exc.value)


def test_a_key_from_dotenv_never_reaches_a_failure_message(monkeypatch, tmp_path):
    monkeypatch.delenv("LIFTOSAUR_API_KEY", raising=False)
    (tmp_path / ".env").write_text("LIFTOSAUR_API_KEY=lftsk_dotenvsecret\n")
    monkeypatch.chdir(tmp_path)

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(liftosaur.BASE + "/settings", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(liftosaur._OPENER, "open", boom)
    with pytest.raises(SystemExit) as exc:
        liftosaur.ok(*liftosaur.call("GET", "/settings"), "GET /settings")
    message = str(exc.value)
    assert "401" in message
    assert "lftsk_dotenvsecret" not in message


def test_the_suite_never_sees_a_real_dotenv_up_the_tree():
    """Guards conftest's cwd isolation.

    api_key() walks parents looking for .env. If a test ever runs with a real
    checkout above it, the suite reads the developer's live key -- which once
    broke three key-resolution tests here and should never happen again.
    """
    start = pathlib.Path.cwd().resolve()
    reachable = [d / ".env" for d in (start, *start.parents) if (d / ".env").is_file()]
    assert reachable == [], f"a real .env is reachable from the test cwd: {reachable}"
