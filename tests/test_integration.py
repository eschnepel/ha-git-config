"""Integration tests: setup, buttons, sensors, services, Apply and Repairs issues."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er, issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.git_config.const import (
    CONF_AUTHOR_EMAIL,
    CONF_AUTHOR_NAME,
    CONF_BRANCH,
    CONF_PRIVATE_KEY,
    CONF_PUBLIC_KEY,
    CONF_REMOTE_URL,
    DOMAIN,
    ISSUE_AUTH_FAILED,
    ISSUE_CONFIG_INVALID,
    ISSUE_PULL_REFUSED,
    ISSUE_RESTART_REQUIRED,
)
from custom_components.git_config.errors import AUTH_FAILED, GitConfigError
from custom_components.git_config.git_ops import GitRepo
from custom_components.git_config.ssh import generate_keypair

from .helpers import BRANCH, make_repo, other_clone, other_commit, remote_files

CHECK = "custom_components.git_config.coordinator.async_check_ha_config_file"
VALID = SimpleNamespace(errors=[], error_str="")


async def setup_entry(
    hass: HomeAssistant, config_dir: Path, remote: Path
) -> MockConfigEntry:
    hass.config.config_dir = str(config_dir)
    await hass.async_add_executor_job(make_repo(config_dir, remote).adopt)
    private, public = generate_keypair()
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_REMOTE_URL: str(remote),
            CONF_BRANCH: BRANCH,
            CONF_PRIVATE_KEY: private,
            CONF_PUBLIC_KEY: public,
        },
        options={CONF_AUTHOR_NAME: "Tester", CONF_AUTHOR_EMAIL: "t@example.com"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


def entity_id(hass: HomeAssistant, entry: MockConfigEntry, platform: str, key: str) -> str:
    found = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{key}"
    )
    assert found
    return found


async def press(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> None:
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": entity_id(hass, entry, "button", key)},
        blocking=True,
    )


def sensor(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> str:
    return hass.states.get(entity_id(hass, entry, "sensor", key)).state


def issue(hass: HomeAssistant, issue_id: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, issue_id)


async def test_startup_fetch_and_sensors(
    hass: HomeAssistant, config_dir: Path, remote: Path
) -> None:
    entry = await setup_entry(hass, config_dir, remote)

    assert sensor(hass, entry, "pending") == "0"
    assert sensor(hass, entry, "behind") == "0"
    assert sensor(hass, entry, "ahead") == "0"
    assert sensor(hass, entry, "last_fetch") != "unknown"


async def test_commit_push_button(hass: HomeAssistant, config_dir: Path, remote: Path) -> None:
    entry = await setup_entry(hass, config_dir, remote)
    (config_dir / "new.yaml").write_text("n\n")

    await press(hass, entry, "commit_push")

    assert "new.yaml" in remote_files(remote)
    assert sensor(hass, entry, "pending") == "0"


async def test_commit_push_service(hass: HomeAssistant, config_dir: Path, remote: Path) -> None:
    await setup_entry(hass, config_dir, remote)
    (config_dir / "svc.yaml").write_text("s\n")

    await hass.services.async_call(DOMAIN, "commit_push", {"message": "hello"}, blocking=True)

    assert "svc.yaml" in remote_files(remote)


async def test_fetch_button_only_updates_behind(
    hass: HomeAssistant, tmp_path: Path, config_dir: Path, remote: Path
) -> None:
    entry = await setup_entry(hass, config_dir, remote)
    other_commit(other_clone(tmp_path, remote), {"theirs.yaml": "t\n"})

    await press(hass, entry, "fetch")

    assert sensor(hass, entry, "behind") == "1"
    assert not (config_dir / "theirs.yaml").exists()


async def test_pull_reloads_included_domain(
    hass: HomeAssistant, tmp_path: Path, config_dir: Path, remote: Path
) -> None:
    (config_dir / "configuration.yaml").write_text("automation: !include automations.yaml\n")
    (config_dir / "automations.yaml").write_text("[]\n")
    entry = await setup_entry(hass, config_dir, remote)
    other_commit(other_clone(tmp_path, remote), {"automations.yaml": "[]\n# changed\n"})
    reloads = async_mock_service(hass, "automation", "reload")

    with patch(CHECK, new=AsyncMock(return_value=VALID)):
        await press(hass, entry, "pull")

    assert len(reloads) == 1
    assert issue(hass, ISSUE_RESTART_REQUIRED) is None
    assert sensor(hass, entry, "behind") == "0"
    assert "changed" in (config_dir / "automations.yaml").read_text()


async def test_pull_requiring_restart_raises_fixable_issue(
    hass: HomeAssistant, tmp_path: Path, config_dir: Path, remote: Path
) -> None:
    (config_dir / "configuration.yaml").write_text("logger:\n")
    entry = await setup_entry(hass, config_dir, remote)
    other_commit(other_clone(tmp_path, remote), {"configuration.yaml": "logger:\n  default: debug\n"})

    with patch(CHECK, new=AsyncMock(return_value=VALID)):
        await press(hass, entry, "pull")

    restart = issue(hass, ISSUE_RESTART_REQUIRED)
    assert restart and restart.is_fixable


async def test_pull_with_invalid_config_neither_reloads_nor_restarts(
    hass: HomeAssistant, tmp_path: Path, config_dir: Path, remote: Path
) -> None:
    (config_dir / "configuration.yaml").write_text("automation: !include automations.yaml\n")
    (config_dir / "automations.yaml").write_text("[]\n")
    entry = await setup_entry(hass, config_dir, remote)
    other_commit(other_clone(tmp_path, remote), {"automations.yaml": "broken\n"})
    reloads = async_mock_service(hass, "automation", "reload")
    bad = SimpleNamespace(errors=["x"], error_str="automation: invalid")

    with patch(CHECK, new=AsyncMock(return_value=bad)):
        await press(hass, entry, "pull")

    assert not reloads
    assert issue(hass, ISSUE_CONFIG_INVALID)
    assert issue(hass, ISSUE_RESTART_REQUIRED) is None
    assert (config_dir / "automations.yaml").read_text() == "broken\n"


async def test_refused_pull_raises_issue_and_clears_after_success(
    hass: HomeAssistant, tmp_path: Path, config_dir: Path, remote: Path
) -> None:
    entry = await setup_entry(hass, config_dir, remote)
    (config_dir / "dirty.yaml").write_text("d\n")

    with pytest.raises(HomeAssistantError):
        await press(hass, entry, "pull")
    assert issue(hass, ISSUE_PULL_REFUSED)
    assert sensor(hass, entry, "last_error") != "unknown"

    await press(hass, entry, "commit_push")
    with patch(CHECK, new=AsyncMock(return_value=VALID)):
        await press(hass, entry, "pull")
    assert issue(hass, ISSUE_PULL_REFUSED) is None


async def test_auth_failure_raises_fixable_issue(
    hass: HomeAssistant, config_dir: Path, remote: Path
) -> None:
    entry = await setup_entry(hass, config_dir, remote)

    with (
        patch.object(GitRepo, "fetch", side_effect=GitConfigError(AUTH_FAILED, "denied")),
        pytest.raises(HomeAssistantError),
    ):
        await press(hass, entry, "fetch")

    auth = issue(hass, ISSUE_AUTH_FAILED)
    assert auth and auth.is_fixable


async def test_service_without_entry_fails(hass: HomeAssistant) -> None:
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, DOMAIN, {})
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "fetch", {}, blocking=True)
