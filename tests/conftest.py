"""Pytest configuration."""

from pathlib import Path

from dulwich.repo import Repo
import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow loading the custom integration in every test."""
    return


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    """An empty bare repository acting as the remote."""
    path = tmp_path / "remote.git"
    Repo.init_bare(str(path), mkdir=True)
    return path


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """An empty configuration directory."""
    path = tmp_path / "config"
    path.mkdir()
    return path
