"""backup-delta: back up into the cloud repo what the secondary repo lacks."""

from __future__ import annotations

import datetime
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from backup import config, repos


def snapshot_files(snapshot_id):
    """{path: (size, mtime)} of the files in the snapshot of the primary repo with the given ID."""
    out = repos.restic_output("ls", "--json", snapshot_id)
    nodes = (json.loads(line) for line in out.splitlines() if line.strip())
    # mtime as timestamp, since its string has the UTC offset of the backup time, e.g. differing across DST
    return {node["path"]: (node.get("size", 0), datetime.datetime.fromisoformat(node["mtime"]).timestamp())
            for node in nodes if node.get("message_type") == "node" and node.get("type") == "file"}


def new_files(base, target):
    """Paths of the files in snapshot target of the primary repo that have new content compared to snapshot
    base. A file with the same size and mtime as any file in base counts as moved, not new."""
    base_contents = set(snapshot_files(base).values())
    for path, content in snapshot_files(target).items():
        if content not in base_contents:
            yield f"{path[1]}:{path[2:]}".replace("/", "\\") # /C/Users/... -> C:\Users\...


def album_arw_files(album):
    """The .arw files of album (relative to the new trees), in whichever new trees have it."""
    folders = [Path(tree, album) for tree in config.new_trees() if Path(tree, album).is_dir()]
    if not folders:
        sys.exit(f"Album {album} not found in any of {', '.join(config.new_trees())}")
    return [str(path) for folder in folders for path in folder.rglob("*") if path.suffix.lower() == ".arw"]


def folder_size(folder):
    return sum(path.stat().st_size for path in Path(folder).rglob("*") if path.is_file())


def free_space(path):
    return shutil.disk_usage(Path(path).anchor).free


def restic_backup_delta():
    repos.require_repo(repos.repo_path(), "init")
    state_path = repos.secondary_state_path()
    if not state_path.exists():
        sys.exit(f"{state_path} is missing, so the newest snapshot the secondary repo has is unknown. "
                 "Run update-secondary, or write it by hand: {\"snapshot\": \"<ID>\"}")
    base = repos.restic_snapshot(json.loads(state_path.read_text(encoding="utf-8"))["snapshot"])
    albums = config.settings()["delta_albums"]
    latest = repos.restic_snapshot("latest")
    print(f"Collecting files new in snapshot {latest['short_id']} ({latest['time'][:16]}) compared to "
          f"{base['short_id']} ({base['time'][:16]}), and the .arw files of {len(albums)} albums ...")
    files = {path for path in new_files(base["id"], latest["id"]) if os.path.isfile(path)}
    for album in albums:
        files.update(album_arw_files(album))
    cloud_repo = config.CLOUD_REPO
    # Upper bound: what earlier runs already uploaded is deduplicated
    size = sum(os.path.getsize(path) for path in files)
    needed = size - (folder_size(cloud_repo) if Path(cloud_repo).exists() else 0)
    free = free_space(cloud_repo)
    print(f"{len(files)} files, {size / 2**30:.1f} GiB, of which at most {needed / 2**30:.1f} GiB new. "
          f"{free / 2**30:.1f} GiB free on {Path(cloud_repo).anchor}")
    if needed > free:
        sys.exit("Not enough space for the cloud repo.")
    if not files:
        print("Nothing to back up.")
        return 0
    if not Path(cloud_repo).exists():
        print(f"Initializing new cloud restic repository at {cloud_repo}")
        if exit_code := repos.restic("init", repo=cloud_repo):
            return exit_code
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as files_from:
        files_from.write("\n".join(sorted(files)))
    try:
        return repos.restic("backup", "--files-from-verbatim", files_from.name, "--tag", f"delta-from-{base['short_id']}",
                            "--verbose", repo=cloud_repo)
    finally:
        os.unlink(files_from.name)
