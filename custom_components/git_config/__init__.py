"""The Git Config integration: put the configuration directory under git control."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType

from .const import (
    DOMAIN,
    FETCH_INTERVAL,
    ISSUE_AUTH_FAILED,
    ISSUE_CONFIG_INVALID,
    ISSUE_HOST_KEY_MISMATCH,
    ISSUE_PULL_REFUSED,
    ISSUE_RESTART_REQUIRED,
    ISSUE_SECRETS_IN_HISTORY,
)
from .coordinator import GitConfigEntry, GitCoordinator
from .services import async_register_services

PLATFORMS = [Platform.BUTTON, Platform.SENSOR]
_ISSUES = (
    ISSUE_AUTH_FAILED,
    ISSUE_CONFIG_INVALID,
    ISSUE_HOST_KEY_MISMATCH,
    ISSUE_PULL_REFUSED,
    ISSUE_RESTART_REQUIRED,
    ISSUE_SECRETS_IN_HISTORY,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services."""
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: GitConfigEntry) -> bool:
    """Set up from a config entry."""
    coordinator = GitCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    # Fixed cadence: the startup minute is the hourly fetch minute.
    entry.async_on_unload(
        async_track_time_interval(hass, coordinator.async_scheduled_fetch, FETCH_INTERVAL)
    )
    entry.async_create_background_task(
        hass, coordinator.async_scheduled_fetch(), f"{DOMAIN}_initial_fetch"
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GitConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: GitConfigEntry) -> None:
    """Clear issues; the repository and .gitignore are left in place."""
    for issue_id in _ISSUES:
        ir.async_delete_issue(hass, DOMAIN, issue_id)


async def _async_reload(hass: HomeAssistant, entry: GitConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
