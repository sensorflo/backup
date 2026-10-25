"""The raw files of unfinished albums, which restic excludes: albumflow handles them, backup runs it.

albumflow owns Backup\\pictures-new on the primary and secondary drives and its plain copies in the cloud; backup only
decides when that happens, as part of its own commands. A missing albumflow is an error, never skipped.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from backup import config


def albumflow_path():
    return Path(config.HOME, "src", "albumflow", ".venv", "Scripts", "albumflow.exe")


def albumflow(*args):
    """Run albumflow with the given arguments, return its exit code."""
    command = f"albumflow {' '.join(args)}"
    exe = albumflow_path()
    if not exe.exists():
        print(f"{command} not run: albumflow not found at {exe}. The album raw files were not handled; "
              "see the README next to backup.pyz on the primary and secondary drives.", file=sys.stderr)
        return 1
    print(f"Running {command}:", flush=True)
    return subprocess.run([str(exe), *args]).returncode
