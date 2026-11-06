"""The Adapter Contract: what every Adapter offers or declines, and how to find them.

An Adapter is a module in scripts/adapters/ that declares `NAME` and `CAPABILITIES`
(the set of operation names it implements) and defines those operations. Only
`ping` and `pull_history` are required. The CLI asks `require()` before calling an
operation, so an undeclared one prints its Fallback instead of failing obscurely.

Also owns the Active App record in profile.json (`app` block) and the one-time
migration of the old `liftosaur` block.
"""

from __future__ import annotations

import importlib
import json
import pathlib
import pkgutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import paths  # noqa: E402
from adapters import http as apphttp  # noqa: E402

REQUIRED = ("ping", "pull_history")

# Operation -> the skill-owned way of covering it when an Adapter lacks it.
FALLBACKS = {
    "ping": "(required: every Adapter declares it)",
    "pull_history": "(required: every Adapter declares it)",
    "pull_bodyweight": "log weigh-ins by hand with `checkin.py log-weight`",
    "find_exercise": "use the shipped canonical exercise list only (references/exercises.txt)",
    "compile": "write the plan as meso-NN.md and enter it in the app by hand",
    "push_plan": "write the plan as meso-NN.md and enter it in the app by hand",
    "update_plan": "push the plan again, or edit it in the app by hand",
    "get_plan": "read the Canonical Plan in plans/",
    "check": "run `plan.py validate` and `plan.py stats` locally only",
}
OPERATIONS = tuple(FALLBACKS)

_NOT_ADAPTERS = {"http", "contract"}


def names() -> list[str]:
    """Module names of every shipped Adapter, sorted."""
    root = pathlib.Path(__file__).resolve().parent
    return sorted(m.name for m in pkgutil.iter_modules([str(root)])
                  if m.name not in _NOT_ADAPTERS and not m.name.startswith("_"))


def load(name: str):
    """The Adapter module called `name` (its NAME), or exit listing the shipped ones."""
    if name not in names():
        sys.exit(f"No Adapter named {name!r}. Shipped: {', '.join(names())}")
    module = importlib.import_module(f"adapters.{name}")
    if getattr(module, "NAME", None) != name or not isinstance(
            getattr(module, "CAPABILITIES", None), (set, frozenset)):
        sys.exit(f"Adapter {name!r} must declare NAME and CAPABILITIES")
    missing = [op for op in REQUIRED if op not in module.CAPABILITIES]
    if missing:
        sys.exit(f"Adapter {name!r} does not declare required: {', '.join(missing)}")
    return module


def all_adapters() -> list:
    return [load(n) for n in names()]


def capabilities(adapter) -> list[str]:
    """Declared operations in contract order."""
    return [op for op in OPERATIONS if op in adapter.CAPABILITIES]


def require(adapter, op: str):
    """The Adapter's `op`, or exit non-zero printing the op's Fallback."""
    if op not in adapter.CAPABILITIES:
        sys.exit(f"{adapter.NAME} does not support {op}. Fallback: {FALLBACKS[op]}")
    return getattr(adapter, op)


# --- profile.json: the Active App -----------------------------------------

def profile_path() -> pathlib.Path:
    return paths.state_dir() / "profile.json"


def _read(path: pathlib.Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except ValueError:
        sys.exit(f"{path} is not valid JSON; fix it before choosing an app.")
    if not isinstance(data, dict):
        sys.exit(f"{path} is not a JSON object; fix it before choosing an app.")
    return data


def migrate(profile: dict) -> bool:
    """Turn the old `liftosaur: {connected, key_source, program_id}` block into
    `app: {name, key_source, program_id}`, in place. True if it changed anything."""
    old = profile.get("liftosaur")
    if not isinstance(old, dict) or "app" in profile:
        return False
    app = {"name": "liftosaur"}
    for key in ("key_source", "program_id"):
        if key in old:
            app[key] = old[key]
    # Rebuild so `app` takes the old block's place and the other keys keep their order.
    items = [("app", app) if k == "liftosaur" else (k, v) for k, v in profile.items()]
    profile.clear()
    profile.update(items)
    return True


def load_profile() -> dict | None:
    """profile.json, migrated on first read (written back and logged once)."""
    path = profile_path()
    profile = _read(path)
    if profile is not None and migrate(profile):
        path.write_text(json.dumps(profile, indent=2) + "\n")
        print(f"profile.json: migrated the liftosaur block to app: liftosaur ({path})",
              file=sys.stderr)
    return profile


def active_app() -> str | None:
    """The Active App's name, or None when the store has not chosen one."""
    app = (load_profile() or {}).get("app")
    name = app.get("name") if isinstance(app, dict) else None
    return name if isinstance(name, str) and name else None


def active_adapter():
    """The Active App's Adapter. A store that has never chosen one gets a pointer
    to `app.py use`, not a guess."""
    name = active_app()
    if name is None:
        sys.exit("No Active App chosen for this store. Run `app.py use <name>` "
                 f"(shipped: {', '.join(names())}).")
    return load(name)


def set_active_app(name: str) -> dict:
    """Record `name` as the Active App. Switching apps drops the old program_id."""
    profile = load_profile() or {}
    old = profile.get("app") if isinstance(profile.get("app"), dict) else {}
    app = {"name": name, "key_source": apphttp.key_source(name)}
    if old.get("name") == name and "program_id" in old:
        app["program_id"] = old["program_id"]
    profile["app"] = app
    path = profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=2) + "\n")
    return app


def set_program_id(name: str, program_id: str) -> None:
    """Record the app's plan id as profile.json `app.program_id` for Active App
    `name`. Refuses an empty id: a push without an id is not a success."""
    if not program_id:
        sys.exit("Refusing to record an empty plan id.")
    profile = load_profile() or {}
    app = profile.get("app") if isinstance(profile.get("app"), dict) else {}
    if app.get("name") != name:
        app = {"name": name, "key_source": apphttp.key_source(name)}
    app["program_id"] = str(program_id)
    profile["app"] = app
    path = profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=2) + "\n")
