"""Repairs fix flows."""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN, ISSUE_AUTH_FAILED, ISSUE_RESTART_REQUIRED
from .keys import async_new_keypair, async_store_keypair


class RestartFlow(RepairsFlow):
    """Restart Home Assistant to apply pulled changes."""

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> FlowResult:
        """Start the flow."""
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> FlowResult:
        """Confirm and restart."""
        if user_input is not None:
            await self.hass.services.async_call("homeassistant", "restart")
            return self.async_create_entry(data={})
        return self.async_show_form(step_id="confirm")


class RegenerateKeyFlow(RepairsFlow):
    """Replace a rejected Deploy Key."""

    def __init__(self) -> None:
        """Initialize."""
        self._private = ""
        self._public = ""

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> FlowResult:
        """Generate a key and show it."""
        if not self._private:
            self._private, self._public = await async_new_keypair(self.hass)
        if user_input is not None:
            for entry in self.hass.config_entries.async_entries(DOMAIN):
                async_store_keypair(self.hass, entry, self._private, self._public)
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="init", description_placeholders={"public_key": self._public}
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Create the fix flow for an issue."""
    if issue_id == ISSUE_RESTART_REQUIRED:
        return RestartFlow()
    if issue_id == ISSUE_AUTH_FAILED:
        return RegenerateKeyFlow()
    return ConfirmRepairFlow()
