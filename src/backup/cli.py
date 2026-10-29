"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
import textwrap

import backup
from backup import albums, cloud, config, files, repos, vcs


def backup_all():
    settings = config.settings()  # first, so a broken settings file stops before the long work
    vcs_exit_code = vcs.backup_vcs()
    files_exit_code = files.restic_backup_files()
    # The raws of unfinished albums, which restic excludes; before update-cloud, whose cloud copies take them
    # from the primary
    raw_exit_code = albums.albumflow("backup-new", "--all", "--no-eject")
    # At home: deletes from the cloud what reached the secondary drive (in the office) since
    raw_exit_code = albums.albumflow("update-secondary", "--no-eject") or raw_exit_code
    # restic's exit code 3: some files could not be read, but the snapshot was created
    if settings["backup_all_includes_update_cloud"] and files_exit_code in (0, 3):
        cloud_exit_code = cloud.restic_update_cloud()
        return vcs_exit_code or raw_exit_code or cloud_exit_code or files_exit_code
    return vcs_exit_code or raw_exit_code or files_exit_code


RESTIC_PASSTHROUGH_COMMANDS = {"restic": repos.repo_path, "restic-secondary": repos.secondary_repo_path}


class CommandHelpFormatter(argparse.HelpFormatter):
    """For a command's help: wraps at spaces only, so names like backup-files aren't split at their hyphen."""

    def _split_lines(self, text, width):
        return textwrap.wrap(" ".join(text.split()), width, break_on_hyphens=False)

    def _fill_text(self, text, width, indent):
        return textwrap.fill(" ".join(text.split()), width, initial_indent=indent, subsequent_indent=indent,
                             break_on_hyphens=False)


class HelpFormatter(CommandHelpFormatter):
    """For the main help: the description and epilog as written, and the commands listed directly, without a
    line for <command> above them (as git and pip do)."""

    def _fill_text(self, text, width, indent):
        return argparse.RawDescriptionHelpFormatter._fill_text(self, text, width, indent)

    def _format_action(self, action):
        if isinstance(action, argparse._SubParsersAction):
            return "".join(self._format_action(a) for a in action._get_subactions())
        return super()._format_action(action)


def build_parser():
    settings = config.SETTINGS_PATH
    parser = argparse.ArgumentParser(prog="backup", description=backup.__doc__,
                                     usage="backup [-h] [<command>] ...",
                                     epilog="backup <command> -h shows the help of a command.\n"
                                            "An overview of backup and albumflow, with what goes where: the Google "
                                            "Drawing \"backup & albumflow\".\n\n"
                                            "(Primary) Repository: "
                                            f"{repos.path_for_help(config.REPO_MARKER_NAME, config.REPO_DIR)}\n"
                                            "Secondary repository: "
                                            f"{repos.path_for_help(config.SECONDARY_REPO_MARKER_NAME, config.SECONDARY_REPO_DIR)}\n"
                                            f"Cloud repository: {config.CLOUD_REPO}\n"
                                            f"Cloud album raws: {config.CLOUD_PICTURES}, rejected ones: "
                                            f"{config.CLOUD_REJECTED}\n"
                                            f"Settings: {settings}",
                                     formatter_class=HelpFormatter)
    parser._positionals.title = "commands"  # instead of "positional arguments"
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    def add(name, func, help, details=""):
        """details: only in the command's own help, not in the list of commands."""
        # argparse %-formats help, but not description
        sub.add_parser(name, help=help.replace("%", "%%"), formatter_class=CommandHelpFormatter,
                       description=f"{help}{'' if help.endswith('.') else '.'} {details}".strip()
                       if details else help).set_defaults(func=func)

    settings_for_help = config.settings_path_for_help()
    add("backup-all", backup_all,
        "the default, without a command: backup-vcs, then backup-files, then albumflow backup-new --all and "
        "albumflow update-secondary (which tidies up the cloud), then update-cloud if "
        f"backup_all_includes_update_cloud in {settings_for_help}")
    add("backup-files", files.restic_backup_files,
        "back up files into the repo; doesn't include albumflow backup-new (backup-all does)",
        "The raws of unfinished albums (Pictures\\new, new-old), which restic excludes, are copied to the backup "
        "drive by albumflow: when importing them, by albumflow backup-new, or by backup-all running "
        "albumflow backup-new --all.")
    add("backup-vcs", vcs.backup_vcs,
        "push branches, stashes and uncommitted changes of my version-controlled repos (currently git only) "
        "to backup branches")
    add("update-cloud", cloud.restic_update_cloud,
        "back up into the cloud repo the files of the primary repo's latest snapshot that the secondary repo "
        "lacks; includes albumflow update-cloud, which copies the album raws the secondary lacks to the cloud. "
        "Run it right after backup-files.",
        f"What the secondary repo has is tracked in {repos.secondary_state_path_for_help()}, written by "
        f"update-secondary. albumflow copies the albums in the primary's Backup\\pictures-new\\to-secondary, and "
        f"the photos of albums not yet on the primary from the laptop, to {config.CLOUD_PICTURES}\\<topic>\\<album>; "
        f"rejected photos to {config.CLOUD_REJECTED}\\<topic>\\<album>, deleted first when space is short. "
        f"backup-all runs it while backup_all_includes_update_cloud is set in {settings_for_help}.")
    add("init", repos.restic_init, "create a new, empty repo")
    add("init-secondary", repos.restic_init_secondary,
        "create a new, empty secondary repo with the primary repo's chunker parameters")
    add("update-secondary", repos.restic_update_secondary,
        "copy snapshots missing in the secondary repo from the primary repo, then run albumflow "
        "update-secondary for the album raws (see its help); both drives stay connected",
        "Afterwards the cloud is no longer needed as a stand-in for the secondary: unless you want it anyway "
        f"(e.g. the laptop at high risk), set backup_all_includes_update_cloud to false in {settings_for_help}, "
        f"so backup-all no longer runs update-cloud, and delete the cloud repo {config.CLOUD_REPO}.")
    # Only for the help: main() passes their arguments to restic before argparse could reject restic's options
    sub.add_parser("restic", help="run restic with the remaining arguments on the repo")
    sub.add_parser("restic-secondary", help="run restic with the remaining arguments on the secondary repo")
    return parser


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in RESTIC_PASSTHROUGH_COMMANDS:
        return repos.restic_passthrough(argv[1:], RESTIC_PASSTHROUGH_COMMANDS[argv[0]]())
    args = build_parser().parse_args(argv)
    return args.func() if args.command else backup_all()
