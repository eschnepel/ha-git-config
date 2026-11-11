"""Synchronous git operations on the configuration directory; run them in an executor."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing, contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
import io
import logging
import os
from pathlib import Path
from typing import Any

from dulwich import porcelain
from dulwich.diff_tree import tree_changes
from dulwich.errors import GitProtocolError, HangupException
from dulwich.graph import find_merge_base
from dulwich.repo import Repo
import paramiko

from .const import PROTECTED_ENTRIES
from .errors import (
    AUTH_FAILED,
    BEHIND_REMOTE,
    BRANCH_MISMATCH,
    DIVERGED,
    NOT_FOUND,
    OPERATION_FAILED,
    OVERWRITE,
    PENDING_CHANGES,
    PROTECTED_IN_REMOTE,
    PROTECTED_STAGED,
    REMOTE_MISMATCH,
    UNRELATED_HISTORY,
    GitConfigError,
)
from .ignore import read_entries, sync_file
from .ssh import PinnedSSHVendor

_LOGGER = logging.getLogger(__name__)

_YAML_SUFFIXES = (".yaml", ".yml")
_MAX_TEXT_BYTES = 1_000_000
_SUMMARY_LIMIT = 10


def is_protected(path: str | bytes) -> bool:
    """Return True if a repo-relative path is covered by a Protected Entry."""
    text = os.fsdecode(path).replace("\\", "/")
    for entry in PROTECTED_ENTRIES:
        if entry.endswith("/"):
            if text.startswith(entry) or text == entry.rstrip("/"):
                return True
        elif text.rsplit("/", 1)[-1] == entry:
            return True
    return False


@dataclass(frozen=True)
class RepoStatus:
    """Snapshot of the Config Repo."""

    pending: int
    ahead: int
    behind: int
    head: str | None
    protected_in_history: bool


@dataclass(frozen=True)
class ChangedFile:
    """One path changed by a pull; texts are only loaded for YAML files."""

    path: str
    old_text: str | None = None
    new_text: str | None = None


@dataclass(frozen=True)
class PullResult:
    """Outcome of a pull."""

    changed: list[ChangedFile] = field(default_factory=list)
    updated: bool = False


@dataclass(frozen=True)
class AdoptResult:
    """Outcome of adopting the configuration directory."""

    initial_commit: bool
    pushed: bool


@dataclass(frozen=True)
class CommitPushResult:
    """Outcome of Commit & Push."""

    commit: str | None
    pushed: bool


def _map_error(err: Exception) -> GitConfigError:
    if isinstance(err, GitConfigError):
        return err
    parts = [str(err)]
    parts.extend(
        line.decode(errors="replace") if isinstance(line, bytes) else str(line)
        for line in getattr(err, "stderr_lines", None) or []
    )
    text = " ".join(parts)
    lowered = text.lower()
    if "repository not found" in lowered:
        return GitConfigError(NOT_FOUND, text)
    if "permission denied" in lowered or isinstance(err, paramiko.AuthenticationException):
        return GitConfigError(AUTH_FAILED, text)
    return GitConfigError(OPERATION_FAILED, text)


class GitRepo:
    """The Config Repo: the whole configuration directory, one remote, one branch."""

    def __init__(
        self,
        config_dir: str | Path,
        remote_url: str,
        branch: str,
        author_name: str,
        author_email: str,
        private_key: str | None = None,
    ) -> None:
        """Initialize; `private_key` is only needed for SSH remotes."""
        self.path = Path(config_dir)
        self._url = remote_url
        self._branch = branch
        self._author = f"{author_name} <{author_email}>".encode()
        self._vendor = PinnedSSHVendor(private_key) if private_key else None
        self._local_ref = f"refs/heads/{branch}".encode()
        self._remote_ref = f"refs/remotes/origin/{branch}".encode()
        self._history_cache: tuple[tuple[bytes | None, bytes | None], bool] | None = None

    # -- helpers -----------------------------------------------------------

    @contextmanager
    def _net(self) -> Iterator[None]:
        from .ssh import use_vendor  # noqa: PLC0415

        with use_vendor(self._vendor) if self._vendor else nullcontext():
            try:
                yield
            except GitConfigError:
                raise
            except (
                paramiko.SSHException,
                GitProtocolError,
                HangupException,
                porcelain.Error,
                OSError,
            ) as err:
                raise _map_error(err) from err

    @staticmethod
    def _ref(repo: Repo, name: bytes) -> bytes | None:
        try:
            return repo.refs[name]
        except KeyError:
            return None

    def _is_repo(self) -> bool:
        return (self.path / ".git").exists()

    def _count(self, repo: Repo, include: bytes | None, exclude: bytes | None) -> int:
        if include is None:
            return 0
        walker = repo.get_walker(
            include=[include], exclude=[exclude] if exclude else None
        )
        return sum(1 for _ in walker)

    def _pending_paths(self, repo: Repo) -> set[str]:
        status = porcelain.status(repo, untracked_files="all")
        paths: set[str] = set()
        for group in status.staged.values():
            paths.update(os.fsdecode(p) for p in group)
        paths.update(os.fsdecode(p) for p in status.unstaged)
        paths.update(os.fsdecode(p) for p in status.untracked)
        return paths

    def _history_has_protected(self, repo: Repo, *shas: bytes | None) -> bool:
        include = [s for s in shas if s]
        if not include:
            return False
        key = (shas[0], shas[1] if len(shas) > 1 else None)
        if self._history_cache and self._history_cache[0] == key:
            return self._history_cache[1]
        paths = [e.rstrip("/").encode() for e in PROTECTED_ENTRIES]
        found = any(True for _ in repo.get_walker(include=include, paths=paths, max_entries=1))
        self._history_cache = (key, found)
        return found

    def _check_branch(self, repo: Repo) -> None:
        head = repo.refs.read_ref(b"HEAD")
        if head is None or not head.startswith(b"ref: "):
            raise GitConfigError(BRANCH_MISMATCH, "detached HEAD")
        current = head[5:].strip()
        if current != self._local_ref:
            raise GitConfigError(BRANCH_MISMATCH, current.decode())

    def _ensure_origin(self, repo: Repo) -> None:
        config = repo.get_config()
        try:
            url = config.get((b"remote", b"origin"), b"url").decode()
        except KeyError:
            porcelain.remote_add(repo, "origin", self._url)
            return
        if url != self._url:
            raise GitConfigError(REMOTE_MISMATCH, url)

    def _open(self) -> Repo:
        if not self._is_repo():
            raise GitConfigError(OPERATION_FAILED, "not a git repository")
        repo = Repo(str(self.path))
        try:
            self._check_branch(repo)
        except BaseException:
            repo.close()
            raise
        return repo

    def _fetch(self, repo: Repo) -> None:
        with self._net():
            porcelain.fetch(
                repo,
                "origin",
                outstream=io.StringIO(),
                errstream=io.BytesIO(),
            )

    def _push(self, repo: Repo) -> None:
        refspec = self._local_ref + b":" + self._local_ref
        with self._net():
            result = porcelain.push(
                repo,
                "origin",
                refspecs=[refspec],
                outstream=io.BytesIO(),
                errstream=io.BytesIO(),
            )
        for ref, error in (getattr(result, "ref_status", None) or {}).items():
            if error is not None:
                raise GitConfigError(OPERATION_FAILED, f"{ref.decode()}: {error}")

    def _untrack_protected(self, repo: Repo) -> None:
        index = repo.open_index()
        doomed = [p for p in index if is_protected(p)]
        for path in doomed:
            del index[path]
        if doomed:
            index.write()

    def _commit_all(self, repo: Repo, message: str | None) -> str | None:
        self._untrack_protected(repo)
        porcelain.add(repo)
        staged = porcelain.status(repo, untracked_files="no").staged
        # Deleting a protected path from the tree is the desired outcome.
        leaked = [
            os.fsdecode(p)
            for kind in ("add", "modify")
            for p in staged.get(kind, [])
            if is_protected(p)
        ]
        if leaked:
            self._untrack_protected(repo)
            raise GitConfigError(PROTECTED_STAGED, ", ".join(sorted(leaked)))
        if not any(staged.values()):
            return None
        if message is None:
            message = self._default_message(staged)
        sha = porcelain.commit(
            repo, message=message.encode(), author=self._author, committer=self._author
        )
        return sha.decode()

    @staticmethod
    def _default_message(staged: dict[str, list[bytes]]) -> str:
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        marks = {"add": "A", "modify": "M", "delete": "D"}
        lines = [
            f"{marks.get(kind, '?')} {os.fsdecode(path)}"
            for kind, group in staged.items()
            for path in group
        ]
        shown = lines[:_SUMMARY_LIMIT]
        if len(lines) > len(shown):
            shown.append(f"... and {len(lines) - len(shown)} more")
        return f"HA config update {stamp}\n\n" + "\n".join(shown)

    def _require_related(self, repo: Repo, local: bytes, remote: bytes) -> None:
        if not find_merge_base(repo, [local, remote]):
            raise GitConfigError(UNRELATED_HISTORY)

    # -- public operations --------------------------------------------------

    def ensure_ignore(self, *, seed: bool = False) -> None:
        """Re-assert the Managed Ignore Block, including Protected Entries."""
        sync_file(self.path / ".gitignore", seed=seed)

    def update_ignore(self, add: list[str], remove: list[str]) -> None:
        """Add and remove user-managed ignore entries."""
        sync_file(self.path / ".gitignore", add=add, remove=remove)

    def ignore_entries(self) -> list[str]:
        """Return the user-managed entries of the Managed Ignore Block."""
        path = self.path / ".gitignore"
        return read_entries(path.read_text(encoding="utf-8") if path.exists() else "")

    def adopt(self) -> AdoptResult:
        """Bring the configuration directory under git control against the remote."""
        if self._is_repo():
            repo = Repo(str(self.path))
        else:
            repo = Repo.init(str(self.path), default_branch=self._branch.encode())
        with closing(repo):
            self._check_branch(repo)
            self._ensure_origin(repo)
            self.ensure_ignore(seed=True)
            self._fetch(repo)

            local = self._ref(repo, self._local_ref)
            remote = self._ref(repo, self._remote_ref)
            initial = pushed = False
            if remote is None:
                if local is None:
                    initial = self._commit_all(repo, "Initial commit") is not None
                self._push(repo)
                pushed = True
            elif local is None:
                repo.refs[self._local_ref] = remote
                porcelain.reset(repo, "mixed", remote)
            else:
                self._require_related(repo, local, remote)
            return AdoptResult(initial_commit=initial, pushed=pushed)

    def status(self) -> RepoStatus:
        """Return pending changes, ahead/behind counts and a history check."""
        with closing(self._open()) as repo:
            local = self._ref(repo, self._local_ref)
            remote = self._ref(repo, self._remote_ref)
            return RepoStatus(
                pending=len(self._pending_paths(repo)),
                ahead=self._count(repo, local, remote),
                behind=self._count(repo, remote, local),
                head=local.decode() if local else None,
                protected_in_history=self._history_has_protected(repo, local, remote),
            )

    def fetch(self) -> RepoStatus:
        """Download remote commits without touching the working tree."""
        with closing(self._open()) as repo:
            self._fetch(repo)
        return self.status()

    def commit_push(self, message: str | None = None) -> CommitPushResult:
        """Commit all Pending Changes and push; pushes existing local commits too."""
        self.ensure_ignore()
        with closing(self._open()) as repo:
            self._fetch(repo)
            local = self._ref(repo, self._local_ref)
            remote = self._ref(repo, self._remote_ref)
            if remote is not None and local is not None:
                self._require_related(repo, local, remote)
                if self._count(repo, remote, local):
                    code = DIVERGED if self._count(repo, local, remote) else BEHIND_REMOTE
                    raise GitConfigError(code)
            commit = self._commit_all(repo, message)
            local = self._ref(repo, self._local_ref)
            if self._count(repo, local, remote) == 0:
                return CommitPushResult(commit=None, pushed=False)
            self._push(repo)
            return CommitPushResult(commit=commit, pushed=True)

    def pull(self) -> PullResult:
        """Fetch and fast-forward; refuse rather than merge or overwrite."""
        with closing(self._open()) as repo:
            if self._pending_paths(repo):
                raise GitConfigError(PENDING_CHANGES)
            self._fetch(repo)
            local = self._ref(repo, self._local_ref)
            remote = self._ref(repo, self._remote_ref)
            if remote is None or local == remote:
                return PullResult()
            if local is not None:
                self._require_related(repo, local, remote)
                behind = self._count(repo, remote, local)
                if self._count(repo, local, remote):
                    if behind:
                        raise GitConfigError(DIVERGED)
                    return PullResult()

            old_tree = repo[local].tree if local else None
            new_tree = repo[remote].tree
            changes = [
                (c.old.path if c.old else None, c.new.path if c.new else None)
                for c in tree_changes(repo.object_store, old_tree, new_tree)
            ]
            touched = sorted(
                {os.fsdecode(p) for pair in changes for p in pair if p}
            )
            offending = [p for p in touched if is_protected(p)]
            if offending:
                raise GitConfigError(PROTECTED_IN_REMOTE, ", ".join(offending))
            collisions = [
                os.fsdecode(new)
                for old, new in changes
                if new
                and not old
                and os.path.lexists(self.path / os.fsdecode(new))
            ]
            if collisions:
                raise GitConfigError(OVERWRITE, ", ".join(sorted(collisions)))

            changed = [
                ChangedFile(
                    p,
                    self._blob_text(repo, old_tree, p),
                    self._blob_text(repo, new_tree, p),
                )
                for p in touched
            ]
            repo.refs[self._local_ref] = remote
            porcelain.reset(repo, "hard", remote)
            return PullResult(changed=changed, updated=True)

    @staticmethod
    def _blob_text(repo: Repo, tree: bytes | None, path: str) -> str | None:
        if tree is None or not path.endswith(_YAML_SUFFIXES):
            return None
        from dulwich.object_store import tree_lookup_path  # noqa: PLC0415

        try:
            _, sha = tree_lookup_path(repo.__getitem__, tree, path.encode())
        except KeyError:
            return None
        data: Any = repo[sha].data
        if len(data) > _MAX_TEXT_BYTES:
            return None
        return data.decode("utf-8", errors="replace")
