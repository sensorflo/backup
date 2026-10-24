import json
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import backup
from backup import albums, cli, config, repos
from conftest import restic_path, snapshots


def test_repo_found_by_marker(env):
    assert repos.repo_path() == str(env.drive / config.REPO_DIR)
    assert repos.secondary_repo_path() == str(env.secondary_drive / config.SECONDARY_REPO_DIR)


def test_no_drive_with_marker(env):
    (env.drive / config.REPO_MARKER_NAME).unlink()
    with pytest.raises(SystemExit, match="exactly one drive.*found: none"):
        repos.repo_path()
    assert repos.repo_path(exit_on_error=False) is None


def test_two_drives_with_marker(env):
    (env.other_drive / config.REPO_MARKER_NAME).write_text("")
    with pytest.raises(SystemExit, match="drive-primary.*drive-other|drive-other.*drive-primary"):
        repos.repo_path()


def test_init_creates_repo_and_self_copy(env, capfd):
    assert cli.main(["init"]) == 0
    assert (env.repo / "config").is_file()
    copy = env.drive / config.BACKUP_DIR / config.SELF_COPY_NAME
    assert copy.is_file()
    assert not os.access(copy, os.W_OK), "the self copy is read-only"


def test_init_refuses_existing(env):
    env.init_primary()
    with pytest.raises(SystemExit, match="already exists"):
        cli.main(["init"])


def test_self_copy_holds_all_modules(env):
    env.init_primary()
    with zipfile.ZipFile(env.drive / config.BACKUP_DIR / config.SELF_COPY_NAME) as archive:
        names = set(archive.namelist())
    package = Path(backup.__file__).parent
    assert names == {"__main__.py"} | {f"backup/{module.name}" for module in package.glob("*.py")}


def test_self_copy_overwrites_read_only_copy(env):
    env.init_primary()
    copy = env.drive / config.BACKUP_DIR / config.SELF_COPY_NAME
    copy.chmod(stat.S_IWRITE)
    copy.write_text("stale")
    copy.chmod(stat.S_IREAD)
    repos.copy_self_to_drive_of(str(env.repo))
    assert zipfile.is_zipfile(copy)


def test_self_copy_runs_without_the_project(env, tmp_path):
    """python backup.pyz works in an isolated interpreter that can't import the installed package."""
    env.init_primary()
    result = subprocess.run([sys.executable, "-I", str(env.drive / config.BACKUP_DIR / config.SELF_COPY_NAME), "-h"],
                            capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    for command in ["backup-all", "backup-files", "backup-vcs", "update-cloud", "init", "init-secondary",
                       "update-secondary", "restic", "restic-secondary"]:
        assert command in result.stdout


def test_self_copy_can_restore(env, tmp_path):
    """The self copy's restic pass-through restores a snapshot, run from the copy itself with its own repo
    lookup pointed at the temporary drives."""
    env.init_primary()
    env.write(env.home / "a.txt", "precious")
    assert repos.restic("backup", str(env.home)) == 0
    copy = env.drive / config.BACKUP_DIR / config.SELF_COPY_NAME
    target = tmp_path / "restored"
    script = (f"import sys; sys.path.insert(0, {str(copy)!r})\n"
              "from pathlib import Path\n"
              "from backup import cli, repos\n"
              f"repos.drives = lambda: [Path({str(env.drive)!r})]\n"
              f"raise SystemExit(cli.main(['restic', 'restore', 'latest:{restic_path(env.home)}', "
              f"'--target', {str(target)!r}]))\n")
    result = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (target / "a.txt").read_text() == "precious"
    assert (env.drive / config.BACKUP_DIR / config.DRIVE_README_NAME).is_file(), "the readme is written even when running from the copy"


def test_self_copy_needs_marker(env, tmp_path):
    with pytest.raises(SystemExit, match="No folder above"):
        repos.copy_self_to_drive_of(str(tmp_path / "nowhere" / "repo"))


def test_restic_passthrough(env, capfd):
    env.init_primary()
    capfd.readouterr()
    # --json would be rejected by argparse, so this also proves the pass-through bypasses it
    assert cli.main(["restic", "snapshots", "--json"]) == 0
    assert json.loads(capfd.readouterr().out) == []


def test_restic_passthrough_exit_code(env):
    env.init_primary()
    assert cli.main(["restic", "cat", "snapshot", "deadbeef"]) != 0


def test_restic_passthrough_copies_self(env):
    env.init_primary()
    copy = env.drive / config.BACKUP_DIR / config.SELF_COPY_NAME
    copy.chmod(stat.S_IWRITE)
    copy.unlink()
    cli.main(["restic", "snapshots"])
    assert copy.is_file()


def test_restic_secondary_passthrough(env, capfd):
    env.init_primary()
    env.init_secondary()
    capfd.readouterr()
    assert cli.main(["restic-secondary", "cat", "config"]) == 0
    assert "chunker_polynomial" in capfd.readouterr().out
    assert (env.secondary_drive / config.BACKUP_DIR / config.SELF_COPY_NAME).is_file()


def test_init_secondary_copies_chunker_params(env):
    env.init_primary()
    assert cli.main(["init-secondary"]) == 0
    assert repos.chunker_polynomial(str(env.repo)) == repos.chunker_polynomial(str(env.secondary_repo))
    assert (env.secondary_drive / config.BACKUP_DIR / config.SELF_COPY_NAME).is_file()


def test_init_secondary_needs_primary(env):
    with pytest.raises(SystemExit, match="No restic repository.*backup init"):
        cli.main(["init-secondary"])


def test_init_secondary_refuses_existing(env):
    env.init_primary()
    env.init_secondary()
    with pytest.raises(SystemExit, match="already exists"):
        cli.main(["init-secondary"])


def backup_home(env):
    assert repos.restic("backup", str(env.home)) == 0
    return snapshots(env.repo)[-1]


def test_update_secondary_copies_snapshots_and_writes_state(env):
    env.init_primary()
    env.init_secondary()
    env.write(env.home / "a.txt", "1")
    backup_home(env)
    env.write(env.home / "b.txt", "2")
    latest = backup_home(env)
    assert cli.main(["update-secondary"]) == 0
    assert sorted(s["time"] for s in snapshots(env.secondary_repo)) == sorted(s["time"] for s in snapshots(env.repo))
    state = json.loads((env.drive / "Backup" / config.SECONDARY_STATE_NAME).read_text(encoding="utf-8"))
    assert state["snapshot"] == latest["id"]
    assert state["time"] == latest["time"]
    assert "copied" in state
    assert env.snapshot_files(env.secondary_repo) == {"home/a.txt", "home/b.txt"}


def test_update_secondary_runs_albumflow_for_the_album_raws(env, monkeypatch):
    env.init_primary()
    env.init_secondary()
    env.write(env.home / "a.txt", "1")
    backup_home(env)
    assert cli.main(["update-secondary"]) == 0
    assert albums.calls == [["update-secondary"]]
    monkeypatch.setattr(albums, "albumflow", lambda *args: 2)
    assert cli.main(["update-secondary"]) == 2
    assert repos.secondary_state_path().exists(), "the snapshots were copied all the same"


def test_update_secondary_twice_copies_only_new(env):
    env.init_primary()
    env.init_secondary()
    env.write(env.home / "a.txt", "1")
    backup_home(env)
    cli.main(["update-secondary"])
    env.write(env.home / "b.txt", "2")
    latest = backup_home(env)
    cli.main(["update-secondary"])
    assert len(snapshots(env.secondary_repo)) == 2
    state = json.loads(repos.secondary_state_path().read_text(encoding="utf-8"))
    assert state["snapshot"] == latest["id"]


def test_update_secondary_refuses_other_chunker_params(env):
    env.init_primary()
    assert repos.restic("init", repo=str(env.secondary_repo)) == 0
    with pytest.raises(SystemExit, match="different chunker parameters"):
        cli.main(["update-secondary"])
    assert not repos.secondary_state_path().exists()


def test_update_secondary_needs_secondary(env):
    env.init_primary()
    with pytest.raises(SystemExit, match="No restic repository.*backup init-secondary"):
        cli.main(["update-secondary"])


def test_update_secondary_failure_keeps_state(env, monkeypatch):
    env.init_primary()
    env.init_secondary()
    env.write(env.home / "a.txt", "1")
    backup_home(env)
    real_restic = repos.restic
    monkeypatch.setattr(repos, "restic", lambda *args, repo=None: 1 if args[0] == "copy" else real_restic(*args, repo=repo))
    assert cli.main(["update-secondary"]) == 1
    assert not repos.secondary_state_path().exists()
    assert albums.calls == [], "no album raws without the snapshots"


def test_snapshot_path():
    assert repos.snapshot_path(R"C:\Users\Senso") == "/C/Users/Senso"
    assert repos.snapshot_path("S:\\") == "/S"


def test_readme_written_next_to_self_copy(env):
    env.init_primary()
    readme = env.drive / config.BACKUP_DIR / config.DRIVE_README_NAME
    text = readme.read_text(encoding="utf-8")
    assert not os.access(readme, os.W_OK), "the readme is read-only"
    assert b"\r\n" in readme.read_bytes(), "Windows line endings, for Notepad"
    assert "This drive holds the primary restic repository" in text
    assert f"python {env.drive / config.BACKUP_DIR / config.SELF_COPY_NAME} update-secondary" in text


def test_readme_on_secondary_drive(env):
    env.init_primary()
    env.init_secondary()
    text = (env.secondary_drive / config.BACKUP_DIR / config.DRIVE_README_NAME).read_text(encoding="utf-8")
    assert "This drive holds the secondary restic repository" in text


def test_readme_rewritten(env):
    env.init_primary()
    readme = env.drive / config.BACKUP_DIR / config.DRIVE_README_NAME
    readme.chmod(stat.S_IWRITE)
    readme.write_text("stale")
    readme.chmod(stat.S_IREAD)
    cli.main(["restic", "snapshots"])
    assert "This drive holds" in readme.read_text(encoding="utf-8")


def test_readme_with_production_paths(env, monkeypatch):
    monkeypatch.setattr(config, "HOME", R"C:\Users\Senso")
    monkeypatch.setattr(config, "SD", "S:\\")
    text = repos.drive_readme(env.drive)
    lines = [line.strip() for line in text.splitlines()]
    assert R"backup restic restore latest:/C/Users/Senso --target C:\Users\Senso" in lines
    assert "backup restic restore latest:/S --target S:\\" in lines
    assert R"C:\Users\Senso\src\backup" in text


def test_readme_full_restore_commands_work(env):
    """Run the readme's full restore commands after losing the home folder and the SD card."""
    env.write(env.home / "Documents" / "a.txt", "home data")
    env.write(env.sd / "Music" / "b.flac", b"sd data")
    env.init_primary()
    assert cli.main(["backup-files"]) == 0
    text = (env.drive / config.BACKUP_DIR / config.DRIVE_README_NAME).read_text(encoding="utf-8")
    commands = [line.split()[1:] for line in text.splitlines() if line.strip().startswith("backup restic restore latest:/")
                and "--target" in line and '"' not in line]
    assert len(commands) == 2
    shutil.rmtree(env.home)
    shutil.rmtree(env.sd)
    for command in commands:
        assert cli.main(command) == 0
    assert (env.home / "Documents" / "a.txt").read_text() == "home data"
    assert (env.sd / "Music" / "b.flac").read_bytes() == b"sd data"


def test_nothing_written_to_drive_root(env):
    """The tools own the Backup folder; the drive's root holds only the marker file."""
    env.init_primary()
    env.init_secondary()
    assert cli.main(["backup-files"]) == 0
    assert cli.main(["update-secondary"]) == 0
    assert sorted(p.name for p in env.drive.iterdir()) == sorted([config.REPO_MARKER_NAME, config.BACKUP_DIR])
    assert sorted(p.name for p in env.secondary_drive.iterdir()) == sorted(
        [config.SECONDARY_REPO_MARKER_NAME, config.BACKUP_DIR])


def test_readme_is_well_formed_markdown(env):
    text = repos.drive_readme(env.drive)
    lines = text.splitlines()
    assert lines[0] == "# Backup"
    assert sum(line == "```" for line in lines) % 2 == 0, "balanced code fences"
    assert "\t" not in text
