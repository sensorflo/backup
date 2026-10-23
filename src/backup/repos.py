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


def snapshot_path(path):
    """How restic names path in a snapshot: C:\\Users\\Senso -> /C/Users/Senso, S:\\ -> /S."""
    posix = Path(path).as_posix()
    return f"/{posix[0]}{posix[2:]}".rstrip("/")


def drive_readme(drive):
    """The text of the readme next to the self copy on drive, in Markdown that also reads well as plain
    text: what the drive is for, how to update the secondary repo, how to restore."""
    pyz = drive / config.BACKUP_DIR / config.SELF_COPY_NAME
    roles = [name for marker, name in [(config.REPO_MARKER_NAME, "primary"),
                                       (config.SECONDARY_REPO_MARKER_NAME, "secondary")]
             if (drive / marker).exists()]
    restores = "\n".join(f"backup restic restore latest:{snapshot_path(source)} --target {source}"
                         for source in config.source_paths())
    home = snapshot_path(config.HOME)
    return rf"""# Backup

This drive holds the {" and ".join(roles)} restic repository of my backup tool (command: `backup`,
project: `{Path(config.HOME, "src", "backup")}`). `{config.SELF_COPY_NAME}` next to this file is a copy of that
tool which needs only Python and restic, not the project: wherever this text says `backup`, you can also
use `python {pyz}`. Both files are rewritten by the tool on each run; don't edit them.

The tools own this `{config.BACKUP_DIR}` folder: backup the restic repositories, albumflow (project:
`{Path(config.HOME, "src", "albumflow")}`) the `pictures-new` folders. Everything else on the drive, e.g.
`Backup manual`, is managed by hand.

## Contents

On the primary drive (marker file in its root: `{config.REPO_MARKER_NAME}`), at home:

- `{Path(config.REPO_DIR).name}`: the primary restic repository.
- `{config.SECONDARY_STATE_NAME}`: the newest snapshot the secondary repository has.
- `pictures-new\to-secondary\<topic>\<album>`: the raw files of unfinished albums (in `Pictures\new`
  or `Pictures\new-old`), which restic excludes. albumflow copies them here.
- `pictures-new\copied-to-secondary\<topic>\<album>`: albums albumflow moved here once copied to the
  secondary.

On the secondary drive (marker file in its root: `{config.SECONDARY_REPO_MARKER_NAME}`), in the office:

- `{Path(config.SECONDARY_REPO_DIR).name}`: copies of the primary repository's snapshots.
- `pictures-new\<topic>\<album>`: raw files of albums, kept as long as there is space. An album may go
  once it is finished, i.e. moved to `Pictures\Originals`, which restic covers.

In the cloud, a stopgap while the secondary is out of reach, written by `backup backup-delta`:

- `{config.CLOUD_REPO}`: restic repository with the files of the primary's latest snapshot that the
  secondary lacks.
- `{config.CLOUD_PICTURES}\<topic>\<album>`: plain copies of the raw files of the albums listed in
  `delta_albums` in `{config.SETTINGS_PATH}`.

## Back up (at home)

```
backup
```

Same as `backup backup-all`: backup-vcs, backup-files, and backup-delta if enabled in
`{config.SETTINGS_PATH}`.

## Update the secondary (in the office)

1. Connect both drives: the primary and the secondary one.
2. Copy the snapshots: `backup update-secondary`
   (or, without the project: `python {pyz} update-secondary`)
3. Check the new snapshots are there: `backup restic-secondary snapshots`
4. Copy the albums' raw files: `albumflow update-secondary` copies each album in the primary's
   `pictures-new\to-secondary` to the secondary's `pictures-new`, then moves it on the primary to
   `pictures-new\copied-to-secondary`. Without albumflow, do that by hand.

On the laptop, `update-secondary` (a script in `~\bin`) does steps 2 and 4, then removes both drives.

Afterwards the cloud repository is no longer needed and can be deleted, and so can the albums in
`{config.CLOUD_PICTURES}` that are now on the secondary.

## Restore

The `restic` and `restic-secondary` subcommands run restic on the primary or secondary repository; add
any restic arguments. A snapshot holds the full source paths, with the drive letter as top folder, so
restore a subtree with `latest:<path>` to avoid extra folder levels. Try `--dry-run` first.

List snapshots, list the files of the latest one:

```
backup restic snapshots
backup restic ls latest
```

Full restore, one command per source:

```
{restores}
```

One folder into a temporary place:

```
backup restic restore "latest:{home}/Documents/some folder" --target C:\restored
```

From the secondary repository: the same with `restic-secondary`.

While the secondary is outdated: restore from the secondary the snapshot named in
`{config.SECONDARY_STATE_NAME}` (next to the primary repository), then the cloud repository's latest
snapshot on top:

```
restic restore latest:{home} --target {config.HOME} --repo "{config.CLOUD_REPO}" --insecure-no-password
```

The raw files of the albums in `{config.CLOUD_PICTURES}` are plain files: copy them back to the album
folders.

## Maintenance

```
backup restic forget --prune --keep-last 5 --keep-monthly 10 --verbose --dry-run
backup restic prune
backup -h
```

`prune` after aborting a backup; `-h` lists all subcommands.
"""


def write_read_only(path, write):
    if path.exists():
        path.chmod(stat.S_IWRITE)
    write(path)
    path.chmod(stat.S_IREAD)


def copy_self_to_drive_of(repo):
    """Write this tool as zipapp to the BACKUP_DIR of repo's drive, so a restore works without the project:
    python backup.pyz restic ..., and next to it a readme."""
    drive = drive_of(repo)
    folder = drive / config.BACKUP_DIR
    folder.mkdir(exist_ok=True)
    write_read_only(folder / config.DRIVE_README_NAME,
                    lambda path: path.write_text(drive_readme(drive), encoding="utf-8", newline="\r\n"))
    target = folder / config.SELF_COPY_NAME
    if target in Path(__file__).parents:
        return  # running from that very copy, e.g. during a restore
    modules = {module.name: module.read_bytes() for module in importlib.resources.files("backup").iterdir()
               if module.name.endswith(".py")}

    def write_zip(path):
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("__main__.py", "from backup.cli import main\n\nraise SystemExit(main())\n")
            for name in sorted(modules):
                archive.writestr(f"backup/{name}", modules[name])

    write_read_only(target, write_zip)


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


def secondary_state_path_for_help():
    repo = path_for_help(config.REPO_MARKER_NAME, config.REPO_DIR)
    folder = repo.rpartition("\\")[0]  # string, not Path: it may be the <drive with ...> placeholder
    return f"{folder}\\{config.SECONDARY_STATE_NAME}"


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
