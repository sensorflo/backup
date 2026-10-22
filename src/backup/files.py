"""backup-files: back up the home folder and the SD card into the primary repo."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from backup import config, repos


def running_apps(names):
    tasklist = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, check=True).stdout
    running = {line.split(",")[0].strip('"').lower() for line in tasklist.splitlines() if line}
    return sorted(name for name in names if name.lower() in running)


def excludes_all_but(folder, includes):
    """Exclude patterns for everything below folder except the includes (tuples of path parts relative to
    folder) and their contents."""
    patterns = []
    for child in folder.iterdir():
        below = [parts[1:] for parts in includes if parts[0].lower() == child.name.lower()]
        if not below:
            # restic patterns can't escape on Windows, so let ? match the special characters literally
            patterns.append(str(child).translate(str.maketrans("*[", "??")))
        elif all(below) and child.is_dir():
            patterns += excludes_all_but(child, below)
    return patterns


def exclude_patterns():
    appdata = Path(config.appdata())
    allowlist = excludes_all_but(appdata, [Path(include).parts for include in config.APPDATA_INCLUDES]) \
        if appdata.is_dir() else []
    return config.excludes() + allowlist


def restic_backup_files():
    repos.require_repo(repos.repo_path(), "init")
    if not Path(config.sd_marker()).exists():
        sys.exit(f"SD card not found: {config.sd_marker()} is missing (card not inserted, or a different "
                 "drive letter?). Not backing up, since the snapshot would be incomplete.")
    if apps := running_apps(config.APPS_MODIFYING_FILES):
        sys.exit(f"Not backing up while {', '.join(apps)} may be modifying files. Close them first.")
    repos.copy_self_to_drive_of(repos.repo_path())
    print(f"Starting backup of {', '.join(config.source_paths())} ...")
    excludes = [f"--iexclude={pattern}" for pattern in exclude_patterns()]
    return repos.restic("backup", *config.source_paths(), *excludes, "--compression", "off", "--verbose")
