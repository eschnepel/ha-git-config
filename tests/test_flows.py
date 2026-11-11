"""Tests for the config flow, options flow, repairs flows and the SSH transport."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import paramiko
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.git_config import ssh
from custom_components.git_config.const import (
    CONF_AUTHOR_EMAIL,
    CONF_AUTHOR_NAME,
    CONF_BRANCH,
    CONF_PRIVATE_KEY,
    CONF_REMOTE_URL,
    DOMAIN,
)
from custom_components.git_config.errors import (
    AUTH_FAILED,
    HOST_KEY_MISMATCH,
    GitConfigError,
)
from custom_components.git_config.git_ops import AdoptResult, GitRepo
from custom_components.git_config.repairs import RegenerateKeyFlow, RestartFlow

from .test_integration import setup_entry

USER_INPUT = {
    CONF_REMOTE_URL: "git@github.com:owner/repo.git",
    CONF_BRANCH: "main",
    CONF_AUTHOR_NAME: "Tester",
    CONF_AUTHOR_EMAIL: "t@example.com",
}


async def start_flow(hass: HomeAssistant, user_input: dict[str, str]):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        (CONF_REMOTE_URL, "https://github.com/owner/repo.git", "invalid_url"),
        (CONF_REMOTE_URL, "git@gitlab.com:owner/repo.git", "invalid_url"),
        (CONF_REMOTE_URL, "git@github.com:owner/repo.git; rm -rf /", "invalid_url"),
        (CONF_BRANCH, "-bad", "invalid_branch"),
    ],
)
async def test_user_step_validation(
    hass: HomeAssistant, field: str, value: str, error: str
) -> None:
    result = await start_flow(hass, {**USER_INPUT, field: value})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {field: error}


async def test_full_flow_creates_entry(hass: HomeAssistant) -> None:
    result = await start_flow(hass, USER_INPUT)
    assert result["step_id"] == "key"
    assert result["description_placeholders"]["public_key"].startswith("ssh-ed25519 ")

    with (
        patch.object(GitRepo, "adopt", return_value=AdoptResult(True, True)),
        patch("custom_components.git_config.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_REMOTE_URL] == USER_INPUT[CONF_REMOTE_URL]
    assert "OPENSSH PRIVATE KEY" in result["data"][CONF_PRIVATE_KEY]
    assert result["options"][CONF_AUTHOR_NAME] == "Tester"


@pytest.mark.parametrize(
    ("code", "error"),
    [(AUTH_FAILED, "auth_failed"), (HOST_KEY_MISMATCH, "host_key_mismatch"), ("diverged", "operation_failed")],
)
async def test_adopt_failure_is_shown_and_retryable(
    hass: HomeAssistant, code: str, error: str
) -> None:
    result = await start_flow(hass, USER_INPUT)

    with patch.object(GitRepo, "adopt", side_effect=GitConfigError(code)):
        failed = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert failed["type"] is FlowResultType.FORM
    assert failed["step_id"] == "key"
    assert failed["errors"] == {"base": error}


async def test_single_instance(hass: HomeAssistant, config_dir: Path, remote: Path) -> None:
    await setup_entry(hass, config_dir, remote)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def open_options(hass: HomeAssistant, entry: MockConfigEntry, step: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


async def test_options_add_and_remove_ignore_entries(
    hass: HomeAssistant, config_dir: Path, remote: Path
) -> None:
    entry = await setup_entry(hass, config_dir, remote)
    form = await open_options(hass, entry, "ignore")
    assert form["step_id"] == "ignore"

    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {"add": "*.bak\nbackups/", "remove": ["tts/"]}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    lines = (config_dir / ".gitignore").read_text().splitlines()
    assert "*.bak" in lines and "backups/" in lines
    assert "tts/" not in lines
    assert "secrets.yaml" in lines and ".storage/" in lines


async def test_options_cannot_remove_protected_entries(
    hass: HomeAssistant, config_dir: Path, remote: Path
) -> None:
    entry = await setup_entry(hass, config_dir, remote)
    form = await open_options(hass, entry, "ignore")

    # Protected entries are not offered, and removal by name is still ineffective.
    with pytest.raises(Exception):  # noqa: B017 - schema rejects values outside the options
        await hass.config_entries.options.async_configure(
            form["flow_id"], {"remove": ["secrets.yaml"]}
        )
    assert "secrets.yaml" in (config_dir / ".gitignore").read_text().splitlines()


async def test_options_reject_negation(
    hass: HomeAssistant, config_dir: Path, remote: Path
) -> None:
    entry = await setup_entry(hass, config_dir, remote)
    form = await open_options(hass, entry, "ignore")

    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {"add": "!secrets.yaml"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"add": "invalid_entry"}


async def test_options_change_author(hass: HomeAssistant, config_dir: Path, remote: Path) -> None:
    entry = await setup_entry(hass, config_dir, remote)
    form = await open_options(hass, entry, "author")

    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {CONF_AUTHOR_NAME: "New Name", CONF_AUTHOR_EMAIL: "new@example.com"}
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_AUTHOR_NAME] == "New Name"


async def test_options_regenerate_key(hass: HomeAssistant, config_dir: Path, remote: Path) -> None:
    entry = await setup_entry(hass, config_dir, remote)
    old = entry.data[CONF_PRIVATE_KEY]
    form = await open_options(hass, entry, "key")
    assert form["description_placeholders"]["public_key"].startswith("ssh-ed25519 ")

    result = await hass.config_entries.options.async_configure(form["flow_id"], {})
    await hass.async_block_till_done(wait_background_tasks=True)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_PRIVATE_KEY] != old


async def test_restart_repair_flow_restarts(hass: HomeAssistant) -> None:
    flow = RestartFlow()
    flow.hass = hass
    flow.handler = DOMAIN
    flow.flow_id = "restart"
    restarts = async_mock_service(hass, "homeassistant", "restart")

    form = await flow.async_step_init()
    assert form["type"] is FlowResultType.FORM and not restarts
    done = await flow.async_step_confirm({})

    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert len(restarts) == 1


async def test_regenerate_key_repair_flow(
    hass: HomeAssistant, config_dir: Path, remote: Path
) -> None:
    entry = await setup_entry(hass, config_dir, remote)
    old = entry.data[CONF_PRIVATE_KEY]
    flow = RegenerateKeyFlow()
    flow.hass = hass
    flow.handler = DOMAIN
    flow.flow_id = "key"

    form = await flow.async_step_init()
    assert form["description_placeholders"]["public_key"].startswith("ssh-ed25519 ")
    done = await flow.async_step_init({})
    await hass.async_block_till_done(wait_background_tasks=True)

    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_PRIVATE_KEY] != old


def test_generated_key_loads_in_paramiko() -> None:
    private, public = ssh.generate_keypair()
    assert public.startswith("ssh-ed25519 ")
    vendor = ssh.PinnedSSHVendor(private)
    assert vendor._pkey.get_name() == "ssh-ed25519"


def test_vendor_refuses_other_hosts() -> None:
    private, _ = ssh.generate_keypair()
    with pytest.raises(GitConfigError) as err:
        ssh.PinnedSSHVendor(private).run_command("evil.example.com", b"git-upload-pack 'x'")
    assert err.value.code == HOST_KEY_MISMATCH


def test_pinned_host_keys_parse() -> None:
    import base64

    from custom_components.git_config.github_host_keys import GITHUB_HOST_KEYS

    assert {t for t, _ in GITHUB_HOST_KEYS} == set(ssh._KEY_CLASSES)
    for key_type, data in GITHUB_HOST_KEYS:
        key = ssh._KEY_CLASSES[key_type](data=base64.b64decode(data))
        assert key.get_name() == key_type
        assert isinstance(key, paramiko.PKey)
