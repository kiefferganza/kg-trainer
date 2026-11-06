"""The HTTP and key handling every Adapter shares, exercised with an app that is not Liftosaur."""

import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from adapters import http as apphttp
import paths

KEY = "hvy_supersecret_value"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "state_dir", lambda: tmp_path)
    return tmp_path


def test_env_name_is_derived_from_the_app_name():
    assert apphttp.env_name("hevy") == "HEVY_API_KEY"
    assert apphttp.env_name("my-app") == "MY_APP_API_KEY"


def test_key_precedence_env_then_dotenv_then_state_file(monkeypatch, store, tmp_path):
    (store / "hevy-key").write_text("from_file\n")
    (tmp_path / ".env").write_text("HEVY_API_KEY=from_dotenv\n")
    monkeypatch.setenv("HEVY_API_KEY", "from_env")
    assert apphttp.api_key("hevy") == "from_env"
    monkeypatch.delenv("HEVY_API_KEY")
    assert apphttp.api_key("hevy") == "from_dotenv"
    (tmp_path / ".env").unlink()
    assert apphttp.api_key("hevy") == "from_file"


def test_apps_do_not_see_each_others_keys(monkeypatch, store):
    monkeypatch.setenv("HEVY_API_KEY", "hevy_only")
    monkeypatch.delenv("OTHER_API_KEY", raising=False)
    (store / "hevy-key").write_text("hevy_file")
    with pytest.raises(SystemExit) as exc:
        apphttp.api_key("other")
    assert "No other API key" in str(exc.value)
    assert "$OTHER_API_KEY" in str(exc.value)
    assert "other-key" in str(exc.value)


def test_missing_key_message_has_no_key_value(monkeypatch, store):
    monkeypatch.delenv("HEVY_API_KEY", raising=False)
    with pytest.raises(SystemExit) as exc:
        apphttp.api_key("hevy", display="Hevy", example="hv_...", hint="Pro required.")
    message = str(exc.value)
    assert message.startswith("No Hevy API key.")
    assert "HEVY_API_KEY=hv_..." in message
    assert message.endswith("Pro required.")


def test_redirect_handler_refuses():
    req = urllib.request.Request("https://api.example/x")
    assert apphttp.NoRedirect().redirect_request(
        req, None, 302, "Found", {}, "https://evil.example/steal") is None


def test_redirect_is_not_followed_for_any_app(monkeypatch):
    monkeypatch.setenv("HEVY_API_KEY", KEY)
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            hits.append((self.path, self.headers.get("Authorization")))
            if self.path == "/v1/workouts":
                self.send_response(307)
                self.send_header("Location", "/elsewhere")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"data": {}}')

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        status, _ = apphttp.request("hevy", f"http://127.0.0.1:{server.server_port}/v1",
                                    "GET", "/workouts")
    finally:
        server.shutdown()
        server.server_close()
    assert status == 307
    assert [p for p, _ in hits] == ["/v1/workouts"]
    assert hits[0][1] == f"Bearer {KEY}"


def test_check_names_operation_and_status_but_not_the_key():
    with pytest.raises(SystemExit) as exc:
        apphttp.check(403, {"error": {"code": "forbidden", "message": "nope"}},
                      "GET /workouts")
    assert str(exc.value) == "GET /workouts failed: HTTP 403 forbidden nope"


def test_check_adds_the_callers_hint_and_the_redirect_explanation():
    with pytest.raises(SystemExit) as exc:
        apphttp.check(401, None, "GET /x", {401: "  (check the key)"})
    assert str(exc.value) == "GET /x failed: HTTP 401   (check the key)"
    with pytest.raises(SystemExit) as exc:
        apphttp.check(302, None, "GET /x")
    assert "redirect not followed" in str(exc.value)


def test_check_unwraps_data():
    assert apphttp.check(200, {"data": {"a": 1}}, "GET /x") == {"a": 1}


def test_the_key_never_appears_in_any_failure_output(monkeypatch, store, capsys):
    """Every failure path: HTTP error, network error, bad JSON, bad status payload."""
    monkeypatch.setenv("HEVY_API_KEY", KEY)
    outputs = []

    def run(opener):
        monkeypatch.setattr(apphttp.OPENER, "open", opener)
        try:
            outputs.append(str(apphttp.check(*apphttp.request("hevy", "https://h.example", "GET", "/w"),
                                             "GET /w")))
        except SystemExit as exc:
            outputs.append(str(exc.code))

    def http_error(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    def network_error(req, timeout=None):
        raise urllib.error.URLError(f"echoed {req.get_header('Authorization')}")

    class Garbage:
        status = 200

        def read(self):
            return b"<html>not json</html>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    run(http_error)
    run(network_error)
    run(lambda req, timeout=None: Garbage())
    with pytest.raises(SystemExit) as exc:
        apphttp.check(500, ["x"], "GET /w")
    outputs.append(str(exc.value))

    captured = capsys.readouterr()
    everything = "\n".join(outputs) + captured.out + captured.err
    assert "401" in everything and "GET /w" in everything
    assert KEY not in everything
