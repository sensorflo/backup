"""backup-delta: back up to the cloud what the secondary repo lacks."""

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
    """{path relative to the album: source path} of the .arw files of album (relative to the album trees),
    in whichever album trees have it."""
    folders = [Path(tree, album) for tree in config.album_trees() if Path(tree, album).is_dir()]
    if not folders:
        sys.exit(f"Album {album} not found in any of {', '.join(config.album_trees())}")
    files = {}
    for folder in folders:
        for path in sorted(folder.rglob("*")):
            if path.suffix.lower() != ".arw" or not path.is_file():
                continue
            relative = path.relative_to(folder)
            if relative in files and files[relative].stat().st_size != path.stat().st_size:
                sys.exit(f"{files[relative]} and {path} differ, but would both be copied to the same place")
            files.setdefault(relative, path)
    return files


def plain_copies_todo(albums):
    """(album, source, destination) of the albums' .arw files that are not yet in CLOUD_PICTURES. Never
    overwrites."""
    todo = []
    for album in albums:
        for relative, source in sorted(album_arw_files(album).items()):
            dest = Path(config.CLOUD_PICTURES, album, relative)
            if not dest.exists():
                todo.append((album, source, dest))
            elif dest.stat().st_size != source.stat().st_size:
                sys.exit(f"{dest} already exists with a different size than {source}")
    return todo


def copy_file(source, dest):
    """Copy via a .part file, so an interrupted copy never has the final name."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    shutil.copy2(source, part)
    part.replace(dest)


def copy_plainly(copies):
    """Copy the (album, source, destination) of plain_copies_todo: one line per album, and in a terminal a
    progress line updated in place, instead of a line per file."""
    total = sum(source.stat().st_size for _, source, _ in copies)
    done = 0
    for album in dict.fromkeys(album for album, _, _ in copies):
        files = [source for name, source, _ in copies if name == album]
        print(f"  {album}: {plural(len(files), '.arw file')}, {sum(f.stat().st_size for f in files) / 2**30:.1f} GiB")
    for i, (_, source, dest) in enumerate(copies, 1):
        copy_file(source, dest)
        done += source.stat().st_size
        if sys.stdout.isatty():
            print(f"\r  copied {i}/{len(copies)}, {done / 2**30:.1f} of {total / 2**30:.1f} GiB", end="", flush=True)
    if sys.stdout.isatty():
        print()
    print(f"Copied {plural(len(copies), '.arw file')}, {total / 2**30:.1f} GiB, to {config.CLOUD_PICTURES}")


def folder_size(folder):
    return sum(path.stat().st_size for path in Path(folder).rglob("*") if path.is_file())


def plural(count, noun):
    return f"{count} {noun}{'' if count == 1 else 's'}"


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
    print(f"Collecting for the cloud repo the files of snapshot {latest['short_id']} ({latest['time'][:16]}) "
          f"that are new compared to the secondary's {base['short_id']} ({base['time'][:16]}) ...")
    files = {path for path in new_files(base["id"], latest["id"]) if os.path.isfile(path)}
    print(f"Collecting for plain copies the .arw files of {plural(len(albums), 'album')} in delta_albums ...")
    copies = plain_copies_todo(albums)
    cloud_repo = config.CLOUD_REPO
    size = sum(os.path.getsize(path) for path in files)
    # Upper bound: what earlier runs already uploaded is deduplicated
    restic_needed = max(0, size - (folder_size(cloud_repo) if Path(cloud_repo).exists() else 0))
    copies_size = sum(source.stat().st_size for _, source, _ in copies)
    print(f"For {cloud_repo}: {plural(len(files), 'file')}, {size / 2**30:.1f} GiB, of which at most "
          f"{restic_needed / 2**30:.1f} GiB new.")
    print(f"For {config.CLOUD_PICTURES}: {plural(len(copies), '.arw file')} not yet there, "
          f"{copies_size / 2**30:.1f} GiB.")
    needed = {}
    for path, amount in ((cloud_repo, restic_needed), (config.CLOUD_PICTURES, copies_size)):
        needed[Path(path).anchor] = needed.get(Path(path).anchor, 0) + amount
    for anchor, amount in needed.items():
        free = free_space(anchor)
        print(f"{free / 2**30:.1f} GiB free on {anchor}")
        if amount > free:
            sys.exit(f"Not enough space on {anchor}")
    if not files and not copies:
        print("Nothing to back up.")
        return 0
    exit_code = backup_to_cloud_repo(files, base) if files else 0
    if copies:
        print(f"Copying to {config.CLOUD_PICTURES}:")
        copy_plainly(copies)
    return exit_code


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
