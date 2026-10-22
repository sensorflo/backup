import json
import os
import shutil
import time

import pytest

from backup import cli, config, delta, repos
from conftest import restic_path, snapshot_files, snapshots

DAY = 24 * 3600


@pytest.fixture
def synced(env):
    """Primary and secondary repo, the secondary holding the primary's only snapshot."""
    old = time.time() - 10 * DAY
    env.write(env.home / "Documents" / "unchanged.txt", "unchanged", mtime=old)
    env.write(env.home / "Documents" / "modified.txt", "before", mtime=old)
    env.write(env.home / "Documents" / "to-move.txt", "moving content", mtime=old)
    env.write(env.home / "Documents" / "deleted.txt", "deleted", mtime=old)
    env.write(env.sd / "Music" / "song.flac", b"\x00\x01" * 1000, mtime=old)
    env.write(env.home / "Large" / "Pictures" / "Pictures" / "new" / "trip" / "a" / "DSC1.ARW", b"raw1")
    env.write(env.sd / "Large" / "Pictures" / "Pictures" / "new" / "trip" / "DSC2.arw", b"raw2")
    env.write(env.sd / "Large" / "Pictures" / "Pictures" / "new" / "trip" / "DSC2.xmp", "sidecar")
    env.write(env.home / "Large" / "Pictures" / "Pictures" / "new" / "other" / "DSC3.ARW", b"raw3")
    env.init_primary()
    env.init_secondary()
    assert cli.main(["backup-files"]) == 0
    assert cli.main(["update-secondary"]) == 0
    return env


def change_files(env):
    docs = env.home / "Documents"
    env.write(docs / "modified.txt", "after, longer")
    env.write(docs / "new.txt", "new")
    (docs / "moved").mkdir()
    (docs / "to-move.txt").rename(docs / "moved" / "to-move.txt")  # keeps size and mtime
    (docs / "deleted.txt").unlink()
    shutil.copy2(env.sd / "Music" / "song.flac", env.sd / "Music" / "copy of song.flac")  # also same size, mtime
    env.write(env.sd / "Music" / "new song.flac", b"\x02" * 500)


def cloud_files(env, snapshot="latest"):
    return env.snapshot_files(env.cloud, snapshot)


def test_delta_holds_new_and_modified_but_not_moved(synced):
    change_files(synced)
    assert cli.main(["backup-files"]) == 0
    assert cli.main(["backup-delta"]) == 0
    assert cloud_files(synced) == {"home/Documents/modified.txt", "home/Documents/new.txt", "sd/Music/new song.flac"}


def test_delta_tagged_with_base_snapshot(synced):
    change_files(synced)
    cli.main(["backup-files"])
    cli.main(["backup-delta"])
    base = json.loads(repos.secondary_state_path().read_text(encoding="utf-8"))["snapshot"]
    [snapshot] = snapshots(synced.cloud)
    assert snapshot["tags"] == [f"delta-from-{base[:8]}"]


def plain_copies(env):
    """{path relative to CLOUD_PICTURES: content} of the plain copies."""
    return {path.relative_to(env.cloud_pictures).as_posix(): path.read_bytes()
            for path in env.cloud_pictures.rglob("*") if path.is_file()}


def test_delta_copies_album_arw_files_plainly_from_all_album_trees(synced):
    synced.write_settings(delta_albums=["trip"])
    synced.write(synced.home / "Documents" / "new.txt", "new")
    cli.main(["backup-files"])
    assert cli.main(["backup-delta"]) == 0
    assert plain_copies(synced) == {"trip/a/DSC1.ARW": b"raw1", "trip/DSC2.arw": b"raw2"}
    assert cloud_files(synced) == {"home/Documents/new.txt"}, "the restic cloud repo gets no raws"


def test_cloud_repo_gets_only_what_backup_files_covers(synced):
    """However the albums are configured, the restic cloud repo holds a subset of the primary's latest
    snapshot, so backup-delta and backup-files include and exclude the same files."""
    synced.write_settings(delta_albums=["trip", "other"])
    change_files(synced)
    synced.write(synced.home / "Large" / "Pictures" / "Pictures" / "new" / "trip" / "DSC9.ARW", b"raw9")
    synced.write(synced.home / "Downloads" / "setup.exe", "excluded")
    cli.main(["backup-files"])
    assert cli.main(["backup-delta"]) == 0
    assert cloud_files(synced) <= synced.snapshot_files(synced.repo)
    assert not any(path.lower().endswith(".arw") for path in cloud_files(synced))


def test_delta_album_in_subfolder(synced):
    synced.write_settings(delta_albums=["trip\\a"])
    cli.main(["backup-files"])
    assert cli.main(["backup-delta"]) == 0
    assert plain_copies(synced) == {"trip/a/DSC1.ARW": b"raw1"}
    assert not synced.cloud.exists(), "nothing new for restic, so no cloud repo"


def test_delta_album_in_new_old(synced):
    synced.write(synced.home / "Large" / "Pictures" / "Pictures" / "new-old" / "party" / "fest" / "X.ARW", b"old")
    synced.write_settings(delta_albums=["party\\fest"])
    assert cli.main(["backup-delta"]) == 0
    assert plain_copies(synced) == {"party/fest/X.ARW": b"old"}


def test_delta_copies_only_missing_and_never_overwrites(synced):
    synced.write_settings(delta_albums=["trip"])
    cli.main(["backup-delta"])
    copy = synced.cloud_pictures / "trip" / "DSC2.arw"
    os.utime(copy, (1_000_000_000, 1_000_000_000))
    synced.write(synced.home / "Large" / "Pictures" / "Pictures" / "new" / "trip" / "a" / "DSC4.ARW", b"raw4")
    assert cli.main(["backup-delta"]) == 0
    assert os.path.getmtime(copy) == 1_000_000_000, "the existing copy was not touched"
    assert plain_copies(synced)["trip/a/DSC4.ARW"] == b"raw4"


def test_delta_refuses_existing_copy_of_other_size(synced):
    synced.write_settings(delta_albums=["trip"])
    synced.write(synced.cloud_pictures / "trip" / "DSC2.arw", b"different")
    with pytest.raises(SystemExit, match="already exists with a different size"):
        cli.main(["backup-delta"])
    assert (synced.cloud_pictures / "trip" / "DSC2.arw").read_bytes() == b"different"
    assert not (synced.cloud_pictures / "trip" / "a").exists(), "checked before copying anything"


def test_delta_refuses_album_differing_between_trees(synced):
    synced.write(synced.sd / "Large" / "Pictures" / "Pictures" / "new" / "trip" / "a" / "DSC1.ARW", b"other size")
    synced.write_settings(delta_albums=["trip"])
    with pytest.raises(SystemExit, match="differ, but would both be copied to the same place"):
        cli.main(["backup-delta"])


def test_delta_replaces_leftover_part_file(synced):
    synced.write_settings(delta_albums=["trip"])
    synced.write(synced.cloud_pictures / "trip" / "DSC2.arw.part", b"interrupted")
    assert cli.main(["backup-delta"]) == 0
    assert plain_copies(synced) == {"trip/a/DSC1.ARW": b"raw1", "trip/DSC2.arw": b"raw2"}


def test_delta_missing_album_refuses(synced):
    synced.write_settings(delta_albums=["no such album"])
    with pytest.raises(SystemExit, match="Album no such album not found"):
        cli.main(["backup-delta"])
    assert not synced.cloud.exists()
    assert not synced.cloud_pictures.exists()


def test_delta_skips_files_deleted_since_the_snapshot(synced):
    synced.write(synced.home / "Documents" / "fleeting.txt", "x")
    cli.main(["backup-files"])
    (synced.home / "Documents" / "fleeting.txt").unlink()
    synced.write(synced.home / "Documents" / "stays.txt", "y")
    cli.main(["backup-files"])
    assert cli.main(["backup-delta"]) == 0
    assert cloud_files(synced) == {"home/Documents/stays.txt"}


def test_delta_compares_against_secondary_state_not_previous_snapshot(synced):
    synced.write(synced.home / "Documents" / "first.txt", "1")
    cli.main(["backup-files"])
    synced.write(synced.home / "Documents" / "second.txt", "2")
    cli.main(["backup-files"])
    assert cli.main(["backup-delta"]) == 0
    assert cloud_files(synced) == {"home/Documents/first.txt", "home/Documents/second.txt"}


def test_delta_repeated_runs_add_snapshots_to_existing_cloud_repo(synced):
    synced.write(synced.home / "Documents" / "first.txt", "1")
    cli.main(["backup-files"])
    cli.main(["backup-delta"])
    synced.write(synced.home / "Documents" / "second.txt", "2")
    cli.main(["backup-files"])
    assert cli.main(["backup-delta"]) == 0
    assert len(snapshots(synced.cloud)) == 2
    assert cloud_files(synced) == {"home/Documents/first.txt", "home/Documents/second.txt"}


def test_delta_without_changes(synced, capfd):
    cli.main(["backup-files"])
    assert cli.main(["backup-delta"]) == 0
    assert "Nothing to back up" in capfd.readouterr().out
    assert not synced.cloud.exists()


def test_delta_needs_secondary_state(synced):
    repos.secondary_state_path().unlink()
    with pytest.raises(SystemExit, match="secondary-latest-snapshot.json is missing"):
        cli.main(["backup-delta"])


def test_delta_accepts_hand_written_state(synced):
    [first] = snapshots(synced.repo)
    repos.secondary_state_path().write_text(json.dumps({"snapshot": first["short_id"]}), encoding="utf-8")
    synced.write(synced.home / "Documents" / "new.txt", "new")
    cli.main(["backup-files"])
    assert cli.main(["backup-delta"]) == 0
    assert cloud_files(synced) == {"home/Documents/new.txt"}


def test_delta_refuses_when_not_enough_space(synced, monkeypatch, capfd):
    synced.write(synced.home / "Documents" / "big.bin", b"b" * 10_000)
    cli.main(["backup-files"])
    monkeypatch.setattr(delta, "free_space", lambda path: 9_999)
    with pytest.raises(SystemExit, match="Not enough space"):
        cli.main(["backup-delta"])
    assert not synced.cloud.exists()


def test_delta_space_check_counts_plain_copies(synced, monkeypatch):
    synced.write_settings(delta_albums=["trip"])  # 8 bytes of raws
    monkeypatch.setattr(delta, "free_space", lambda path: 7)
    with pytest.raises(SystemExit, match="Not enough space"):
        cli.main(["backup-delta"])
    assert not synced.cloud_pictures.exists()
    monkeypatch.setattr(delta, "free_space", lambda path: 8)
    assert cli.main(["backup-delta"]) == 0


def test_delta_space_check_counts_existing_cloud_repo(synced, monkeypatch):
    synced.write(synced.home / "Documents" / "big.bin", b"b" * 10_000)
    cli.main(["backup-files"])
    cli.main(["backup-delta"])
    cloud_size = delta.folder_size(synced.cloud)
    # The same files again: what is already in the cloud repo is deduplicated, so little space is enough
    monkeypatch.setattr(delta, "free_space", lambda path: max(0, 10_000 - cloud_size) + 1)
    assert cli.main(["backup-delta"]) == 0


def test_free_space_of_cloud_drive(env):
    assert delta.free_space(env.cloud) == shutil.disk_usage(env.root.anchor).free


def test_recovery_from_secondary_plus_cloud_delta(synced, tmp_path):
    """The documented recovery: restore the secondary's snapshot, then the cloud repo's latest on top. All
    content of the primary's latest snapshot comes back; moved files at their old place."""
    change_files(synced)
    cli.main(["backup-files"])
    cli.main(["backup-delta"])
    target = tmp_path / "recovered"
    state = json.loads(repos.secondary_state_path().read_text(encoding="utf-8"))
    [secondary_snapshot] = [s for s in snapshots(synced.secondary_repo) if s["time"] == state["time"]]
    for repo, snapshot in [(synced.secondary_repo, secondary_snapshot["id"]), (synced.cloud, "latest")]:
        for root in (synced.home, synced.sd):
            assert repos.restic("restore", f"{snapshot}:{restic_path(root)}", "--target", str(target / root.name),
                                repo=str(repo)) == 0
    assert (target / "home" / "Documents" / "modified.txt").read_text() == "after, longer"
    assert (target / "home" / "Documents" / "new.txt").read_text() == "new"
    assert (target / "home" / "Documents" / "unchanged.txt").read_text() == "unchanged"
    assert (target / "home" / "Documents" / "to-move.txt").read_text() == "moving content"
    assert (target / "sd" / "Music" / "new song.flac").read_bytes() == b"\x02" * 500
    assert (target / "sd" / "Music" / "song.flac").read_bytes() == b"\x00\x01" * 1000


# snapshot_files/new_files with restic's output faked, for what real restic can't be made to produce here

def ls_line(path, size, mtime, type="file"):
    return json.dumps({"message_type": "node", "struct_type": "node", "type": type, "path": path,
                       "size": size, "mtime": mtime})


def fake_ls(monkeypatch, snapshots_by_id):
    def restic_output(*args, repo=None):
        assert args[:2] == ("ls", "--json")
        header = json.dumps({"message_type": "snapshot", "id": args[2]})
        return "\n".join([header, *snapshots_by_id[args[2]]]) + "\n"
    monkeypatch.setattr(repos, "restic_output", restic_output)


def test_new_files_ignores_utc_offset_change(monkeypatch):
    # The same instant, written with the offset of summer time in base and winter time in target
    fake_ls(monkeypatch, {
        "base": [ls_line("/C/Users/Senso/a.txt", 5, "2026-07-01T12:00:00.5+02:00")],
        "target": [ls_line("/C/Users/Senso/a.txt", 5, "2026-07-01T11:00:00.5+01:00"),
                   ls_line("/C/Users/Senso/b.txt", 5, "2026-07-01T12:00:00.5+01:00")],
    })
    assert list(delta.new_files("base", "target")) == [R"C:\Users\Senso\b.txt"]


def test_new_files_moved_and_modified(monkeypatch):
    fake_ls(monkeypatch, {
        "base": [ls_line("/C/x/old.txt", 5, "2026-07-01T12:00:00+02:00"),
                 ls_line("/S/m.txt", 5, "2026-07-01T12:00:00+02:00")],
        "target": [ls_line("/C/y/moved.txt", 5, "2026-07-01T12:00:00+02:00"),
                   ls_line("/S/m.txt", 6, "2026-07-01T12:00:00+02:00"),
                   ls_line("/S/touched.txt", 5, "2026-07-01T12:00:01+02:00"),
                   ls_line("/S/dir", 0, "2026-07-01T12:00:00+02:00", type="dir")],
    })
    assert sorted(delta.new_files("base", "target")) == [R"S:\m.txt", R"S:\touched.txt"]


def test_snapshot_files_of_real_snapshot(env):
    env.init_primary()
    env.write(env.home / "a.txt", "12345", mtime=1_700_000_000.25)
    (env.home / "dir").mkdir()
    repos.restic("backup", str(env.home))
    files = delta.snapshot_files("latest")
    assert files == {restic_path(env.home / "a.txt"): (5, 1_700_000_000.25)}
