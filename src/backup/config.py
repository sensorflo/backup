"""What gets backed up where. Paths are built from HOME and SD at call time, so tests can point them elsewhere."""
#
# == Where programs keep their data
# Legend:
# + backed up
# - intentionally not backed up (regenerable or recreated by reinstall)
#
# FastRawViewer
# - Local\LibRaw LLC\FastRawViewer: thumbnail DB, shader cache
# - its settings, in the registry (HKCU\Software\LibRaw LLC), see NTUSER.DAT exclude below
#
# darktable (ansel has same layout)
# + Local\darktable: library.db (~430MB), data.db, darktablerc, styles, lua
# - Local\darktable\*.db-snp-*, *.db-pre-*: DB snapshots/copies, ~23GB. restic has history.
# - Local\Microsoft\Windows\INetCache\darktable: mipmaps (thumbnails), OpenCL kernels
#
# Lightroom
# + Large\Pictures\Lightroom\Lightroom Catalog.lrcat (~2.7GB) and .lrcat-data (~39GB)
# + Roaming\Adobe\Lightroom: presets, templates, plugins, preferences
# + Roaming\Adobe\CameraRaw: presets, profiles
# - Large\Pictures\Lightroom\*Previews.lrdata, **\*.acr
# - Roaming\Adobe\CameraRaw\ModelZoo: downloaded AI models, ~1GB
# - Local\Adobe\Lightroom, Local\Adobe\CameraRaw: caches, logs
#
# digikam
# + Large\Pictures\digikam4.db (~550MB), recognition.db, similarity.db
# + Local\digikamrc, Local\digikam_systemrc
# - Large\Pictures\thumbnails-digikam.db (~21GB), Local\digikam: cache, marble map data
#
# VS Code
# + Roaming\Code\User: settings, keybindings, snippets
# - Roaming\Code\{Cache,CachedData,logs,...}, .vscode\extensions (reinstalled by sync)
#
# Chrome
# - Local\Google\Chrome\User Data: everything, relying on Google account sync
#
# Firefox
# + Roaming\Mozilla\Firefox: profiles with bookmarks, passwords, history, extensions (~280MB)
# - Local\Mozilla\Firefox: cache
#
# EMDB        + Roaming\EMDB: emdb.dat and downloaded posters, covers, actor images (~650MB)
# Notepad++   + Roaming\Notepad++: config, session, unsaved tabs (backup\), plugins
# SmartGit    + Roaming\syntevo, - its updates, logs and lock files
# Everything, Greenshot, calibre (settings only, the library is elsewhere)
#             + Roaming\<name>, - Local\calibre-cache
# backup      + Roaming\backup: config.json of this tool

from __future__ import annotations

import json
import os
from pathlib import Path

HOME = R"C:\Users\Senso"
# The SD card is semantically an extension of my home folder, so both go into one snapshot.
SD = "S:\\"
SD_MARKER_NAME = ".backup-source-sd"

OWN_REMOTE_PREFIXES = (
    "https://github.com/sensorflo/", "git@github.com:sensorflo/",
    "https://gitlab.com/sensorflo1/", "git@gitlab.com:sensorflo1/",
)
# Names of git repo folders that are skipped: either their remote isn't mine, or they have
# intentionally no remote. Any other git repo without a remote of mine is an error.
GIT_REPOS_WITHOUT_OWN_REMOTE = {
    "cppfront", "googletest", "vcpkg", "SyncArwDng", "console-application-gtest",
}

# Programs that may modify files being backed up. backup-files refuses to run while one is running.
APPS_MODIFYING_FILES = {"darktable.exe", "ansel.exe", "Lightroom.exe", "digikam.exe", "emdb.exe"}

# On each backup drive, the tools (this one and albumflow) own this folder; everything else on the drive,
# e.g. "Backup manual", is managed by hand. Only the marker file lives in the drive's root.
BACKUP_DIR = "Backup"
# The primary repo. Unqualified 'repo' and 'backup' in this tool always mean the primary one.
# Each repo lives on whichever drive has its marker file in its root.
REPO_MARKER_NAME = ".backup-destination"
REPO_DIR = rf"{BACKUP_DIR}\restic-private"
# The secondary repo holds copies of the primary repo's snapshots (restic copy).
SECONDARY_REPO_MARKER_NAME = ".secondary-backup-destination"
SECONDARY_REPO_DIR = rf"{BACKUP_DIR}\restic-private-secondary"
# The cloud repo is a stopgap while the secondary repo is out of reach, e.g. on holiday: it holds the
# files that changed since the newest snapshot the secondary has, plus the .arw files of chosen albums
# in the 'new' trees, which the other repos exclude. Delete it once the secondary is updated again.
# Recovery: restore that snapshot from the secondary, then the cloud repo's latest snapshot on top.
CLOUD_REPO = R"G:\My Drive\Backup\restic-delta-dell-xps-15-home-folder"
# Written by update-secondary next to the primary repo, read by backup-delta: the newest snapshot the
# secondary repo has. JSON: {"snapshot": <ID>, "time": <snapshot time>, "copied": <time of the copy>}
SECONDARY_STATE_NAME = "secondary-latest-snapshot.json"
# The copy of this tool written to BACKUP_DIR of each repo's drive, so a restore works without the project.
SELF_COPY_NAME = "backup.pyz"
# Written next to it: what the drive is for, how to update the secondary repo, how to restore.
DRIVE_README_NAME = "README.md"

# Optional settings. JSON, missing keys get the defaults of DEFAULT_SETTINGS:
#   backup_all_includes_delta: whether backup-all ends with backup-delta
#   delta_albums: albums, relative to the new trees, whose .arw files backup-delta backs up too,
#                 e.g. ["hollidays\\Madeira 2026"]
SETTINGS_PATH = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"), "backup", "config.json")
DEFAULT_SETTINGS = {"backup_all_includes_delta": False, "delta_albums": []}

# Below AppData, only these (relative to AppData) are backed up, everything else there is excluded:
# mostly caches and things reinstalling a program recreates. The includes are databases and settings
# worth carrying over to a new laptop. Restore them before first starting the respective program.
APPDATA_INCLUDES = [
    # Databases. The ones of digikam and Lightroom are in Large\Pictures.
    R"Local\darktable", # also config, styles and lua scripts
    R"Local\ansel",
    R"Roaming\EMDB", # including downloaded posters etc., < 1GB
    # Settings
    R"Roaming\Adobe\Lightroom", # presets, templates, plugins, preferences
    R"Roaming\Adobe\CameraRaw", # presets, profiles
    R"Local\digikamrc",
    R"Local\digikam_systemrc",
    R"Local\RawTherapee\options",
    R"Local\RawTherapee\profiles",
    R"Roaming\Code\User",
    R"Roaming\Mozilla\Firefox", # bookmarks, passwords, extensions, ...
    R"Roaming\syntevo",
    R"Roaming\Notepad++",
    R"Roaming\Everything",
    R"Roaming\Greenshot",
    R"Roaming\calibre",
    R"Roaming\backup", # this tool's settings
]


def _join(root, relative):
    return str(Path(root, relative))


def source_paths():
    return [HOME, SD]


def sd_marker():
    return _join(SD, SD_MARKER_NAME)


def appdata():
    return _join(HOME, "AppData")


def new_trees():
    return [_join(HOME, R"Large\Pictures\Pictures\new"), _join(SD, R"Large\Pictures\Pictures\new")]


def git_search_roots():
    """Git repos below these folders get their branches, stashes and uncommitted changes pushed to
    backup/<computer name>/... branches on their remote, if the remote is mine."""
    return [_join(HOME, "src"), _join(HOME, "bin"), _join(HOME, "Documents")]


def git_repos():
    """Additional single git repos, whose subfolders are not searched for further git repos."""
    return [HOME, _join(HOME, R"Large\Pictures")]


def excludes():
    """Case-insensitive restic exclude patterns (--iexclude)."""
    home, sd = HOME, SD
    return [
        # _new_ .arw files are backed up separately
        _join(home, R"Large\Pictures\Pictures\new\**\*.arw"),
        _join(home, R"Large\Pictures\Pictures\new-old\**\*.arw"),
        _join(home, R"Large\Pictures\Rejected\**\*.arw"),
        _join(sd, R"Large\Pictures\Pictures\new\**\*.arw"),
        _join(sd, R"Large\Pictures\Pictures\new-old\**\*.arw"),
        _join(sd, R"Large\Pictures\Rejected\**\*.arw"),

        # Note that non-raw image files are backed up, even the ones in the 'new'
        # 'new-old' trees. I don't have too many of those.

        # Misc
        R"**\thumbnails-digikam.db",
        R"**\*.acr", # lightroom artifacts
        R"**\.git\**", # backup-vcs pushes what isn't pushed yet. At least we have the working tree.
        _join(home, R"src\**\build*\**"), # TODO: Maybe a tad dangerous in maybe wrongly excluding too much.
        _join(home, "NTUSER.DAT*"),
        R"**\.dtrash\**", # darktable trash

        # Complete folders
        _join(home, R"Large\Pictures\Temp\**"),
        _join(home, R"Large\Pictures\Lightroom\Lightroom Catalog Previews.lrdata\**"),
        _join(home, R"Large\Pictures\Lightroom\Lightroom Catalog Smart Previews.lrdata\**"),
        _join(home, R"CrossDevice\**"),
        _join(home, R"OneDrive\**"),
        _join(home, R"Downloads\**"),
        _join(home, R"src\vcpkg\**"),
        _join(home, R"src\external\**"),
        _join(home, R".vscode\extensions\**"),
        _join(sd, R"rejected\**"),
        _join(sd, R"$RECYCLE.BIN\**"),

        # Excludes from within APPDATA_INCLUDES
        _join(home, R"AppData\Local\*\*.db-snp-*"), # darktable/ansel DB snapshots, ~20GB. restic has history.
        _join(home, R"AppData\Local\*\*.db-pre-*"), # darktable/ansel DB copies from before version upgrades
        _join(home, R"AppData\Roaming\Adobe\CameraRaw\ModelZoo\**"), # downloaded AI models, ~1GB
        _join(home, R"AppData\Roaming\Adobe\CameraRaw\Logs\**"),
        _join(home, R"AppData\Roaming\syntevo\SmartGit\updates\**"),
        # Locked while the program runs, making restic fail on them
        _join(home, R"AppData\Roaming\syntevo\SmartGit\*\logs\**"),
        _join(home, R"AppData\Roaming\syntevo\SmartGit\*\*lock*"),
        _join(home, R"AppData\Roaming\Mozilla\Firefox\Profiles\*\parent.lock"),
    ]


def settings():
    if not SETTINGS_PATH.exists():
        return DEFAULT_SETTINGS
    return DEFAULT_SETTINGS | json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
