"""Finding the restic repos, running restic on them, and the repo-level subcommands."""

from __future__ import annotations

import datetime
import functools
import importlib.resources
import json
import os
import stat
import string
import subprocess
import sys
import zipfile
from pathlib import Path

from backup import config


def drives():
    """The roots of all drives that exist."""
    return [Path(f"{letter}:\\") for letter in string.ascii_uppercase if os.path.exists(f"{letter}:\\")]


@functools.cache
def find_repo_path(marker_name, repo_dir, exit_on_error=True):
    found = [drive for drive in drives() if (drive / marker_name).exists()]
    if len(found) == 1:
        return str(found[0] / repo_dir)
    if exit_on_error:
        sys.exit(f"Expected exactly one drive with a {marker_name} file in its root, "
                 f"found: {', '.join(str(drive) for drive in found) or 'none'}")
    return None


def repo_path(exit_on_error=True):
    return find_repo_path(config.REPO_MARKER_NAME, config.REPO_DIR, exit_on_error)


def secondary_repo_path(exit_on_error=True):
    return find_repo_path(config.SECONDARY_REPO_MARKER_NAME, config.SECONDARY_REPO_DIR, exit_on_error)


def path_for_help(marker_name, repo_dir):
    return find_repo_path(marker_name, repo_dir, exit_on_error=False) or f"<drive with {marker_name}>:\\{repo_dir}"


def restic_command(args, repo):
    return ["restic", *args, "--repo", repo, "--insecure-no-password"]


def restic(*args, repo=None):
    """Run restic with the given arguments on repo (default: the primary repo), return its exit code."""
    return subprocess.run(restic_command(args, repo or repo_path())).returncode


def restic_output(*args, repo=None):
    """Run restic with the given arguments on repo (default: the primary repo), return its stdout."""
    return subprocess.run(restic_command(args, repo or repo_path()), capture_output=True, text=True,
                          encoding="utf-8", check=True).stdout


def require_repo(path, init_option):
    if not (Path(path) / "config").exists():
        sys.exit(f"No restic repository at {path}. To create a new one: backup {init_option}")


def chunker_polynomial(repo):
    return json.loads(restic_output("cat", "config", repo=repo))["chunker_polynomial"]


def drive_of(repo):
    """The root of the drive repo lives on: the folder above it with a repo marker file."""
    for folder in Path(repo).parents:
        if (folder / config.REPO_MARKER_NAME).exists() or (folder / config.SECONDARY_REPO_MARKER_NAME).exists():
            return folder
    sys.exit(f"No folder above {repo} has a {config.REPO_MARKER_NAME} or {config.SECONDARY_REPO_MARKER_NAME} file")


def copy_self_to_drive_of(repo):
    """Write this tool as zipapp to the root of repo's drive, so a restore works without the project:
    python backup.pyz restic ..."""
    target = drive_of(repo) / config.SELF_COPY_NAME
    if target in Path(__file__).parents:
        return  # running from that very copy, e.g. during a restore
    modules = {module.name: module.read_bytes() for module in importlib.resources.files("backup").iterdir()
               if module.name.endswith(".py")}
    if target.exists():
        target.chmod(stat.S_IWRITE)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("__main__.py", "from backup.cli import main\n\nraise SystemExit(main())\n")
        for name in sorted(modules):
            archive.writestr(f"backup/{name}", modules[name])
    target.chmod(stat.S_IREAD)


def restic_init():
    if Path(repo_path()).exists():
        sys.exit(f"Not initializing: {repo_path()} already exists.")
    copy_self_to_drive_of(repo_path())
    print(f"Initializing new restic repository at {repo_path()}")
    return restic("init", "--compression", "off")


def restic_init_secondary():
    if Path(secondary_repo_path()).exists():
        sys.exit(f"Not initializing: {secondary_repo_path()} already exists.")
    require_repo(repo_path(), "init")
    copy_self_to_drive_of(secondary_repo_path())
    print(f"Initializing new secondary restic repository at {secondary_repo_path()}, "
          f"with the chunker parameters of {repo_path()}")
    return restic("init", "--from-repo", repo_path(), "--from-insecure-no-password", "--copy-chunker-params",
                  repo=secondary_repo_path())


def secondary_state_path():
    return Path(repo_path()).parent / config.SECONDARY_STATE_NAME


def restic_snapshot(snapshot_id):
    """The snapshot of the primary repo with the given ID (or 'latest') as dict, see restic snapshots --json."""
    snapshots = json.loads(restic_output("snapshots", "--json", snapshot_id))
    if not snapshots:
        sys.exit(f"No snapshot {snapshot_id} in {repo_path()}")
    return snapshots[-1]


def restic_update_secondary():
    require_repo(repo_path(), "init")
    require_repo(secondary_repo_path(), "init-secondary")
    if chunker_polynomial(repo_path()) != chunker_polynomial(secondary_repo_path()):
        sys.exit(f"{secondary_repo_path()} has different chunker parameters than {repo_path()}, "
                 "so copying would not deduplicate and inflate it. Recreate it with init-secondary.")
    copy_self_to_drive_of(secondary_repo_path())
    latest = restic_snapshot("latest")
    print(f"Copying new snapshots from {repo_path()} to {secondary_repo_path()} ...")
    if exit_code := restic("copy", "--from-repo", repo_path(), "--from-insecure-no-password", "--compression", "off",
                           repo=secondary_repo_path()):
        return exit_code
    state = {"snapshot": latest["id"], "time": latest["time"], "copied": datetime.datetime.now().astimezone().isoformat()}
    secondary_state_path().write_text(json.dumps(state, indent=2), encoding="utf-8")
    return 0


def restic_passthrough(restic_args, repo):
    copy_self_to_drive_of(repo)
    return restic(*restic_args, repo=repo)
