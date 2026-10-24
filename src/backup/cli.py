"""Command line entry point."""

from __future__ import annotations

import argparse
import sys

import backup
from backup import albums, config, delta, files, repos, vcs


def backup_all():
    vcs_exit_code = vcs.backup_vcs()
    files_exit_code = files.restic_backup_files()
    # The raws of unfinished albums, which restic excludes; before the delta, whose cloud copies take them
    # from the primary
    raw_exit_code = albums.albumflow("backup-raw", "--all", "--no-eject")
    # restic's exit code 3: some files could not be read, but the snapshot was created
    if config.settings()["backup_all_includes_delta"] and files_exit_code in (0, 3):
        delta_exit_code = delta.restic_backup_delta()
        return vcs_exit_code or raw_exit_code or delta_exit_code or files_exit_code
    return vcs_exit_code or raw_exit_code or files_exit_code


RESTIC_PASSTHROUGH_SUBCOMMANDS = {"restic": repos.repo_path, "restic-secondary": repos.secondary_repo_path}


def build_parser():
    settings = config.SETTINGS_PATH
    parser = argparse.ArgumentParser(prog="backup", description=backup.__doc__,
                                     epilog="backup <subcommand> -h shows the help of a subcommand.\n\n"
                                            "(Primary) Repository: "
                                            f"{repos.path_for_help(config.REPO_MARKER_NAME, config.REPO_DIR)}\n"
                                            "Secondary repository: "
                                            f"{repos.path_for_help(config.SECONDARY_REPO_MARKER_NAME, config.SECONDARY_REPO_DIR)}\n"
                                            f"Cloud repository: {config.CLOUD_REPO}\n"
                                            f"Cloud album raws: {config.CLOUD_PICTURES}\n"
                                            f"Settings: {settings}",
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="subcommand", metavar="<subcommand>",
                                help="one of the following, default: backup-all")

    def add(name, func, help, details=""):
        """details: only in the subcommand's own help, not in the list of subcommands."""
        # argparse %-formats help, but not description
        sub.add_parser(name, help=help.replace("%", "%%"),
                       description=f"{help} {details}".strip()).set_defaults(func=func)

    settings_for_help = config.settings_path_for_help()
    add("backup-all", backup_all,
        "backup-vcs, then backup-files, then albumflow backup-raw --all, then backup-delta if "
        f"backup_all_includes_delta in {settings_for_help}")
    add("backup-files", files.restic_backup_files,
        "back up files into the repo; doesn't include albumflow backup-raw (backup-all does)",
        "The raws of unfinished albums (Pictures\\new, new-old), which restic excludes, are copied to the backup "
        "drive by albumflow: when importing them, by albumflow backup-raw, or by backup-all running "
        "albumflow backup-raw --all.")
    add("backup-vcs", vcs.backup_vcs,
        "push branches, stashes and uncommitted changes of my version-controlled repos (currently git only) "
        "to backup branches")
    add("backup-delta", delta.restic_backup_delta,
        "back up into the cloud repo the files of the primary repo's latest snapshot that the secondary repo "
        "lacks; includes albumflow update-cloud, which copies the album raws the secondary lacks to the cloud. "
        "Run it right after backup-files.",
        f"What the secondary repo has is tracked in {repos.secondary_state_path_for_help()}, written by "
        f"update-secondary. albumflow copies the albums in the primary's Backup\\pictures-new\\to-secondary, and "
        f"the photos of albums not yet on the primary from the laptop, to {config.CLOUD_PICTURES}\\<topic>\\<album>; "
        "existing copies are never overwritten or deleted. "
        f"backup-all runs it while backup_all_includes_delta is set in {settings_for_help}.")
    add("init", repos.restic_init, "create a new, empty repo")
    add("init-secondary", repos.restic_init_secondary,
        "create a new, empty secondary repo with the primary repo's chunker parameters")
    add("update-secondary", repos.restic_update_secondary,
        "copy snapshots missing in the secondary repo from the primary repo, then run albumflow "
        "update-secondary, which copies the album raws the secondary lacks and removes both drives")
    # Only for the help: main() passes their arguments to restic before argparse could reject restic's options
    sub.add_parser("restic", help="run restic with the remaining arguments on the repo")
    sub.add_parser("restic-secondary", help="run restic with the remaining arguments on the secondary repo")
    return parser


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in RESTIC_PASSTHROUGH_SUBCOMMANDS:
        return repos.restic_passthrough(argv[1:], RESTIC_PASSTHROUGH_SUBCOMMANDS[argv[0]]())
    args = build_parser().parse_args(argv)
    return args.func() if args.subcommand else backup_all()
