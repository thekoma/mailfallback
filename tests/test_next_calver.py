"""CalVer computation script — exercised against throwaway git repos."""

import subprocess
from pathlib import Path

SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "next_calver.sh")


# Git hooks (pre-push from a worktree especially) export GIT_DIR,
# GIT_INDEX_FILE, GIT_WORK_TREE… to their children. Inherited, they point
# `git init`/`git tag` at the REAL repository instead of tmp_path — a push
# once re-initialised it as bare. Every call here gets a clean environment.
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_CONFIG_NOSYSTEM": "1",
    "HOME": "/nonexistent",
    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
}


def _git(tmp_path, *args):
    subprocess.run(["git", *args], cwd=tmp_path, check=True, env=_GIT_ENV)  # noqa: S607


def _git_repo(tmp_path, tags=()):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "--no-gpg-sign", "-m", "init")
    for tag in tags:
        _git(tmp_path, "tag", tag)
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


def test_fixture_ignores_inherited_git_env(tmp_path, monkeypatch):
    """A hook's GIT_DIR must not redirect the fixture to another repository."""
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    subprocess.run(["git", "init", "-q", str(decoy)], check=True, env=_GIT_ENV)  # noqa: S607
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(decoy))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_repo(repo, tags=["2026.07.0"])
    assert (repo / ".git").is_dir()
    decoy_cfg = (decoy / ".git" / "config").read_text()
    assert "bare = false" in decoy_cfg
    tags = subprocess.run(
        ["git", "tag", "-l"],  # noqa: S607
        cwd=decoy,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    ).stdout
    assert tags == ""
