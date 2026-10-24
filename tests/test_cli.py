import os
import subprocess
import sys
from pathlib import Path

import pytest

from backup import albums, cli, config, delta, files, repos, vcs

SUBCOMMANDS = ["backup-all", "backup-files", "backup-vcs", "backup-delta", "init", "init-secondary",
               "update-secondary", "restic", "restic-secondary"]


class Calls(list):
    codes: dict


@pytest.fixture
def calls(monkeypatch):
    """Records which subcommand functions run, each returning the exit code set in calls.codes."""
    calls = Calls()
    calls.codes = {}

    def stub(name):
        def run():
            calls.append(name)
            return calls.codes.get(name, 0)
        return run

    for module, name in [(vcs, "backup_vcs"), (files, "restic_backup_files"), (delta, "restic_backup_delta"),
                         (repos, "restic_init"), (repos, "restic_init_secondary"),
                         (repos, "restic_update_secondary")]:
        monkeypatch.setattr(module, name, stub(name))
    return calls


@pytest.mark.parametrize("argv, expected", [
    (["backup-files"], ["restic_backup_files"]),
    (["backup-vcs"], ["backup_vcs"]),
    (["backup-delta"], ["restic_backup_delta"]),
    (["init"], ["restic_init"]),
    (["init-secondary"], ["restic_init_secondary"]),
    (["update-secondary"], ["restic_update_secondary"]),
    (["backup-all"], ["backup_vcs", "restic_backup_files"]),
    ([], ["backup_vcs", "restic_backup_files"]),
])
def test_subcommand_dispatch(env, calls, argv, expected):
    # build_parser binds the functions when it runs, so the stubs are what gets called
    assert cli.main(argv) == 0
    assert calls == expected


def test_backup_all_with_delta(env, calls):
    env.write_settings(backup_all_includes_delta=True)
    assert cli.main([]) == 0
    assert calls == ["backup_vcs", "restic_backup_files", "restic_backup_delta"]
    assert albums.calls == [["backup-raw", "--all", "--no-eject"]]


def test_backup_all_backs_up_album_raws_also_when_restic_fails(env, calls, monkeypatch):
    calls.codes["restic_backup_files"] = 1
    assert cli.main([]) == 1
    assert albums.calls == [["backup-raw", "--all", "--no-eject"]]
    monkeypatch.setattr(albums, "albumflow", lambda *args: 2)
    calls.codes["restic_backup_files"] = 0
    assert cli.main([]) == 2


@pytest.mark.parametrize("vcs_code, files_code, delta_code, delta_runs, result", [
    (0, 0, 0, True, 0),
    (1, 0, 0, True, 1),     # a failed git repo doesn't stop the file backup, but fails the run
    (0, 3, 0, True, 3),     # some files unreadable, but a snapshot was made: delta still runs
    (0, 1, 0, False, 1),    # no snapshot: no delta
    (0, 0, 1, True, 1),
    (1, 3, 0, True, 1),
])
def test_backup_all_exit_codes(env, calls, vcs_code, files_code, delta_code, delta_runs, result):
    env.write_settings(backup_all_includes_delta=True)
    calls.codes.update(backup_vcs=vcs_code, restic_backup_files=files_code, restic_backup_delta=delta_code)
    assert cli.main(["backup-all"]) == result
    assert ("restic_backup_delta" in calls) == delta_runs
    assert calls[:2] == ["backup_vcs", "restic_backup_files"]


def test_backup_all_files_run_even_if_vcs_fails(env, calls):
    calls.codes["backup_vcs"] = 1
    assert cli.main([]) == 1
    assert calls == ["backup_vcs", "restic_backup_files"]


def test_help_lists_subcommands_and_repos(env, capsys):
    with pytest.raises(SystemExit) as exit:
        cli.main(["-h"])
    assert exit.value.code == 0
    out = capsys.readouterr().out
    for subcommand in SUBCOMMANDS:
        assert subcommand in out
    assert str(env.repo) in out
    assert str(env.secondary_repo) in out
    assert str(env.cloud) in out


def test_help_with_drives_missing(env, capsys):
    (env.drive / ".backup-destination").unlink()
    with pytest.raises(SystemExit):
        cli.main(["-h"])
    assert "<drive with .backup-destination>" in capsys.readouterr().out


def test_unknown_subcommand(env, capsys):
    with pytest.raises(SystemExit) as exit:
        cli.main(["backup-everything"])
    assert exit.value.code == 2


def test_passthrough_options_reach_restic(env, monkeypatch):
    seen = []
    monkeypatch.setattr(repos, "restic", lambda *args, repo=None: seen.append((args, repo)) or 0)
    monkeypatch.setattr(repos, "copy_self_to_drive_of", lambda repo: None)
    assert cli.main(["restic", "forget", "--prune", "--keep-last", "5", "--dry-run"]) == 0
    assert cli.main(["restic-secondary", "check", "-h"]) == 0
    assert seen == [(("forget", "--prune", "--keep-last", "5", "--dry-run"), str(env.repo)),
                    (("check", "-h"), str(env.secondary_repo))]


def test_python_dash_m(env):
    result = subprocess.run([sys.executable, "-m", "backup", "backup-vcs", "-h"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "backup branches" in result.stdout


def test_console_script_in_venv():
    script = Path(sys.executable).parent / "backup.exe"
    if not script.exists():
        pytest.skip("not run from the project's venv")
    result = subprocess.run([str(script), "-h"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "backup-all" in result.stdout


def test_bin_wrapper():
    wrapper = Path(os.environ["USERPROFILE"], "bin", "backup.cmd")
    if "venv" not in (wrapper.read_text() if wrapper.exists() else ""):
        pytest.skip(f"{wrapper} doesn't run the venv's console script (yet)")
    result = subprocess.run(["cmd", "/c", str(wrapper), "-h"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "backup-all" in result.stdout


def test_subcommand_help_names_settings_relative_to_appdata(env, monkeypatch, capsys):
    monkeypatch.setenv("APPDATA", str(env.settings.parent.parent))
    monkeypatch.setattr(config, "SETTINGS_PATH", env.settings.parent.parent / "backup" / "config.json")
    for subcommand in ["backup-delta", "backup-all"]:
        with pytest.raises(SystemExit):
            cli.main([subcommand, "-h"])
        assert R"%APPDATA%\backup\config.json" in " ".join(capsys.readouterr().out.split())


def test_settings_path_for_help_outside_appdata(env, monkeypatch):
    monkeypatch.setenv("APPDATA", str(env.root / "elsewhere"))
    assert config.settings_path_for_help() == str(env.settings)


def test_main_help_survives_percent_signs(env, monkeypatch, capsys):
    monkeypatch.setenv("APPDATA", str(env.settings.parent.parent))
    monkeypatch.setattr(config, "SETTINGS_PATH", env.settings.parent.parent / "backup" / "config.json")
    with pytest.raises(SystemExit) as exit:
        cli.main(["-h"])
    assert exit.value.code == 0
    assert R"%APPDATA%\backup\config.json" in " ".join(capsys.readouterr().out.split())


def test_backup_delta_help_names_secondary_state_file(env, capsys):
    with pytest.raises(SystemExit):
        cli.main(["backup-delta", "-h"])
    out = "".join(capsys.readouterr().out.split())  # argparse may wrap at hyphens
    expected = f"tracked in {env.drive / 'Backup' / 'secondary-latest-snapshot.json'}, written by update-secondary"
    assert "".join(expected.split()) in out


def test_backup_delta_help_without_primary_drive(env, capsys):
    (env.drive / ".backup-destination").unlink()
    with pytest.raises(SystemExit):
        cli.main(["backup-delta", "-h"])
    out = "".join(capsys.readouterr().out.split())  # argparse may wrap at hyphens
    expected = R"tracked in <drive with .backup-destination>:\Backup\secondary-latest-snapshot.json"
    assert "".join(expected.split()) in out


def test_subcommand_list_has_no_details(env, capsys):
    with pytest.raises(SystemExit):
        cli.main(["-h"])
    assert "secondary-latest-snapshot.json" not in capsys.readouterr().out
