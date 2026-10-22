"""Every test runs against temporary folders: HOME, SD, the drives holding the repos, the cloud repo and the
settings all live below tmp_path, and restic refuses (via the guard below) any repo outside of it. git may
only use local paths as remotes (GIT_ALLOW_PROTOCOL=file), so nothing can be pushed to GitHub."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from backup import config, files, repos

if shutil.which("restic") is None or shutil.which("git") is None:
    pytest.exit("The tests need restic and git on the PATH", returncode=1)


class Env:
    """The temporary world a test runs in."""

    def __init__(self, tmp_path: Path, templates: dict[str, Path]):
        self.templates = templates
        self.root = tmp_path
        self.home = tmp_path / "home"
        self.sd = tmp_path / "sd"
        self.drive = tmp_path / "drive-primary"
        self.secondary_drive = tmp_path / "drive-secondary"
        self.other_drive = tmp_path / "drive-other"
        self.cloud = tmp_path / "gdrive" / "Backup" / "cloud-repo"
        self.remotes = tmp_path / "remotes"
        self.settings = tmp_path / "settings" / "config.json"
        for folder in (self.home, self.sd, self.drive, self.secondary_drive, self.other_drive, self.remotes):
            folder.mkdir()
        (self.sd / config.SD_MARKER_NAME).write_text("")
        (self.drive / config.REPO_MARKER_NAME).write_text("")
        (self.secondary_drive / config.SECONDARY_REPO_MARKER_NAME).write_text("")

    @property
    def repo(self) -> Path:
        return self.drive / config.REPO_DIR

    @property
    def secondary_repo(self) -> Path:
        return self.secondary_drive / config.SECONDARY_REPO_DIR

    def write(self, path: Path, content: bytes | str = b"x", mtime: float | None = None) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def write_settings(self, **settings) -> None:
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(json.dumps(settings), encoding="utf-8")

    def snapshot_files(self, repo: Path, snapshot: str = "latest") -> set[str]:
        """Paths of the files in a snapshot, relative to the test's tmp_path and in posix form."""
        return snapshot_files(repo, self.root, snapshot)

    def init_primary(self) -> None:
        """What backup init does, but faster: a copy of an empty repo made once per test session."""
        shutil.copytree(self.templates["primary"], self.repo)
        repos.copy_self_to_drive_of(str(self.repo))

    def init_secondary(self) -> None:
        """What backup init-secondary does, but faster: a copy of a repo created once per test session with
        the chunker parameters of the primary template."""
        shutil.copytree(self.templates["secondary"], self.secondary_repo)
        repos.copy_self_to_drive_of(str(self.secondary_repo))


def restic_path(path: Path) -> str:
    """How restic names path in a snapshot: C:\\Users\\x -> /C/Users/x."""
    posix = Path(path).resolve().as_posix()
    return f"/{posix[0]}{posix[2:]}"


def snapshot_files(repo: Path, root: Path, snapshot: str = "latest") -> set[str]:
    """Paths of the files in a snapshot, relative to root and in posix form."""
    prefix = restic_path(root) + "/"
    out = repos.restic_output("ls", "--json", snapshot, repo=str(repo))
    nodes = (json.loads(line) for line in out.splitlines() if line.strip())
    return {node["path"].removeprefix(prefix) for node in nodes
            if node.get("message_type") == "node" and node.get("type") == "file"}


def snapshots(repo: Path) -> list[dict]:
    return json.loads(repos.restic_output("snapshots", "--json", repo=str(repo)))


@pytest.fixture(scope="session")
def repo_templates(tmp_path_factory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("repo-templates")
    primary, secondary = root / "primary", root / "secondary"
    environment = {**os.environ, "RESTIC_CACHE_DIR": str(root / "cache")}
    for args in (["init", "--compression", "off", "--repo", str(primary)],
                 ["init", "--repo", str(secondary), "--from-repo", str(primary), "--from-insecure-no-password",
                  "--copy-chunker-params"]):
        subprocess.run(["restic", *args, "--insecure-no-password", "--quiet"], check=True, env=environment)
    return {"primary": primary, "secondary": secondary}


@pytest.fixture
def env(tmp_path, repo_templates) -> Env:
    return Env(tmp_path, repo_templates)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    home, sd = tmp_path / "home", tmp_path / "sd"
    monkeypatch.setattr(config, "HOME", str(home))
    monkeypatch.setattr(config, "SD", str(sd))
    monkeypatch.setattr(config, "CLOUD_REPO", str(tmp_path / "gdrive" / "Backup" / "cloud-repo"))
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings" / "config.json")
    monkeypatch.setattr(config, "OWN_REMOTE_PREFIXES", ((tmp_path / "remotes").as_posix() + "/",))
    drives = [tmp_path / "drive-primary", tmp_path / "drive-secondary", tmp_path / "drive-other"]
    monkeypatch.setattr(repos, "drives", lambda: [drive for drive in drives if drive.exists()])
    monkeypatch.setattr(files, "running_apps", lambda names: [])
    repos.find_repo_path.cache_clear()

    real_restic_command = repos.restic_command
    allowed = tmp_path.resolve()

    def guarded_restic_command(args, repo):
        if not Path(repo).resolve().is_relative_to(allowed):
            raise AssertionError(f"test tried to use restic repo {repo} outside of {allowed}")
        return real_restic_command(args, repo)

    monkeypatch.setattr(repos, "restic_command", guarded_restic_command)

    monkeypatch.setenv("RESTIC_CACHE_DIR", str(tmp_path / "tool-state" / "restic-cache"))
    monkeypatch.delenv("RESTIC_REPOSITORY", raising=False)
    monkeypatch.delenv("RESTIC_PASSWORD", raising=False)
    # git: local remotes only, and none of my own git config (url rewrites, hooks, signing)
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    git_config = tmp_path / "tool-state" / "gitconfig"
    git_config.parent.mkdir()
    git_config.write_text("[user]\n\tname = Test\n\temail = test@example.com\n"
                          "[init]\n\tdefaultBranch = main\n[core]\n\tautocrlf = false\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(git_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    yield
    repos.find_repo_path.cache_clear()
