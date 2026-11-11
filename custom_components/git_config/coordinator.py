"""Coordinator: owns the Config Repo, serialises operations and raises Repairs issues."""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.check_config import async_check_ha_config_file
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .apply import CORE_DOMAIN, build_plan
from .const import (
    CONF_AUTHOR_EMAIL,
    CONF_AUTHOR_NAME,
    CONF_BRANCH,
    CONF_PRIVATE_KEY,
    CONF_REMOTE_URL,
    DEFAULT_AUTHOR_EMAIL,
    DEFAULT_AUTHOR_NAME,
    DOMAIN,
    ISSUE_AUTH_FAILED,
    ISSUE_CONFIG_INVALID,
    ISSUE_HOST_KEY_MISMATCH,
    ISSUE_PULL_REFUSED,
    ISSUE_RESTART_REQUIRED,
    ISSUE_SECRETS_IN_HISTORY,
    STATUS_INTERVAL,
)
from .errors import (
    AUTH_FAILED,
    HOST_KEY_MISMATCH,
    PULL_REFUSAL_CODES,
    GitConfigError,
)
from .git_ops import CommitPushResult, GitRepo, PullResult, RepoStatus

_LOGGER = logging.getLogger(__name__)

type GitConfigEntry = ConfigEntry[GitCoordinator]


def build_repo(hass: HomeAssistant, entry: ConfigEntry) -> GitRepo:
    """Create the Config Repo handle from a config entry."""
    options = entry.options
    return GitRepo(
        hass.config.config_dir,
        entry.data[CONF_REMOTE_URL],
        entry.data[CONF_BRANCH],
        options.get(CONF_AUTHOR_NAME, DEFAULT_AUTHOR_NAME),
        options.get(CONF_AUTHOR_EMAIL, DEFAULT_AUTHOR_EMAIL),
        entry.data[CONF_PRIVATE_KEY],
    )


class GitCoordinator(DataUpdateCoordinator[RepoStatus]):
    """Coordinates status polling and user-triggered actions."""

    config_entry: GitConfigEntry

    def __init__(self, hass: HomeAssistant, entry: GitConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=STATUS_INTERVAL,
        )
        self.repo = build_repo(hass, entry)
        self._lock = asyncio.Lock()
        self.last_fetch: datetime | None = None
        self.last_error: str | None = None

    async def _async_setup(self) -> None:
        await self.hass.async_add_executor_job(self.repo.ensure_ignore)

    async def _async_update_data(self) -> RepoStatus:
        async with self._lock:
            try:
                status = await self.hass.async_add_executor_job(self.repo.status)
            except GitConfigError as err:
                raise UpdateFailed(
                    translation_domain=DOMAIN,
                    translation_key=f"error_{err.code}",
                    translation_placeholders={"detail": err.detail},
                ) from err
        self._sync_history_issue(status)
        return status

    # -- actions -------------------------------------------------------------

    async def async_scheduled_fetch(self, _now: datetime | None = None) -> None:
        """Fetch at startup and hourly; failures are recorded, not raised."""
        try:
            await self.async_fetch()
        except HomeAssistantError as err:
            _LOGGER.warning("Scheduled fetch failed: %s", err)

    async def async_fetch(self) -> None:
        """Fetch only."""
        async with self._lock:
            await self._run(self.repo.fetch)
            self.last_fetch = dt_util.utcnow()
        await self.async_refresh()

    async def async_commit_push(self, message: str | None = None) -> CommitPushResult:
        """Commit all Pending Changes and push."""
        async with self._lock:
            result: CommitPushResult = await self._run(self.repo.commit_push, message)
            if result.pushed:
                self.last_fetch = dt_util.utcnow()
        await self.async_refresh()
        return result

    async def async_pull(self) -> PullResult:
        """Fetch, fast-forward and apply."""
        async with self._lock:
            result: PullResult = await self._run(self.repo.pull, pull=True)
            self.last_fetch = dt_util.utcnow()
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_PULL_REFUSED)
            await self._apply(result)
        await self.async_refresh()
        return result

    async def async_ensure_ignore(self) -> None:
        """Re-assert the Managed Ignore Block."""
        await self.hass.async_add_executor_job(self.repo.ensure_ignore)

    # -- internals -------------------------------------------------------------

    async def _run(self, func: Any, *args: Any, pull: bool = False) -> Any:
        try:
            result = await self.hass.async_add_executor_job(func, *args)
        except GitConfigError as err:
            self._record_error(err, pull=pull)
            raise _as_ha_error(err) from err
        self.last_error = None
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_AUTH_FAILED)
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_HOST_KEY_MISMATCH)
        return result

    def _record_error(self, err: GitConfigError, *, pull: bool) -> None:
        self.last_error = f"{err.code}: {err.detail}" if err.detail else err.code
        self.async_update_listeners()
        if err.code == AUTH_FAILED:
            self._create_issue(ISSUE_AUTH_FAILED, fixable=True, detail=err.detail)
        elif err.code == HOST_KEY_MISMATCH:
            self._create_issue(ISSUE_HOST_KEY_MISMATCH, detail=err.detail)
        elif pull and err.code in PULL_REFUSAL_CODES:
            self._create_issue(
                ISSUE_PULL_REFUSED,
                translation_key=f"{ISSUE_PULL_REFUSED}_{err.code}",
                detail=err.detail,
            )

    def _create_issue(
        self,
        issue_id: str,
        *,
        fixable: bool = False,
        translation_key: str | None = None,
        severity: ir.IssueSeverity = ir.IssueSeverity.WARNING,
        **placeholders: str,
    ) -> None:
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=fixable,
            severity=severity,
            translation_key=translation_key or issue_id,
            translation_placeholders=placeholders or None,
        )

    def _sync_history_issue(self, status: RepoStatus) -> None:
        if status.protected_in_history:
            self._create_issue(ISSUE_SECRETS_IN_HISTORY)
        else:
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_SECRETS_IN_HISTORY)

    def _has_reload(self, domain: str) -> bool:
        service = "reload_core_config" if domain == CORE_DOMAIN else "reload"
        return self.hass.services.has_service(domain, service)

    async def _apply(self, result: PullResult) -> None:
        """Bring the running system in line with pulled files."""
        if not result.updated:
            return
        check = await async_check_ha_config_file(self.hass)
        if check.errors:
            self._create_issue(
                ISSUE_CONFIG_INVALID,
                severity=ir.IssueSeverity.ERROR,
                error=check.error_str,
            )
            return
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_CONFIG_INVALID)

        plan = await self.hass.async_add_executor_job(
            build_plan, self.repo.path, result.changed, self._has_reload
        )
        if not plan.needs_restart:
            for domain in sorted(plan.reload_domains):
                service = "reload_core_config" if domain == CORE_DOMAIN else "reload"
                try:
                    await self.hass.services.async_call(domain, service, blocking=True)
                except HomeAssistantError as err:
                    plan.restart_reasons.append(f"{domain} reload failed: {err}")
        if plan.needs_restart:
            self._create_issue(
                ISSUE_RESTART_REQUIRED,
                fixable=True,
                reasons=", ".join(plan.restart_reasons[:5]),
            )


def _as_ha_error(err: GitConfigError) -> HomeAssistantError:
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=f"error_{err.code}",
        translation_placeholders={"detail": err.detail},
    )
