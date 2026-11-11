"""Config and options flows."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
)
import voluptuous as vol

from .const import (
    BRANCH_RE,
    CONF_AUTHOR_EMAIL,
    CONF_AUTHOR_NAME,
    CONF_BRANCH,
    CONF_PRIVATE_KEY,
    CONF_PUBLIC_KEY,
    CONF_REMOTE_URL,
    DEFAULT_AUTHOR_EMAIL,
    DEFAULT_AUTHOR_NAME,
    DEFAULT_BRANCH,
    DOMAIN,
    PROTECTED_ENTRIES,
    REMOTE_URL_RE,
)
from .errors import GitConfigError
from .git_ops import GitRepo
from .ignore import normalize_entry
from .keys import async_new_keypair, async_store_keypair

_LOGGER = logging.getLogger(__name__)

_KNOWN_ERRORS = {
    "auth_failed",
    "host_key_mismatch",
    "remote_mismatch",
    "branch_mismatch",
    "unrelated_history",
    "not_found",
}


def _author_schema(name: str, email: str) -> dict[Any, Any]:
    return {
        vol.Required(CONF_AUTHOR_NAME, default=name): str,
        vol.Required(CONF_AUTHOR_EMAIL, default=email): str,
    }


def _author_valid(user_input: dict[str, Any]) -> bool:
    name = user_input[CONF_AUTHOR_NAME].strip()
    email = user_input[CONF_AUTHOR_EMAIL].strip()
    return bool(name and email) and not any(c in name + email for c in "<>\n\r")


class GitConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up the Config Repo."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._input: dict[str, Any] = {}
        self._private = ""
        self._public = ""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> GitConfigOptionsFlow:
        """Return the options flow."""
        return GitConfigOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect remote, branch and commit author."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_REMOTE_URL] = user_input[CONF_REMOTE_URL].strip()
            user_input[CONF_BRANCH] = user_input[CONF_BRANCH].strip()
            if not REMOTE_URL_RE.match(user_input[CONF_REMOTE_URL]):
                errors[CONF_REMOTE_URL] = "invalid_url"
            elif not BRANCH_RE.match(user_input[CONF_BRANCH]):
                errors[CONF_BRANCH] = "invalid_branch"
            elif not _author_valid(user_input):
                errors["base"] = "invalid_author"
            else:
                self._input = user_input
                self._private, self._public = await async_new_keypair(self.hass)
                return await self.async_step_key()

        defaults = user_input or {}
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_REMOTE_URL, default=defaults.get(CONF_REMOTE_URL, "")): str,
                    vol.Required(CONF_BRANCH, default=defaults.get(CONF_BRANCH, DEFAULT_BRANCH)): str,
                    **_author_schema(
                        defaults.get(CONF_AUTHOR_NAME, DEFAULT_AUTHOR_NAME),
                        defaults.get(CONF_AUTHOR_EMAIL, DEFAULT_AUTHOR_EMAIL),
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_key(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the Deploy Key, then verify access and adopt the directory."""
        errors: dict[str, str] = {}
        if user_input is not None:
            repo = GitRepo(
                self.hass.config.config_dir,
                self._input[CONF_REMOTE_URL],
                self._input[CONF_BRANCH],
                self._input[CONF_AUTHOR_NAME],
                self._input[CONF_AUTHOR_EMAIL],
                self._private,
            )
            try:
                await self.hass.async_add_executor_job(repo.adopt)
            except GitConfigError as err:
                _LOGGER.warning("Adopting the configuration directory failed: %s", err)
                errors["base"] = err.code if err.code in _KNOWN_ERRORS else "operation_failed"
            else:
                return self.async_create_entry(
                    title=self._input[CONF_REMOTE_URL],
                    data={
                        CONF_REMOTE_URL: self._input[CONF_REMOTE_URL],
                        CONF_BRANCH: self._input[CONF_BRANCH],
                        CONF_PRIVATE_KEY: self._private,
                        CONF_PUBLIC_KEY: self._public,
                    },
                    options={
                        CONF_AUTHOR_NAME: self._input[CONF_AUTHOR_NAME],
                        CONF_AUTHOR_EMAIL: self._input[CONF_AUTHOR_EMAIL],
                    },
                )
        return self.async_show_form(
            step_id="key",
            description_placeholders={"public_key": self._public},
            errors=errors,
        )


class GitConfigOptionsFlow(OptionsFlow):
    """Edit ignore entries, commit author and the Deploy Key."""

    def __init__(self) -> None:
        """Initialize."""
        self._private = ""
        self._public = ""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the menu."""
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        return self.async_show_menu(step_id="init", menu_options=["ignore", "author", "key"])

    async def async_step_ignore(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add and remove entries in the Managed Ignore Block."""
        repo: GitRepo = self.config_entry.runtime_data.repo
        errors: dict[str, str] = {}
        current = await self.hass.async_add_executor_job(repo.ignore_entries)

        if user_input is not None:
            lines = [
                line for line in user_input.get("add", "").splitlines() if line.strip()
            ]
            try:
                add = [normalize_entry(line) for line in lines]
            except ValueError:
                errors["add"] = "invalid_entry"
            else:
                await self.hass.async_add_executor_job(
                    repo.update_ignore, add, user_input.get("remove", [])
                )
                return self.async_create_entry(data=dict(self.config_entry.options))

        return self.async_show_form(
            step_id="ignore",
            data_schema=vol.Schema(
                {
                    vol.Optional("remove"): SelectSelector(
                        SelectSelectorConfig(options=current, multiple=True)
                    ),
                    vol.Optional("add"): TextSelector(TextSelectorConfig(multiline=True)),
                }
            ),
            description_placeholders={"protected": ", ".join(PROTECTED_ENTRIES)},
            errors=errors,
        )

    async def async_step_author(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the commit author."""
        errors: dict[str, str] = {}
        options = self.config_entry.options
        if user_input is not None:
            if _author_valid(user_input):
                return self.async_create_entry(data={**options, **user_input})
            errors["base"] = "invalid_author"
        return self.async_show_form(
            step_id="author",
            data_schema=vol.Schema(
                _author_schema(
                    options.get(CONF_AUTHOR_NAME, DEFAULT_AUTHOR_NAME),
                    options.get(CONF_AUTHOR_EMAIL, DEFAULT_AUTHOR_EMAIL),
                )
            ),
            errors=errors,
        )

    async def async_step_key(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Regenerate the Deploy Key and show its public half."""
        if not self._private:
            self._private, self._public = await async_new_keypair(self.hass)
        if user_input is not None:
            async_store_keypair(self.hass, self.config_entry, self._private, self._public)
            return self.async_create_entry(data=dict(self.config_entry.options))
        return self.async_show_form(
            step_id="key", description_placeholders={"public_key": self._public}
        )
