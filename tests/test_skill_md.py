"""Every command SKILL.md tells the agent to run exists as written.

Extracts each `scripts/<name>.py [sub] [--flags]` from SKILL.md and checks the
script is executable, the subcommand is one the script's parser offers, and every
flag appears in that subcommand's --help. Also keeps the names that moved out of
SKILL.md (and the files it points at) from going stale.
"""
import os
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = (ROOT / "SKILL.md").read_text()

# `scripts/x.py ...` up to the closing backtick or a shell comment.
CALL = re.compile(r"scripts/(\w+)\.py([^`\n#]*)")


def commands():
    seen = []
    for script, rest in CALL.findall(SKILL):
        tokens = rest.replace("\\|", "|").split()
        subs = [""]
        if tokens and re.fullmatch(r"[a-z][a-z-]*(\|[a-z][a-z-]*)*", tokens[0]):
            subs, tokens = tokens[0].split("|"), tokens[1:]
        flags = sorted({t.split("=")[0] for t in tokens if re.fullmatch(r"--[a-z][a-z-]*(=.*)?", t)})
        for sub in subs:
            row = (script, sub, tuple(flags))
            if row not in seen:
                seen.append(row)
    return seen


def run_help(script, *sub):
    env = {**os.environ, "KG_TRAINER_HOME": str(ROOT.parent / "kg-trainer-help-store")}
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / f"{script}.py"), *sub, "--help"],
                          capture_output=True, text=True, env=env, cwd=str(ROOT.parent))
    return done.returncode, done.stdout + done.stderr


def test_skill_md_has_commands_to_check():
    assert len(commands()) >= 20


@pytest.mark.parametrize("script,sub,flags", commands(),
                         ids=lambda v: v if isinstance(v, str) else None)
def test_command_exists_as_written(script, sub, flags):
    path = ROOT / "scripts" / f"{script}.py"
    assert path.exists(), f"SKILL.md runs scripts/{script}.py, which does not exist"
    assert os.access(path, os.X_OK), f"scripts/{script}.py is not executable"
    code, top = run_help(script)
    assert code == 0, top
    offered = re.search(r"\{([^}]+)\}", top.split("positional arguments")[0] if "positional" in top else "")
    if sub:
        assert offered and sub in offered.group(1).split(","), (
            f"SKILL.md runs `scripts/{script}.py {sub}`; the script offers "
            f"{offered.group(1) if offered else 'no subcommands'}")
        code, text = run_help(script, sub)
        assert code == 0, text
    else:
        text = top
    for flag in flags:
        assert flag in text, f"SKILL.md uses {flag} with `scripts/{script}.py {sub}`; --help lacks it"


@pytest.mark.parametrize("stale", ["import-liftosaur", "program-push", "program-stats",
                                   "program-get", "analyze.py pull", "references/liftoscript.md",
                                   "references/liftosaur-api.md", "references/liftohistory.md"])
def test_skill_md_names_nothing_that_moved(stale):
    assert stale not in SKILL


def test_every_reference_skill_md_points_at_exists():
    for ref in re.findall(r"`((?:references|docs)/[\w./-]+)`", SKILL):
        if "<" in ref:
            continue
        assert (ROOT / ref).exists(), f"SKILL.md points at {ref}, which does not exist"
    for ref in re.findall(r"`(tests/[\w./-]+)`", SKILL):
        assert (ROOT / ref).exists(), ref
    for app in ("liftosaur", "hevy"):
        assert (ROOT / "references" / "adapters" / app / "README.md").exists()
