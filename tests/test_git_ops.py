"""Tests for the git layer, using local bare repositories as the remote."""

from __future__ import annotations

from pathlib import Path

from dulwich import porcelain
from dulwich.repo import Repo
import pytest

from custom_components.git_config import errors
from custom_components.git_config.git_ops import GitRepo

from .helpers import BRANCH, make_repo, other_clone, other_commit, remote_files, seed_remote


def test_adopt_empty_remote_pushes_initial_commit(config_dir: Path, remote: Path) -> None:
    (config_dir / "configuration.yaml").write_text("homeassistant:\n")
    (config_dir / "secrets.yaml").write_text("pw: x\n")
    (config_dir / ".storage").mkdir()
    (config_dir / ".storage" / "core.config_entries").write_text("{}")

    result = make_repo(config_dir, remote).adopt()

    assert result.initial_commit and result.pushed
    files = remote_files(remote)
    assert "configuration.yaml" in files
    assert ".gitignore" in files
    assert "secrets.yaml" not in files
    assert not any(f.startswith(".storage/") for f in files)
    ignore = (config_dir / ".gitignore").read_text()
    assert "secrets.yaml" in ignore and ".storage/" in ignore


def test_adopt_populated_remote_keeps_local_files(
    tmp_path: Path, config_dir: Path, remote: Path
) -> None:
    seed_remote(tmp_path, remote, {"configuration.yaml": "remote\n", "extra.yaml": "e\n"})
    (config_dir / "configuration.yaml").write_text("local\n")

    repo = make_repo(config_dir, remote)
    result = repo.adopt()

    assert not result.pushed
    assert (config_dir / "configuration.yaml").read_text() == "local\n"
    status = repo.status()
    assert status.pending >= 2  # modified file, missing file and .gitignore
    assert status.ahead == status.behind == 0


def test_adopt_rejects_remote_mismatch(config_dir: Path, remote: Path, tmp_path: Path) -> None:
    make_repo(config_dir, remote).adopt()
    other = tmp_path / "elsewhere.git"
    Repo.init_bare(str(other), mkdir=True)
    with pytest.raises(errors.GitConfigError) as err:
        make_repo(config_dir, other).adopt()
    assert err.value.code == errors.REMOTE_MISMATCH


def test_adopt_rejects_unrelated_history(
    tmp_path: Path, config_dir: Path, remote: Path
) -> None:
    seed_remote(tmp_path, remote, {"a.yaml": "a\n"})
    local = Repo.init(str(config_dir), default_branch=BRANCH.encode())
    local.close()
    (config_dir / "b.yaml").write_text("b\n")
    porcelain.add(str(config_dir))
    porcelain.commit(str(config_dir), message=b"local", author=b"L <l@x>", committer=b"L <l@x>")

    with pytest.raises(errors.GitConfigError) as err:
        make_repo(config_dir, remote).adopt()
    assert err.value.code == errors.UNRELATED_HISTORY


def test_commit_push_and_noop(config_dir: Path, remote: Path) -> None:
    repo = make_repo(config_dir, remote)
    repo.adopt()
    (config_dir / "automations.yaml").write_text("[]\n")

    first = repo.commit_push()
    second = repo.commit_push()

    assert first.commit and first.pushed
    assert second.commit is None and not second.pushed
    assert "automations.yaml" in remote_files(remote)
    assert repo.status().pending == 0


def test_commit_push_untracks_protected_and_survives_negation(
    config_dir: Path, remote: Path
) -> None:
    repo = make_repo(config_dir, remote)
    repo.adopt()
    (config_dir / "secrets.yaml").write_text("pw: x\n")
    (config_dir / ".storage").mkdir()
    (config_dir / ".storage" / "key").write_text("k")
    # Nested ignore file that negates everything, defeating the root block.
    (config_dir / ".storage" / ".gitignore").write_text("!*\n")
    ignore = config_dir / ".gitignore"
    ignore.write_text("!secrets.yaml\n!.storage/\n" + ignore.read_text())

    result = repo.commit_push()

    files = remote_files(remote)
    assert "secrets.yaml" not in files
    assert not any(f.startswith(".storage/") for f in files)
    assert result.pushed
    assert ".storage/" in ignore.read_text().splitlines()[-2:][0] or ".storage/" in ignore.read_text()


def test_commit_push_untracks_previously_tracked_secret(config_dir: Path, remote: Path) -> None:
    (config_dir / "secrets.yaml").write_text("pw: x\n")
    local = Repo.init(str(config_dir), default_branch=BRANCH.encode())
    local.close()
    porcelain.add(str(config_dir), paths=["secrets.yaml"])
    porcelain.commit(str(config_dir), message=b"oops", author=b"L <l@x>", committer=b"L <l@x>")

    repo = make_repo(config_dir, remote)
    result = repo.adopt()

    assert result.pushed
    assert repo.status().protected_in_history is True
    (config_dir / "x.yaml").write_text("x\n")
    repo.commit_push()
    assert "secrets.yaml" not in remote_files(remote)
    assert (config_dir / "secrets.yaml").exists()


def test_pull_fast_forwards_and_reports_changes(
    tmp_path: Path, config_dir: Path, remote: Path
) -> None:
    (config_dir / "configuration.yaml").write_text("a: 1\n")
    repo = make_repo(config_dir, remote)
    repo.adopt()
    clone = other_clone(tmp_path, remote)
    other_commit(clone, {"configuration.yaml": "a: 2\n", "new.yaml": "n\n"})

    assert repo.fetch().behind == 1
    result = repo.pull()

    assert result.updated
    assert {c.path for c in result.changed} == {"configuration.yaml", "new.yaml"}
    changed = {c.path: c for c in result.changed}
    assert changed["configuration.yaml"].old_text == "a: 1\n"
    assert changed["configuration.yaml"].new_text == "a: 2\n"
    assert (config_dir / "new.yaml").read_text() == "n\n"
    assert repo.status().behind == 0


def test_pull_deletes_removed_files(tmp_path: Path, config_dir: Path, remote: Path) -> None:
    (config_dir / "gone.yaml").write_text("g\n")
    repo = make_repo(config_dir, remote)
    repo.adopt()
    clone = other_clone(tmp_path, remote)
    other_commit(clone, {"gone.yaml": None})

    repo.pull()

    assert not (config_dir / "gone.yaml").exists()


def test_pull_refuses_pending_changes(tmp_path: Path, config_dir: Path, remote: Path) -> None:
    repo = make_repo(config_dir, remote)
    repo.adopt()
    (config_dir / "dirty.yaml").write_text("d\n")

    with pytest.raises(errors.GitConfigError) as err:
        repo.pull()
    assert err.value.code == errors.PENDING_CHANGES


def test_pull_refuses_divergence(tmp_path: Path, config_dir: Path, remote: Path) -> None:
    repo = make_repo(config_dir, remote)
    repo.adopt()
    clone = other_clone(tmp_path, remote)
    other_commit(clone, {"theirs.yaml": "t\n"})
    (config_dir / "mine.yaml").write_text("m\n")
    # Commit locally without pushing.
    porcelain.add(str(config_dir))
    porcelain.commit(str(config_dir), message=b"mine", author=b"L <l@x>", committer=b"L <l@x>")

    with pytest.raises(errors.GitConfigError) as err:
        repo.pull()
    assert err.value.code == errors.DIVERGED
    assert not (config_dir / "theirs.yaml").exists()


def test_pull_refuses_protected_in_remote(
    tmp_path: Path, config_dir: Path, remote: Path
) -> None:
    (config_dir / "secrets.yaml").write_text("local: secret\n")
    repo = make_repo(config_dir, remote)
    repo.adopt()
    clone = other_clone(tmp_path, remote)
    other_commit(clone, {"secrets.yaml": "remote: injected\n", "ok.yaml": "o\n"})

    with pytest.raises(errors.GitConfigError) as err:
        repo.pull()

    assert err.value.code == errors.PROTECTED_IN_REMOTE
    assert (config_dir / "secrets.yaml").read_text() == "local: secret\n"
    assert not (config_dir / "ok.yaml").exists()


def test_pull_refuses_to_overwrite_ignored_file(
    tmp_path: Path, config_dir: Path, remote: Path
) -> None:
    repo = make_repo(config_dir, remote)
    repo.adopt()
    (config_dir / "tts").mkdir()
    (config_dir / "tts" / "cache.yaml").write_text("local\n")
    clone = other_clone(tmp_path, remote)
    other_commit(clone, {"tts/cache.yaml": "remote\n"})

    with pytest.raises(errors.GitConfigError) as err:
        repo.pull()
    assert err.value.code == errors.OVERWRITE
    assert (config_dir / "tts" / "cache.yaml").read_text() == "local\n"


def test_commit_push_refuses_when_behind(tmp_path: Path, config_dir: Path, remote: Path) -> None:
    repo = make_repo(config_dir, remote)
    repo.adopt()
    clone = other_clone(tmp_path, remote)
    other_commit(clone, {"theirs.yaml": "t\n"})
    (config_dir / "mine.yaml").write_text("m\n")

    with pytest.raises(errors.GitConfigError) as err:
        repo.commit_push()
    assert err.value.code == errors.BEHIND_REMOTE
    assert repo.status().pending == 1


def test_branch_mismatch(config_dir: Path, remote: Path) -> None:
    make_repo(config_dir, remote).adopt()
    wrong = GitRepo(config_dir, str(remote), "other", "T", "t@x")
    with pytest.raises(errors.GitConfigError) as err:
        wrong.status()
    assert err.value.code == errors.BRANCH_MISMATCH
