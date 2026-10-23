"""DataUpdateCoordinator for 国家电网电力 electricity data."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    SgccPowerApiError,
    SgccPowerAppApi,
    SgccPowerAuthenticationError,
    SgccPowerNetworkError,
)
from .compat import LegacyStoreWriter
from .const import (
    CONF_AUTH_ERROR,
    CONF_COMPAT_LEGACY_STORE,
    CONF_HISTORY_MONTHS,
    CONF_LOGIN_SESSION,
    CONF_UPDATE_INTERVAL_HOURS,
    DEFAULT_HISTORY_MONTHS,
    DEFAULT_UPDATE_INTERVAL_HOURS,
    DOMAIN,
)
from .models import AccountUsage

_LOGGER = logging.getLogger(__name__)


def _auth_error_message(error: SgccPowerAuthenticationError) -> str:
    """Keep the upstream errors that led to a failed automatic login."""
    chain: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, SgccPowerApiError):
            chain.append(f"[{current.source} {current.code}] {current.message}")
        current = current.__cause__
    return " → ".join(reversed(chain))


class SgccPowerDataCoordinator(DataUpdateCoordinator[dict[str, AccountUsage]]):
    """Refresh all power accounts while sharing one App login session."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: SgccPowerAppApi,
        legacy_writer: LegacyStoreWriter | None = None,
    ) -> None:
        self.entry = entry
        self.api = api
        self.legacy_writer = legacy_writer
        self.configured_options = dict(entry.options)
        hours = int(
            entry.options.get(CONF_UPDATE_INTERVAL_HOURS, DEFAULT_UPDATE_INTERVAL_HOURS)
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(hours=max(6, min(hours, 24))),
        )

    async def _async_update_data(self) -> dict[str, AccountUsage]:
        months = int(
            self.entry.options.get(CONF_HISTORY_MONTHS, DEFAULT_HISTORY_MONTHS)
        )
        auth_error: str | None = None
        succeeded = False
        try:
            result = await self.api.async_query_history(months=months)
        except SgccPowerAuthenticationError as error:
            auth_error = _auth_error_message(error)
            raise ConfigEntryAuthFailed(auth_error) from error
        except (SgccPowerNetworkError, SgccPowerApiError) as error:
            raise UpdateFailed(str(error)) from error
        else:
            succeeded = True
            if self.entry.options.get(CONF_COMPAT_LEGACY_STORE, True):
                if self.legacy_writer is not None:
                    await self.legacy_writer.async_write(result)
            return result
        finally:
            # Login can succeed before an electricity query fails. Persist the
            # new session, or an invalidation, independently of query success.
            data = dict(self.entry.data)
            if self.api.login_session is None:
                data.pop(CONF_LOGIN_SESSION, None)
            else:
                data[CONF_LOGIN_SESSION] = self.api.login_session.as_dict()
            if succeeded:
                data.pop(CONF_AUTH_ERROR, None)
            elif auth_error is not None:
                data[CONF_AUTH_ERROR] = auth_error
            if data != self.entry.data:
                self.hass.config_entries.async_update_entry(self.entry, data=data)


@dataclass
class SgccPowerRuntimeData:
    api: SgccPowerAppApi
    coordinator: SgccPowerDataCoordinator
    remove_update_listener: Callable[[], None]
