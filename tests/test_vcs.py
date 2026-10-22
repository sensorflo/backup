import subprocess
from pathlib import Path

import pytest

from backup import cli, config, vcs

PREFIX = vcs.backup_branch_prefix().removeprefix("refs/heads/")


def git(repo, *args):
    return vcs.git(repo, *args)


def make_remote(env, name):
    remote = env.remotes / f"{name}.git"
    subprocess.run(["git", "init", "--bare", "--quiet", str(remote)], check=True)
    return remote


def make_repo(env, path: Path, remote: Path | None = None, remote_name="origin"):
    """A git repo with one commit on main, and remote (a bare repo) added and pushed to."""
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "--quiet")
    env.write(path / "file.txt", "v1")
    git(path, "add", ".")
    git(path, "commit", "--quiet", "-m", "first")
    if remote:
        git(path, "remote", "add", remote_name, remote.as_posix())
        git(path, "push", "--quiet", remote_name, "main")
    return path


def remote_branches(remote):
    out = git(remote, "for-each-ref", "--format=%(refname:lstrip=2) %(objectname)", "refs/heads")
    return dict(line.split() for line in out.splitlines())


def backup_branches(remote):
    return {name.removeprefix(PREFIX): sha for name, sha in remote_branches(remote).items() if name.startswith(PREFIX)}


@pytest.fixture
def project(env):
    """A repo below the git search roots, and its own remote. No other repos to back up."""
    for root in config.git_repos():
        Path(root).mkdir(parents=True, exist_ok=True)
        make_repo(env, Path(root), make_remote(env, Path(root).name))
    remote = make_remote(env, "project")
    return make_repo(env, env.home / "src" / "project", remote), remote


def test_remote_prefixes_are_local_only(env):
    # The isolated fixture's safety net: nothing in the tests counts GitHub/GitLab remotes as mine
    assert not any("github" in prefix or "gitlab" in prefix for prefix in config.OWN_REMOTE_PREFIXES)
    assert all(prefix.startswith(env.remotes.as_posix()) for prefix in config.OWN_REMOTE_PREFIXES)


def test_github_push_impossible_in_tests(env):
    repo = make_repo(env, env.root / "x", None)
    git(repo, "remote", "add", "origin", "https://github.com/sensorflo/nonexistent.git")
    with pytest.raises(subprocess.CalledProcessError, match="push") as error:
        git(repo, "push", "origin", "main")
    assert "not allowed" in error.value.stderr or "transport" in error.value.stderr


def test_branches_pushed(project):
    repo, remote = project
    git(repo, "branch", "feature")
    git(repo, "checkout", "--quiet", "-b", "wip/x")
    project[0].joinpath("wip.txt").write_text("wip")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "unpushed work")
    assert cli.main(["backup-vcs"]) == 0
    branches = backup_branches(remote)
    assert branches == {name: git(repo, "rev-parse", name).strip() for name in ["main", "feature", "wip/x"]}
    assert "main" in remote_branches(remote), "the normal branches are untouched"


def test_stashes_pushed(project):
    repo, remote = project
    for content in ["stash a", "stash b"]:
        (repo / "file.txt").write_text(content)
        git(repo, "stash", "--quiet")
    assert cli.main(["backup-vcs"]) == 0
    branches = backup_branches(remote)
    assert branches["stash/0"] == git(repo, "rev-parse", "stash@{0}").strip()
    assert branches["stash/1"] == git(repo, "rev-parse", "stash@{1}").strip()
    assert git(remote, "show", f"{PREFIX}stash/0:file.txt") == "stash b"
    assert git(remote, "show", f"{PREFIX}stash/1:file.txt") == "stash a"


def test_uncommitted_changes_pushed_without_touching_working_tree(project):
    repo, remote = project
    (repo / "file.txt").write_text("edited")
    (repo / "staged.txt").write_text("staged")
    git(repo, "add", "staged.txt")
    assert cli.main(["backup-vcs"]) == 0
    uncommitted = f"{PREFIX}stash/uncommitted"
    assert git(remote, "show", f"{uncommitted}:file.txt") == "edited"
    assert git(remote, "show", f"{uncommitted}:staged.txt") == "staged"
    assert (repo / "file.txt").read_text() == "edited"
    assert git(repo, "stash", "list") == ""
    assert git(repo, "diff", "--cached", "--name-only").split() == ["staged.txt"]


def test_second_run_up_to_date(project, capfd):
    repo, remote = project
    cli.main(["backup-vcs"])
    capfd.readouterr()
    assert cli.main(["backup-vcs"]) == 0
    assert f"{repo}: up to date" in capfd.readouterr().out


def test_obsolete_backup_branches_deleted(project):
    repo, remote = project
    git(repo, "branch", "feature")
    (repo / "file.txt").write_text("edited")
    cli.main(["backup-vcs"])
    assert {"feature", "stash/uncommitted"} <= set(backup_branches(remote))
    git(repo, "branch", "-D", "feature")
    git(repo, "checkout", "--quiet", "file.txt")
    assert cli.main(["backup-vcs"]) == 0
    assert set(backup_branches(remote)) == {"main"}


def test_updated_branch_force_pushed(project):
    repo, remote = project
    cli.main(["backup-vcs"])
    git(repo, "commit", "--quiet", "--amend", "-m", "rewritten")
    assert cli.main(["backup-vcs"]) == 0
    assert backup_branches(remote)["main"] == git(repo, "rev-parse", "main").strip()


def test_other_computers_backup_branches_untouched(project):
    repo, remote = project
    git(repo, "push", "--quiet", remote.as_posix(), "main:refs/heads/backup/other-computer/main")
    cli.main(["backup-vcs"])
    assert "backup/other-computer/main" in remote_branches(remote)


def test_own_remote_among_several(env, project):
    foreign = env.root / "foreign" / "lib.git"
    foreign.parent.mkdir()
    subprocess.run(["git", "init", "--bare", "--quiet", str(foreign)], check=True)
    remote = make_remote(env, "multi")
    repo = make_repo(env, env.home / "src" / "multi", foreign, remote_name="upstream")
    git(repo, "remote", "add", "mine", remote.as_posix())
    assert vcs.own_remote(repo) == "mine"
    assert cli.main(["backup-vcs"]) == 0
    assert "main" in backup_branches(remote)
    assert not any(name.startswith("backup/") for name in remote_branches(foreign))


def test_listed_foreign_repo_skipped(env, project, capfd):
    foreign = env.root / "foreign" / "googletest.git"
    foreign.parent.mkdir()
    subprocess.run(["git", "init", "--bare", "--quiet", str(foreign)], check=True)
    make_repo(env, env.home / "src" / "googletest", foreign)
    assert cli.main(["backup-vcs"]) == 0
    assert "googletest" not in capfd.readouterr().out
    assert remote_branches(foreign) == {"main": remote_branches(foreign)["main"]}


def test_repo_without_own_remote_is_an_error_but_others_continue(env, project, capfd):
    repo, remote = project
    make_repo(env, env.home / "Documents" / "notes", None)
    assert cli.main(["backup-vcs"]) == 1
    err = capfd.readouterr().err
    assert "notes: FAILED: no remote of mine" in err
    assert "Failed git repos:" in err
    assert "main" in backup_branches(remote), "the other repos are still backed up"


def test_branch_named_stash_is_an_error(project, capfd):
    repo, remote = project
    git(repo, "branch", "stash/old")
    assert cli.main(["backup-vcs"]) == 1
    assert "a branch named 'stash' would clash" in capfd.readouterr().err
    assert backup_branches(remote) == {}


def test_unreachable_remote_is_an_error(env, project, capfd):
    repo, remote = project
    git(repo, "remote", "set-url", "origin", (env.remotes / "gone.git").as_posix())
    assert cli.main(["backup-vcs"]) == 1
    assert f"{repo}: FAILED" in capfd.readouterr().err


def test_missing_configured_repo_is_an_error(env, project, monkeypatch, capfd):
    monkeypatch.setattr(config, "git_repos", lambda: [str(env.home / "nonexistent")])
    assert cli.main(["backup-vcs"]) == 1
    assert "nonexistent: FAILED" in capfd.readouterr().err


def test_find_git_repos_finds_nested_but_not_inside_dot_git(env):
    make_repo(env, env.root / "s" / "a")
    make_repo(env, env.root / "s" / "a" / "nested")
    make_repo(env, env.root / "s" / "deep" / "er" / "b")
    (env.root / "s" / "worktree").mkdir()
    (env.root / "s" / "worktree" / ".git").write_text("gitdir: elsewhere")  # a worktree or submodule
    found = {path.relative_to(env.root / "s").as_posix() for path in vcs.find_git_repos(env.root / "s")}
    assert found == {"a", "a/nested", "deep/er/b", "worktree"}


def test_all_git_repos_covers_configured_repos_and_search_roots(env, project):
    found = set(vcs.all_git_repos())
    assert {Path(path) for path in config.git_repos()} <= found
    assert env.home / "src" / "project" in found
