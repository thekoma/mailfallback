"""CalVer computation script — exercised against throwaway git repos."""

import subprocess
from pathlib import Path

SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "next_calver.sh")


def _git_repo(tmp_path, tags=()):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)  # noqa: S607
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "--no-gpg-sign", "-m", "init"],  # noqa: S607
        cwd=tmp_path,
        check=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        },
    )
    for tag in tags:
        subprocess.run(["git", "tag", tag], cwd=tmp_path, check=True)  # noqa: S607
    return tmp_path


def _run(cwd, *args, today="2026-07-15"):
    result = subprocess.run(
        [SCRIPT, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env={"CALVER_TODAY": today, "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_first_release_ever(tmp_path):
    assert _run(_git_repo(tmp_path)) == "2026.07.0"


def test_increments_within_month(tmp_path):
    repo = _git_repo(tmp_path, tags=["2026.07.0", "2026.07.1"])
    assert _run(repo) == "2026.07.2"


def test_resets_on_new_month(tmp_path):
    repo = _git_repo(tmp_path, tags=["2026.07.3"])
    assert _run(repo, today="2026-08-01") == "2026.08.0"


def test_ignores_prerelease_and_foreign_tags(tmp_path):
    repo = _git_repo(tmp_path, tags=["2026.07.0", "2026.07.1-rc1", "pre-squash-backup"])
    assert _run(repo) == "2026.07.1"


def test_first_rc(tmp_path):
    repo = _git_repo(tmp_path, tags=["2026.07.0"])
    assert _run(repo, "--pre", "rc") == "2026.07.1-rc1"


def test_rc_increments(tmp_path):
    repo = _git_repo(tmp_path, tags=["2026.07.0", "2026.07.1-rc1", "2026.07.1-rc2"])
    assert _run(repo, "--pre", "rc") == "2026.07.1-rc3"
