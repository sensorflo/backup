"""backup-vcs: push branches, stashes and uncommitted changes of my git repos to backup branches."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

from backup import config


def find_git_repos(root):
    for dirpath, dirnames, filenames in os.walk(root):
        if ".git" in dirnames or ".git" in filenames:
            yield Path(dirpath)
        if ".git" in dirnames:
            dirnames.remove(".git")


def git(git_repo, *args):
    return subprocess.run(["git", "-C", str(git_repo), *args], capture_output=True, text=True, check=True,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}).stdout


def own_remote(git_repo):
    for remote in git(git_repo, "remote").split():
        if git(git_repo, "remote", "get-url", remote).strip().startswith(config.OWN_REMOTE_PREFIXES):
            return remote
    return None


def backup_branch_prefix():
    return f"refs/heads/backup/{platform.node()}/"


def backup_git_repo(git_repo, remote):
    prefix = backup_branch_prefix()
    branches = dict(line.split() for line in
                    git(git_repo, "for-each-ref", "--format=%(refname:lstrip=2) %(objectname)", "refs/heads").splitlines())
    if any(name == "stash" or name.startswith("stash/") for name in branches):
        raise RuntimeError("a branch named 'stash' would clash with the backup of the stashes")
    wanted = {prefix + name: sha for name, sha in branches.items()}
    for i, sha in enumerate(git(git_repo, "stash", "list", "--format=%H").split()):
        wanted[f"{prefix}stash/{i}"] = sha
    uncommitted = git(git_repo, "stash", "create").strip()
    if uncommitted:
        wanted[f"{prefix}stash/uncommitted"] = uncommitted

    existing = {ref: sha for sha, ref in (line.split() for line in git(git_repo, "ls-remote", "--heads", remote).splitlines())
                if ref.startswith(prefix)}
    refspecs = [f"+{sha}:{ref}" for ref, sha in wanted.items() if existing.get(ref) != sha]
    refspecs += [f":{ref}" for ref in existing if ref not in wanted]
    if not refspecs:
        return "up to date"
    git(git_repo, "push", "--quiet", remote, *refspecs)
    return f"{len(refspecs)} backup branches updated"


def all_git_repos():
    yield from (Path(git_repo) for git_repo in config.git_repos())
    for root in config.git_search_roots():
        yield from find_git_repos(root)


def backup_vcs():
    failed = []
    for git_repo in all_git_repos():
        if git_repo.name in config.GIT_REPOS_WITHOUT_OWN_REMOTE:
            continue
        try:
            remote = own_remote(git_repo)
            if remote is None:
                raise RuntimeError("no remote of mine, and not in GIT_REPOS_WITHOUT_OWN_REMOTE")
            print(f"{git_repo}: {backup_git_repo(git_repo, remote)}")
        except (RuntimeError, subprocess.CalledProcessError) as error:
            failed.append(git_repo)
            print(f"{git_repo}: FAILED: {getattr(error, 'stderr', None) or error}", file=sys.stderr)
    if failed:
        print("Failed git repos:", *failed, sep="\n    ", file=sys.stderr)
    return 1 if failed else 0
