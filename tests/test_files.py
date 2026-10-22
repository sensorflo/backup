import stat
import subprocess
from pathlib import Path

import pytest

from backup import cli, config, files, repos
from conftest import snapshots

# Taken before the isolated fixture stubs it
real_running_apps = files.running_apps

# Relative to tmp_path: (path, whether backup-files must back it up)
TREE = [
    # Home folder
    ("home/Documents/letter.txt", True),
    ("home/Documents/sub dir/ümlaut ünd space.txt", True),
    ("home/Large/Pictures/Pictures/new/party/DSC1.ARW", False),
    ("home/Large/Pictures/Pictures/new/party/DSC1.xmp", True),
    ("home/Large/Pictures/Pictures/new/party/DSC1.jpg", True),
    ("home/Large/Pictures/Pictures/new-old/x/DSC2.arw", False),
    ("home/Large/Pictures/Pictures/old/2020/DSC3.arw", True),
    ("home/Large/Pictures/Rejected/a/DSC4.arw", False),
    ("home/Large/Pictures/Rejected/a/DSC4.jpg", True),
    ("home/Large/Pictures/thumbnails-digikam.db", False),
    ("home/Large/Pictures/digikam4.db", True),
    ("home/Large/Pictures/Lightroom/Lightroom Catalog.lrcat", True),
    ("home/Large/Pictures/Lightroom/Lightroom Catalog Previews.lrdata/a/b.lrprev", False),
    ("home/Large/Pictures/Lightroom/Lightroom Catalog Smart Previews.lrdata/c.dng", False),
    ("home/Large/Pictures/Pictures/old/2020/DSC3.acr", False),
    ("home/Large/Pictures/Temp/scratch.jpg", False),
    ("home/Large/Pictures/darktable/.dtrash/gone.arw", False),
    ("home/src/proj/main.py", True),
    ("home/src/proj/.git/HEAD", False),
    ("home/src/proj/build/out.o", False),
    ("home/src/proj/build-release/out.o", False),
    ("home/src/vcpkg/x.txt", False),
    ("home/src/external/y.txt", False),
    ("home/NTUSER.DAT", False),
    ("home/NTUSER.DAT.LOG1", False),
    ("home/Downloads/setup.exe", False),
    ("home/OneDrive/doc.txt", False),
    ("home/CrossDevice/phone.jpg", False),
    ("home/.vscode/extensions/ext/package.json", False),
    ("home/.vscode/argv.json", True),
    # AppData: allowlist, minus excludes inside it
    ("home/AppData/Local/darktable/library.db", True),
    ("home/AppData/Local/darktable/library.db-snp-20260101", False),
    ("home/AppData/Local/darktable/data.db-pre-4.8", False),
    ("home/AppData/Local/darktable/styles/s.dtstyle", True),
    ("home/AppData/Local/digikamrc", True),
    ("home/AppData/Local/digikam/cache.db", False),
    ("home/AppData/Local/RawTherapee/options", True),
    ("home/AppData/Local/RawTherapee/cache/x.dat", False),
    ("home/AppData/Local/Google/Chrome/User Data/Default/History", False),
    ("home/AppData/Local/[weird]*folder/f.txt".replace("*", "+"), False),
    ("home/AppData/Roaming/Adobe/Lightroom/Preferences/p.agprefs", True),
    ("home/AppData/Roaming/Adobe/CameraRaw/Settings/s.xmp", True),
    ("home/AppData/Roaming/Adobe/CameraRaw/ModelZoo/model.bin", False),
    ("home/AppData/Roaming/Adobe/CameraRaw/Logs/log.txt", False),
    ("home/AppData/Roaming/Adobe/Acrobat/cache.txt", False),
    ("home/AppData/Roaming/Code/User/settings.json", True),
    ("home/AppData/Roaming/Code/Cache/data", False),
    ("home/AppData/Roaming/Mozilla/Firefox/Profiles/abc.default/places.sqlite", True),
    ("home/AppData/Roaming/Mozilla/Firefox/Profiles/abc.default/parent.lock", False),
    ("home/AppData/Roaming/syntevo/SmartGit/25.1/settings.xml", True),
    ("home/AppData/Roaming/syntevo/SmartGit/25.1/logs/log.txt", False),
    ("home/AppData/Roaming/syntevo/SmartGit/25.1/x.lock", False),
    ("home/AppData/Roaming/syntevo/SmartGit/updates/u.zip", False),
    ("home/AppData/Roaming/backup/config.json", True),
    ("home/AppData/LocalLow/thing.txt", False),
    # SD card
    ("sd/.backup-source-sd", True),
    ("sd/Large/Pictures/Pictures/new/trip/DSC5.ARW", False),
    ("sd/Large/Pictures/Pictures/new/trip/DSC5.xmp", True),
    ("sd/Large/Pictures/Pictures/new-old/t/DSC6.arw", False),
    ("sd/Large/Pictures/Pictures/2021/DSC7.arw", True),
    ("sd/Large/Pictures/Rejected/DSC8.arw", False),
    ("sd/rejected/r.jpg", False),
    ("sd/$RECYCLE.BIN/S-1-5/x", False),
    ("sd/Music/song.flac", True),
]


@pytest.fixture
def tree(env):
    for path, _ in TREE:
        env.write(env.root / path, f"content of {path}")
    return env


def test_includes_and_excludes_in_real_snapshot(tree):
    tree.init_primary()
    assert cli.main(["backup-files"]) == 0
    backed_up = tree.snapshot_files(tree.repo)
    expected = {path for path, included in TREE if included}
    excluded = {path for path, included in TREE if not included}
    assert expected - backed_up == set(), "missing from the snapshot"
    assert backed_up & excluded == set(), "excluded, but in the snapshot"
    assert backed_up == expected


def test_one_snapshot_holds_home_and_sd(tree):
    tree.init_primary()
    cli.main(["backup-files"])
    [snapshot] = snapshots(tree.repo)
    assert sorted(Path(p).name for p in snapshot["paths"]) == ["home", "sd"]


def test_excludes_all_but_keeps_includes_and_their_contents(tmp_path):
    tmp_path = tmp_path / "folder"
    tmp_path.mkdir()
    for path in ["a/keep/x", "a/keep/deep/y", "a/drop/z", "a/file", "b/c", "B2/d", "top.txt"]:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text("")
    patterns = files.excludes_all_but(tmp_path, [("a", "keep"), ("b",)])
    assert sorted(patterns) == sorted(str(tmp_path / p) for p in ["a/drop", "a/file", "B2", "top.txt"])


def test_excludes_all_but_is_case_insensitive(tmp_path):
    tmp_path = tmp_path / "folder"
    tmp_path.mkdir()
    (tmp_path / "Local" / "DarkTable").mkdir(parents=True)
    (tmp_path / "Local" / "other").mkdir()
    assert files.excludes_all_but(tmp_path, [("local", "darktable")]) == [str(tmp_path / "Local" / "other")]


def test_excludes_all_but_include_naming_a_file(tmp_path):
    tmp_path = tmp_path / "folder"
    tmp_path.mkdir()
    (tmp_path / "Local").mkdir()
    (tmp_path / "Local" / "digikamrc").write_text("")
    # An include below a file can't match anything inside it, so the file itself is kept
    assert files.excludes_all_but(tmp_path, [("Local", "digikamrc", "sub")]) == []


def test_excludes_all_but_replaces_pattern_characters(tmp_path):
    tmp_path = tmp_path / "folder"
    tmp_path.mkdir()
    (tmp_path / "[x]y").mkdir()
    assert files.excludes_all_but(tmp_path, []) == [str(tmp_path / "?x]y")]


def test_missing_sd_marker_refuses(tree):
    tree.init_primary()
    (tree.sd / config.SD_MARKER_NAME).unlink()
    with pytest.raises(SystemExit, match="SD card not found"):
        cli.main(["backup-files"])
    assert snapshots(tree.repo) == []


def test_running_apps_refuse(tree, monkeypatch):
    tree.init_primary()
    monkeypatch.setattr(files, "running_apps", lambda names: ["Lightroom.exe", "darktable.exe"])
    with pytest.raises(SystemExit, match="Lightroom.exe, darktable.exe"):
        cli.main(["backup-files"])
    assert snapshots(tree.repo) == []


def test_missing_repo_refuses(tree):
    with pytest.raises(SystemExit, match="No restic repository.*backup init"):
        cli.main(["backup-files"])


def test_running_apps_parses_tasklist(monkeypatch):
    output = '"System","4","Services","0","144 K"\n"Lightroom.exe","123","Console","1","1.000 K"\n\n'
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=output))
    assert real_running_apps({"lightroom.EXE", "darktable.exe"}) == ["lightroom.EXE"]


def test_running_apps_with_real_tasklist():
    assert real_running_apps({"definitely-not-running-xyz.exe"}) == []


def test_backup_files_copies_self_to_drive(tree):
    tree.init_primary()
    (tree.drive / config.SELF_COPY_NAME).chmod(stat.S_IWRITE)
    (tree.drive / config.SELF_COPY_NAME).unlink()
    cli.main(["backup-files"])
    assert (tree.drive / config.SELF_COPY_NAME).is_file()


def test_second_backup_is_incremental_and_sees_changes(tree):
    tree.init_primary()
    cli.main(["backup-files"])
    tree.write(tree.home / "Documents" / "new.txt", "new")
    (tree.home / "Documents" / "letter.txt").unlink()
    cli.main(["backup-files"])
    first, second = snapshots(tree.repo)
    assert "home/Documents/new.txt" in tree.snapshot_files(tree.repo, second["id"])
    assert "home/Documents/letter.txt" not in tree.snapshot_files(tree.repo, second["id"])
    assert "home/Documents/letter.txt" in tree.snapshot_files(tree.repo, first["id"])
