import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_no_tracked_file_mentions_hevy():
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                           text=True, check=True).stdout.split()
    offenders = {}
    for name in files:
        if name.startswith("docs/superpowers/"):
            continue          # the spec and this plan describe the migration itself
        if name == "tests/test_no_hevy.py":
            continue          # this file names what it forbids
        path = ROOT / name
        if not path.is_file():
            continue
        hits = [i for i, line in enumerate(path.read_text(errors="ignore").splitlines(), 1)
                if "hevy" in line.lower()]
        if hits:
            offenders[name] = hits[:5]
    assert offenders == {}, f"Hevy references remain: {offenders}"


def test_hevy_files_are_gone():
    for name in ("scripts/hevy.py", "references/hevy-api.md", "references/hevy-openapi.json"):
        assert not (ROOT / name).exists(), f"{name} still present"
