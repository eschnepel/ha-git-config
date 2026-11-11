"""Deploy Key helpers shared by the config, options and repairs flows."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_PRIVATE_KEY, CONF_PUBLIC_KEY
from .ssh import generate_keypair


async def async_new_keypair(hass: HomeAssistant) -> tuple[str, str]:
    """Generate a Deploy Key; returns (private, public)."""
    return await hass.async_add_executor_job(generate_keypair)


def async_store_keypair(
    hass: HomeAssistant, entry: ConfigEntry, private: str, public: str
) -> None:
    """Replace the stored Deploy Key; the entry reloads to use it."""
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_PRIVATE_KEY: private, CONF_PUBLIC_KEY: public}
    )
