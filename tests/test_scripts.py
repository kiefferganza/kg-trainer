import os
import pathlib

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
COMMANDS = sorted(p.name for p in SCRIPTS.glob("*.py")
                  if p.read_text().startswith("#!") and "def main(" in p.read_text())


def test_there_are_commands_to_check():
    assert {"liftosaur.py", "fetch_reference.py", "analyze.py"} <= set(COMMANDS)


@pytest.mark.parametrize("name", COMMANDS)
def test_documented_commands_are_executable(name):
    # SKILL.md and the README run these as scripts/<name>, not through python.
    assert os.access(SCRIPTS / name, os.X_OK), f"scripts/{name} is not executable"
