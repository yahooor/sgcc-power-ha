"""国家电网电力 integration backed by the 网上国网 Android App API."""

from __future__ import annotations

from functools import partial

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SgccPowerAppApi
from .compat import LegacyStoreWriter
from .const import (
    CONF_LOGIN_SESSION,
    CONF_SYNTHETIC_DEVICE,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import SgccPowerDataCoordinator, SgccPowerRuntimeData
from .models import DeviceProfile, LoginSession
from .synthetic_device import build_device_profile


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
    coordinator = SgccPowerDataCoordinator(
        hass, entry, api, legacy_writer=LegacyStoreWriter(hass)
    )
    await coordinator.async_config_entry_first_refresh()
    remove_update_listener = entry.add_update_listener(_async_reload_entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = SgccPowerRuntimeData(
        api=api,
        coordinator=coordinator,
        remove_update_listener=remove_update_listener,
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry without closing Home Assistant's shared session."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    runtime = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if runtime is not None:
        runtime.remove_update_listener()
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload for options changes, not routine session/cache persistence."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime is None or runtime.coordinator.configured_options == entry.options:
        return
    runtime.coordinator.configured_options = dict(entry.options)
    await hass.config_entries.async_reload(entry.entry_id)
