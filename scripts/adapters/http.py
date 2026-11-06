"""HTTP and API-key handling shared by every Adapter.

Three guarantees, so no Adapter has to re-implement them:
  * Redirects are refused, so the key is never re-sent to another host.
  * The key resolves for any app: $<APP>_API_KEY, then the nearest .env (cwd then
    parents), then <state>/<app>-key.
  * A failure names the operation and the HTTP status, never the key.

Import as `from adapters import http as apphttp` to keep it apart from the
standard library's `http` package.
"""

from __future__ import annotations

import http.client
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import paths  # noqa: E402


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib re-sends the Authorization header on redirects, even to another host.
    Refuse every redirect instead: the redirect status surfaces as an HTTP error."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Module-private on purpose: installing it globally would change urllib for every
# importer of this module (dashboard.py downloads through plain urlopen).
OPENER = urllib.request.build_opener(NoRedirect)


def env_name(app: str) -> str:
    """`liftosaur` -> LIFTOSAUR_API_KEY, `my-app` -> MY_APP_API_KEY."""
    return re.sub(r"[^A-Za-z0-9]", "_", app).upper() + "_API_KEY"


def dotenv_value(name: str) -> str | None:
    """Read one variable from the nearest .env, searching the cwd then its parents.

    Nothing in the standard library reads .env, so this is a deliberately small
    parser: `KEY=VALUE` lines, optional surrounding quotes, `#` comments and blank
    lines skipped. The walk continues past a .env that does not define `name`, so
    running from a nested directory (a git worktree, say) still finds the project
    root's file. An exported variable always wins over .env -- see api_key().
    """
    try:
        start = pathlib.Path.cwd().resolve()
    except OSError:
        return None
    for directory in (start, *start.parents):
        env_file = directory / ".env"
        if not env_file.is_file():
            continue
        try:
            text = env_file.read_text()
        except OSError:
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() != name:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            value = value.strip()
            if value:
                return value
    return None


def api_key(app: str, *, display: str | None = None, example: str = "...",
            hint: str = "") -> str:
    """Resolve `app`'s API key, or exit with guidance. Never prints a key."""
    name = env_name(app)
    env = os.environ.get(name)
    if env and env.strip():
        return env.strip()
    from_dotenv = dotenv_value(name)
    if from_dotenv:
        return from_dotenv
    key_file = paths.state_dir() / f"{app}-key"
    if key_file.exists():
        value = key_file.read_text().strip()
        if value:
            return value
    sys.exit(f"No {display or app} API key. Set ${name}, or put "
             f"{name}={example} in a .env file in the project directory "
             f"(keep it out of git), or save the key to {key_file} and chmod 600 it."
             + (f" {hint}" if hint else ""))


def bearer(key: str) -> dict:
    """Default auth: `Authorization: Bearer <key>` (Liftosaur)."""
    return {"Authorization": f"Bearer {key}"}


def header_auth(name: str):
    """Auth builder for apps that take the key in a plain header, e.g. `api-key`."""
    return lambda key: {name: key}


def request(app: str, base: str, method: str, path: str, params: dict | None = None,
            body: dict | None = None, *, key: str | None = None, auth=bearer, **key_help):
    """One JSON call as (status, parsed body or None).

    HTTP error statuses (including refused redirects) are returned, not raised, so
    the caller can report them through check(). Transport failures and an
    unparseable success body exit naming the operation, never the key.
    """
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    headers = {**auth(key or api_key(app, **key_help)), "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with OPENER.open(req, timeout=60) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        try:
            status, raw = exc.code, exc.read()
        except (OSError, http.client.HTTPException):
            return exc.code, None
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        sys.exit(f"{method} {path} failed: network error ({type(exc).__name__})")
    try:
        return status, json.loads(raw or b"null")
    except ValueError as exc:
        if status >= 300:
            return status, None
        sys.exit(f"{method} {path} failed: HTTP {status}, response was not JSON "
                 f"({type(exc).__name__})")


def check(status: int, payload, what: str, hints: dict[int, str] | None = None,
          *, unwrap: bool = True):
    """Unwrap {"data": ...} (or, with unwrap=False, return the payload object), or exit naming `what` and the status. Never echoes the key.

    `hints` maps a status to extra advice; any status below 400 (a refused
    redirect) gets the redirect explanation.
    """
    if status >= 300:
        error = payload.get("error") if isinstance(payload, dict) else None
        error = error if isinstance(error, dict) else {}
        detail = " ".join(str(x) for x in (error.get("code"), error.get("message")) if x)
        hint = (hints or {}).get(status, "")
        if not hint and status < 400:
            hint = "  (redirect not followed: the API moved or a proxy answered; the key was not forwarded)"
        sys.exit(f"{what} failed: HTTP {status} {detail}{hint}".rstrip())
    if not isinstance(payload, dict):
        sys.exit(f"{what} failed: response was not a JSON object")
    return payload.get("data") if unwrap else payload


def key_source(app: str) -> str | None:
    """Where `app`'s key would come from -- "env", "dotenv" or "file" -- or None.
    Never returns or prints the key itself."""
    name = env_name(app)
    if (os.environ.get(name) or "").strip():
        return "env"
    if dotenv_value(name):
        return "dotenv"
    key_file = paths.state_dir() / f"{app}-key"
    if key_file.exists() and key_file.read_text().strip():
        return "file"
    return None
