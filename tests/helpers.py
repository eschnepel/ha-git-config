"""Shared helpers for tests that use local bare repositories as the remote."""

from __future__ import annotations

from pathlib import Path

from dulwich import porcelain
from dulwich.object_store import iter_tree_contents
from dulwich.repo import Repo

from custom_components.git_config.git_ops import GitRepo

BRANCH = "main"


def make_repo(config_dir: Path, remote: Path) -> GitRepo:
    return GitRepo(config_dir, str(remote), BRANCH, "Tester", "t@example.com")


def other_clone(tmp_path: Path, remote: Path, name: str = "other") -> Path:
    """A second working copy acting as a different machine."""
    path = tmp_path / name
    porcelain.clone(str(remote), str(path), branch=BRANCH.encode())
    return path


def other_commit(path: Path, files: dict[str, str | None], message: str = "other") -> None:
    for name, content in files.items():
        target = path / name
        if content is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    # Stage explicitly: the clone's .gitignore would skip protected/ignored paths.
    with Repo(str(path)) as repo:
        repo.get_worktree().stage(list(files))
    porcelain.commit(str(path), message=message.encode(), author=b"O <o@x>", committer=b"O <o@x>")
    porcelain.push(str(path), "origin", refspecs=[f"refs/heads/{BRANCH}".encode()])


def remote_files(remote: Path) -> set[str]:
    with Repo(str(remote)) as repo:
        head = repo.refs[f"refs/heads/{BRANCH}".encode()]
        return {
            e.path.decode() for e in iter_tree_contents(repo.object_store, repo[head].tree)
        }


def seed_remote(tmp_path: Path, remote: Path, files: dict[str, str]) -> None:
    seed = tmp_path / "seed"
    seed.mkdir()
    repo = Repo.init(str(seed), default_branch=BRANCH.encode())
    repo.close()
    porcelain.remote_add(str(seed), "origin", str(remote))
    for name, content in files.items():
        (seed / name).parent.mkdir(parents=True, exist_ok=True)
        (seed / name).write_text(content)
    porcelain.add(str(seed))
    porcelain.commit(str(seed), message=b"seed", author=b"S <s@x>", committer=b"S <s@x>")
    porcelain.push(str(seed), "origin", refspecs=[f"refs/heads/{BRANCH}".encode()])
