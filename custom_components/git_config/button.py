"""Buttons: Commit & Push, Fetch, Fetch & Pull."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import GitConfigEntry, GitCoordinator
from .entity import GitEntity


@dataclass(frozen=True, kw_only=True)
class GitButtonDescription(ButtonEntityDescription):
    """Button with an action on the coordinator."""

    press_fn: Callable[[GitCoordinator], Awaitable[object]]


BUTTONS = (
    GitButtonDescription(
        key="commit_push",
        translation_key="commit_push",
        press_fn=lambda c: c.async_commit_push(),
    ),
    GitButtonDescription(
        key="fetch",
        translation_key="fetch",
        press_fn=lambda c: c.async_fetch(),
    ),
    GitButtonDescription(
        key="pull",
        translation_key="pull",
        press_fn=lambda c: c.async_pull(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GitConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up buttons."""
    async_add_entities(GitButton(entry.runtime_data, d) for d in BUTTONS)


class GitButton(GitEntity, ButtonEntity):
    """A git action button."""

    entity_description: GitButtonDescription

    def __init__(self, coordinator: GitCoordinator, description: GitButtonDescription) -> None:
        """Initialize."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Run the action."""
        await self.entity_description.press_fn(self.coordinator)
