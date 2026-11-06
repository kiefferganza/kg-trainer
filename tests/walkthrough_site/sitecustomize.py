"""Serve an Adapter's recorded fixtures to a real script process.

tests/test_walkthrough.py puts this directory on PYTHONPATH of the child it runs, so
`python scripts/app.py pull` talks to the fixture provider instead of the network.
Inert unless KG_WALK_APP names an app.
"""
import importlib
import io
import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse

APP = os.environ.get("KG_WALK_APP")
if APP:
    root = pathlib.Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(root / "scripts"), str(root / "tests")]

    import paths
    from adapters import http as apphttp

    provider = importlib.import_module(f"contract_fixtures.{APP}")
    if hasattr(provider, "setup"):
        paths.state_dir().mkdir(parents=True, exist_ok=True)
        provider.setup(paths.state_dir())

    class Reply:
        def __init__(self, status, body):
            self.status, self._raw = status, json.dumps(body).encode()

        def read(self):
            return self._raw

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def opener(req, timeout=None):
        url = urllib.parse.urlparse(req.full_url)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        body = json.loads(req.data) if req.data else None
        got = provider.respond(req.get_method(), url.path, query, body)
        if got is None:
            raise AssertionError(f"no recorded response for {req.get_method()} {url.path} {query}")
        status, payload = got
        if status >= 300:
            raise urllib.error.HTTPError(req.full_url, status, "err", {},
                                         io.BytesIO(json.dumps(payload).encode()))
        return Reply(status, payload)

    apphttp.OPENER.open = opener
