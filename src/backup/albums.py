"""The raw files of unfinished albums, which restic excludes: albumflow handles them, backup runs it.

albumflow owns Backup\\pictures-new on the primary and secondary drives and its plain copies in the cloud; backup only
decides when that happens, as part of its own commands. A missing albumflow is an error, never skipped.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from backup import config

# Written by albumflow next to backup.pyz on the primary and secondary drive (albumflow's config.SELF_COPY)
ALBUMFLOW_PYZ = "albumflow.pyz"


def albumflow_path():
    return Path(config.HOME, "src", "albumflow", ".venv", "Scripts", "albumflow.exe")


def albumflow_pyz():
    """albumflow's copy of itself on the primary drive, for another computer (e.g. the office laptop)."""
    from backup import repos  # not at the top: repos imports this module
    repo = repos.repo_path(exit_on_error=False)
    return Path(repo).parent / ALBUMFLOW_PYZ if repo else None


def albumflow_command():
    """How to run albumflow: installed (on the laptop), else albumflow.pyz on the primary drive; None if neither."""
    if albumflow_path().exists():
        return [str(albumflow_path())]
    pyz = albumflow_pyz()
    return [sys.executable, str(pyz)] if pyz and pyz.is_file() else None


def albumflow(*args):
    """Run albumflow with the given arguments, return its exit code."""
    command = f"albumflow {' '.join(args)}"
    runner = albumflow_command()
    if runner is None:
        print(f"{command} not run: albumflow not found, neither at {albumflow_path()} nor as {ALBUMFLOW_PYZ} on "
              "the primary drive. The album raw files were not handled; see the README next to backup.pyz on "
              "the primary and secondary drives.", file=sys.stderr)
        return 1
    print(f"Running {command}:", flush=True)
    return subprocess.run([*runner, *args]).returncode
