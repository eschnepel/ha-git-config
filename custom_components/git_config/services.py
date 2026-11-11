"""Services for the Git Config integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .const import (
    ATTR_MESSAGE,
    DOMAIN,
    SERVICE_COMMIT_PUSH,
    SERVICE_FETCH,
    SERVICE_PULL,
)
from .coordinator import GitCoordinator


def _coordinator(hass: HomeAssistant) -> GitCoordinator:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")


def async_register_services(hass: HomeAssistant) -> None:
    """Register the domain services."""

    async def commit_push(call: ServiceCall) -> None:
        await _coordinator(call.hass).async_commit_push(call.data.get(ATTR_MESSAGE))

    async def fetch(call: ServiceCall) -> None:
        await _coordinator(call.hass).async_fetch()

    async def pull(call: ServiceCall) -> None:
        await _coordinator(call.hass).async_pull()

    hass.services.async_register(
        DOMAIN,
        SERVICE_COMMIT_PUSH,
        commit_push,
        schema=vol.Schema({vol.Optional(ATTR_MESSAGE): cv.string}),
    )
    hass.services.async_register(DOMAIN, SERVICE_FETCH, fetch, schema=vol.Schema({}))
    hass.services.async_register(DOMAIN, SERVICE_PULL, pull, schema=vol.Schema({}))
