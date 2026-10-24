"""国家电网电力 integration backed by the 网上国网 Android App API."""

from __future__ import annotations

import asyncio
import logging
from functools import partial

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SgccPowerAppApi
from .compat import LegacyStoreWriter
from .const import (
    CONF_LOGIN_SESSION,
    CONF_PRICE_PER_KWH,
    CONF_SYNTHETIC_DEVICE,
    CONF_TIERED_PRICING,
    DEFAULT_PRICE_PER_KWH,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import SgccPowerDataCoordinator, SgccPowerRuntimeData
from .diagnostics import async_get_config_entry_diagnostics
from .models import DeviceProfile, LoginSession
from .statistics import StatisticsImporter
from .synthetic_device import build_device_profile

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "async_get_config_entry_diagnostics",
    "async_setup_entry",
    "async_unload_entry",
]


async def _async_handle_refresh(hass: HomeAssistant, call: ServiceCall) -> None:
    """sgcc_power.refresh：立即拉取所有账户的最新数据。"""
    runtimes: dict[str, SgccPowerRuntimeData] = hass.data.get(DOMAIN, {})
    targets = [r.coordinator.async_request_refresh() for r in runtimes.values()]
    if not targets:
        _LOGGER.warning("sgcc_power.refresh 被调用，但当前没有已加载的账户")
        return
    await asyncio.gather(*targets)


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "refresh"):
        return

    async def _async_refresh(call: ServiceCall) -> None:
        await _async_handle_refresh(hass, call)

    # 必须注册协程函数本体：lambda 包装会被 HA 当作 callback 丢进执行器，
    # 返回的协程无人 await，服务实际不生效。
    hass.services.async_register(DOMAIN, "refresh", _async_refresh)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one 国家电网电力 account."""

    async def async_profile_provider() -> DeviceProfile:
        profile, updated_state = await hass.async_add_executor_job(
            partial(build_device_profile, entry.data[CONF_SYNTHETIC_DEVICE])
        )
        if updated_state != entry.data[CONF_SYNTHETIC_DEVICE]:
            hass.config_entries.async_update_entry(
                entry,
                data={**entry.data, CONF_SYNTHETIC_DEVICE: updated_state},
            )
        return profile

    profile = await async_profile_provider()
    api = SgccPowerAppApi(
        async_get_clientsession(hass),
        username=entry.data[CONF_USERNAME],
        password=str(entry.data.get(CONF_PASSWORD, "")),
        profile=profile,
        login_session=LoginSession.from_dict(entry.data.get(CONF_LOGIN_SESSION)),
        profile_provider=async_profile_provider,
    )
    statistics_importer = StatisticsImporter(
        hass,
        price=float(entry.options.get(CONF_PRICE_PER_KWH, DEFAULT_PRICE_PER_KWH)),
        tiered=bool(entry.options.get(CONF_TIERED_PRICING, False)),
    )
    await statistics_importer.async_load()
    coordinator = SgccPowerDataCoordinator(
        hass,
        entry,
        api,
        legacy_writer=LegacyStoreWriter(hass),
        statistics_importer=statistics_importer,
    )
    await coordinator.async_config_entry_first_refresh()
    remove_update_listener = entry.add_update_listener(_async_reload_entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = SgccPowerRuntimeData(
        api=api,
        coordinator=coordinator,
        remove_update_listener=remove_update_listener,
    )
    _register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry without closing Home Assistant's shared session."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    runtime = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if runtime is not None:
        runtime.remove_update_listener()
    if not hass.data.get(DOMAIN) and hass.services.has_service(DOMAIN, "refresh"):
        hass.services.async_remove(DOMAIN, "refresh")
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload for options changes, not routine session/cache persistence."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime is None or runtime.coordinator.configured_options == entry.options:
        return
    runtime.coordinator.configured_options = dict(entry.options)
    await hass.config_entries.async_reload(entry.entry_id)
