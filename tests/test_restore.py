"""Round trips: what backup-files backs up comes back byte-identical from the primary and the secondary repo."""

import hashlib
import os
import random

import pytest

from backup import cli, repos
from conftest import restic_path, snapshots
from test_files import TREE


def contents(root):
    """{relative posix path: sha256} of the files below root."""
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def data(env):
    """TREE plus files whose content restic must not alter: random binary, empty, larger than a chunk,
    CRLF text, odd names, deep nesting."""
    rng = random.Random(42)
    for path, _ in TREE:
        env.write(env.root / path, f"content of {path}")
    env.write(env.home / "Documents" / "random.bin", rng.randbytes(100_000))
    env.write(env.home / "Documents" / "empty.txt", b"")
    env.write(env.home / "Large" / "big.bin", rng.randbytes(9 * 2**20))  # several restic chunks
    env.write(env.home / "Documents" / "crlf.txt", b"line 1\r\nline 2\r\n")
    env.write(env.home / "Documents" / "名前 & spaces; (1) #x.txt", "unicode")
    env.write(env.sd / "a" / "b" / "c" / "d" / "e" / "f" / "deep.txt", "deep")
    env.write(env.home / "Documents" / "old.txt", "old", mtime=946684800)
    (env.home / "empty dir").mkdir()
    env.init_primary()
    assert cli.main(["backup-files"]) == 0
    return env


def restore(repo, snapshot, source, target):
    assert repos.restic("restore", f"{snapshot}:{restic_path(source)}", "--target", str(target), repo=str(repo)) == 0


def expected_contents(env, root):
    """contents(root) minus what backup-files excludes."""
    excluded = {path for path, included in TREE if not included}
    return {path: digest for path, digest in contents(root).items()
            if f"{root.name}/{path}" not in excluded}


@pytest.mark.parametrize("source", ["home", "sd"])
def test_restore_round_trip(data, tmp_path, source):
    root = getattr(data, source)
    target = tmp_path / "restored" / source
    restore(data.repo, "latest", root, target)
    assert contents(target) == expected_contents(data, root)


def test_restore_keeps_mtime_and_empty_dirs(data, tmp_path):
    target = tmp_path / "restored"
    restore(data.repo, "latest", data.home, target)
    assert os.path.getmtime(target / "Documents" / "old.txt") == 946684800
    assert (target / "empty dir").is_dir()


def test_restore_older_snapshot_after_changes(data, tmp_path):
    before = contents(data.home)
    data.write(data.home / "Documents" / "letter.txt", "overwritten")
    (data.home / "Documents" / "random.bin").unlink()
    assert cli.main(["backup-files"]) == 0
    first = snapshots(data.repo)[0]["id"]
    target = tmp_path / "restored"
    restore(data.repo, first, data.home, target)
    assert contents(target) == {path: digest for path, digest in before.items()
                                if f"home/{path}" not in {p for p, included in TREE if not included}}


def test_restore_from_secondary(data, tmp_path):
    data.init_secondary()
    assert cli.main(["update-secondary"]) == 0
    for root in (data.home, data.sd):
        target = tmp_path / "restored" / root.name
        restore(data.secondary_repo, "latest", root, target)
        assert contents(target) == expected_contents(data, root)


def test_repos_pass_restic_check(data):
    data.init_secondary()
    cli.main(["update-secondary"])
    assert repos.restic("check", "--read-data") == 0
    assert repos.restic("check", "--read-data", repo=str(data.secondary_repo)) == 0
