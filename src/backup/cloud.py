"""update-cloud: back up to the cloud what the secondary repo lacks."""

from __future__ import annotations

import datetime
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from backup import albums, config, repos


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


def plural(count, noun):
    return f"{count} {noun}{'' if count == 1 else 's'}"


def free_space(path):
    return shutil.disk_usage(Path(path).anchor).free


def restic_update_cloud():
    repos.require_repo(repos.repo_path(), "init")
    state_path = repos.secondary_state_path()
    if not state_path.exists():
        sys.exit(f"{state_path} is missing, so the newest snapshot the secondary repo has is unknown. "
                 "Run update-secondary, or write it by hand: {\"snapshot\": \"<ID>\"}")
    base = repos.restic_snapshot(json.loads(state_path.read_text(encoding="utf-8"))["snapshot"])
    latest = repos.restic_snapshot("latest")
    print(f"Collecting for the cloud repo the files of snapshot {latest['short_id']} ({latest['time'][:16]}) "
          f"that are new compared to the secondary's {base['short_id']} ({base['time'][:16]}) ...")
    files = {path for path in new_files(base["id"], latest["id"]) if os.path.isfile(path)}
    cloud_repo = config.CLOUD_REPO
    # An upper bound: restic deduplicates and compresses. Too high only makes albumflow delete more rejected photos.
    size = sum(os.path.getsize(path) for path in files)
    print(f"For {cloud_repo}: {plural(len(files), 'file')}, at most {size / 2**30:.1f} GiB.")
    # First the raws of the albums the secondary lacks, which restic excludes: albumflow keeps room for the repo
    # part, deleting rejected photos from the cloud if needed
    albumflow_exit_code = albums.albumflow("update-cloud", f"--room-for={size}")
    free = free_space(cloud_repo)
    print(f"{free / 2**30:.1f} GiB free on {Path(cloud_repo).anchor}")
    if size > free:
        sys.exit(f"Not enough space on {Path(cloud_repo).anchor}")
    if files:
        exit_code = backup_to_cloud_repo(files, base)
    else:
        print("Nothing new for the cloud repo.")
        exit_code = 0
    return albumflow_exit_code or exit_code


def backup_to_cloud_repo(files, base):
    cloud_repo = config.CLOUD_REPO
    if not Path(cloud_repo).exists():
        print(f"Initializing new cloud restic repository at {cloud_repo}")
        if exit_code := repos.restic("init", repo=cloud_repo):
            return exit_code
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as files_from:
        files_from.write("\n".join(sorted(files)))
    try:
        # No --verbose: restic would print all the thousands of paths of files_from, twice
        print(f"Backing up {plural(len(files), 'file')} into {cloud_repo}:")
        return repos.restic("backup", "--files-from-verbatim", files_from.name, "--tag", f"delta-from-{base['short_id']}",
                            repo=cloud_repo)
    finally:
        os.unlink(files_from.name)
