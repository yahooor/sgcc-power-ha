"""Password-first config flow with on-demand new-device verification.

Adapted from stevenjoezhang/hass-state-grid (MIT License).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from functools import partial
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    SgccPowerApiError,
    SgccPowerAppApi,
    SgccPowerAuthenticationError,
    SgccPowerDeviceVerificationRequired,
    SgccPowerInteractiveChallengeRequired,
    SgccPowerNetworkError,
)
from .const import (
    CONF_AUTH_ERROR,
    CONF_COMPAT_LEGACY_STORE,
    CONF_HISTORY_MONTHS,
    CONF_LOGIN_SESSION,
    CONF_PRICE_PER_KWH,
    CONF_SYNTHETIC_DEVICE,
    CONF_UPDATE_INTERVAL_HOURS,
    DEFAULT_HISTORY_MONTHS,
    DEFAULT_PRICE_PER_KWH,
    DEFAULT_UPDATE_INTERVAL_HOURS,
    DOMAIN,
)
from .models import DeviceProfile
from .synthetic_device import build_device_profile, create_device_state

CONF_VERIFICATION_CODE = "verification_code"
CONF_CONTINUE = "continue"
_LOGGER = logging.getLogger(__name__)


def _error_placeholders(error: Exception) -> dict[str, str]:
    """Return complete error details without truncating the upstream message."""
    if isinstance(error, SgccPowerApiError):
        source = error.source
        code = error.code
        message = error.message
    elif isinstance(error, SgccPowerNetworkError):
        source = "transport"
        code = type(error).__name__
        message = str(error)
    else:
        source = "client"
        code = type(error).__name__
        message = str(error)
    return {
        "error_source": str(source),
        "error_code": str(code),
        "error_type": type(error).__name__,
        "error_message": str(message),
    }


def _set_flow_error(
    errors: dict[str, str],
    placeholders: dict[str, str],
    key: str,
    error: Exception,
    *,
    operation: str,
    unexpected: bool = False,
) -> None:
    """Expose complete details and log metadata without request credentials."""
    details = _error_placeholders(error)
    errors["base"] = key
    placeholders.update(details)
    log = _LOGGER.exception if unexpected else _LOGGER.warning
    log(
        "%s failed: source=%s code=%s type=%s message=%s",
        operation,
        details["error_source"],
        details["error_code"],
        details["error_type"],
        details["error_message"],
    )


def _password_selector() -> selector.TextSelector:
    return selector.TextSelector(
        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
    )


class SgccPowerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up password login and request device SMS only when challenged."""

    VERSION = 1

    def __init__(self) -> None:
        self._api: SgccPowerAppApi | None = None
        self._pending: dict[str, Any] = {}
        self._code_key = ""
        self._reauth_entry: config_entries.ConfigEntry | None = None
        self._reauth_error = ""

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return SgccPowerOptionsFlow(config_entry)

    async def _build_api(
        self,
        *,
        username: str,
        password: str,
        state: Mapping[str, Any],
    ) -> None:
        self._pending = {
            CONF_USERNAME: username,
            CONF_PASSWORD: password,
            CONF_SYNTHETIC_DEVICE: dict(state),
        }
        profile = await self._async_device_profile()
        self._api = SgccPowerAppApi(
            async_get_clientsession(self.hass),
            username=username,
            password=password,
            profile=profile,
            profile_provider=self._async_device_profile,
        )

    async def _async_device_profile(self) -> DeviceProfile:
        """Refresh the token even if a reauth form has been open for days."""
        profile, state = await self.hass.async_add_executor_job(
            partial(build_device_profile, self._pending[CONF_SYNTHETIC_DEVICE])
        )
        self._pending[CONF_SYNTHETIC_DEVICE] = state
        return profile

    async def _send_device_verification_sms(self):
        assert self._api is not None
        self._code_key = await self._api.async_send_device_verification_sms()
        return await self.async_step_device_verification()

    async def _finish(self):
        assert self._api is not None and self._api.login_session is not None
        data = {
            **self._pending,
            CONF_LOGIN_SESSION: self._api.login_session.as_dict(),
        }
        unique_id = self._api.login_session.user_id or self._pending[CONF_USERNAME]
        await self.async_set_unique_id(unique_id)
        if self._reauth_entry is not None:
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            return self.async_update_reload_and_abort(
                self._reauth_entry, data_updates={**data, CONF_AUTH_ERROR: None}
            )
        self._abort_if_unique_id_configured()
        username = str(self._pending[CONF_USERNAME])
        return self.async_create_entry(
            title=f"国家电网电力 · {username[-4:]}", data=data
        )

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            try:
                state = (
                    self._pending[CONF_SYNTHETIC_DEVICE]
                    if self._pending.get(CONF_USERNAME) == user_input[CONF_USERNAME]
                    else create_device_state()
                )
                await self._build_api(
                    username=user_input[CONF_USERNAME],
                    password=user_input[CONF_PASSWORD],
                    state=state,
                )
                assert self._api is not None
                await self._api.async_login()
            except SgccPowerDeviceVerificationRequired:
                try:
                    return await self._send_device_verification_sms()
                except SgccPowerNetworkError as error:
                    _set_flow_error(
                        errors,
                        placeholders,
                        "cannot_connect",
                        error,
                        operation="send device verification SMS",
                    )
                except SgccPowerApiError as error:
                    _set_flow_error(
                        errors,
                        placeholders,
                        "cannot_send_verification_code",
                        error,
                        operation="send device verification SMS",
                    )
            except SgccPowerInteractiveChallengeRequired as error:
                _set_flow_error(
                    errors,
                    placeholders,
                    "interactive_challenge_unsupported",
                    error,
                    operation="initial password login",
                )
            except SgccPowerAuthenticationError as error:
                _set_flow_error(
                    errors,
                    placeholders,
                    "invalid_auth",
                    error,
                    operation="initial password login",
                )
            except SgccPowerNetworkError as error:
                _set_flow_error(
                    errors,
                    placeholders,
                    "cannot_connect",
                    error,
                    operation="initial password login",
                )
            except SgccPowerApiError as error:
                _set_flow_error(
                    errors,
                    placeholders,
                    "server_error",
                    error,
                    operation="initial password login",
                )
            except ValueError as error:
                _set_flow_error(
                    errors,
                    placeholders,
                    "local_error",
                    error,
                    operation="build initial login request",
                )
            except Exception as error:  # noqa: BLE001
                _set_flow_error(
                    errors,
                    placeholders,
                    "unknown",
                    error,
                    operation="initial password login",
                    unexpected=True,
                )
            else:
                return await self._finish()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): _password_selector(),
                }
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_device_verification(self, user_input=None):
        errors: dict[str, str] = {}
        placeholders = {"phone_suffix": str(self._pending.get(CONF_USERNAME, ""))[-4:]}
        if user_input is not None:
            assert self._api is not None
            try:
                await self._api.async_login(
                    verification_code=user_input[CONF_VERIFICATION_CODE],
                    code_key=self._code_key,
                )
            except SgccPowerInteractiveChallengeRequired as error:
                _set_flow_error(
                    errors,
                    placeholders,
                    "interactive_challenge_unsupported",
                    error,
                    operation="password login after device verification",
                )
            except SgccPowerAuthenticationError as error:
                _set_flow_error(
                    errors,
                    placeholders,
                    "invalid_verification_code",
                    error,
                    operation="password login after device verification",
                )
            except SgccPowerNetworkError as error:
                _set_flow_error(
                    errors,
                    placeholders,
                    "cannot_connect",
                    error,
                    operation="password login after device verification",
                )
            except SgccPowerApiError as error:
                _set_flow_error(
                    errors,
                    placeholders,
                    "server_error",
                    error,
                    operation="password login after device verification",
                )
            except ValueError as error:
                _set_flow_error(
                    errors,
                    placeholders,
                    "local_error",
                    error,
                    operation="validate device verification code",
                )
            except Exception as error:  # noqa: BLE001
                _set_flow_error(
                    errors,
                    placeholders,
                    "unknown",
                    error,
                    operation="password login after device verification",
                    unexpected=True,
                )
            else:
                self._code_key = ""
                return await self._finish()
        return self.async_show_form(
            step_id="device_verification",
            data_schema=vol.Schema({vol.Required(CONF_VERIFICATION_CODE): str}),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]):
        self._reauth_entry = self._get_reauth_entry()
        data = self._reauth_entry.data
        self._reauth_error = str(data.get(CONF_AUTH_ERROR) or "")
        await self._build_api(
            username=str(data[CONF_USERNAME]),
            password=str(data.get(CONF_PASSWORD, "")),
            state=data[CONF_SYNTHETIC_DEVICE],
        )
        if not self._pending[CONF_PASSWORD]:
            return await self.async_step_reauth_credentials()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {"auth_error": self._reauth_error}
        if user_input is not None:
            assert self._api is not None
            try:
                await self._api.async_login()
            except SgccPowerDeviceVerificationRequired:
                try:
                    return await self._send_device_verification_sms()
                except SgccPowerNetworkError as error:
                    _set_flow_error(
                        errors,
                        placeholders,
                        "cannot_connect",
                        error,
                        operation="send reauthentication SMS",
                    )
                except SgccPowerApiError as error:
                    _set_flow_error(
                        errors,
                        placeholders,
                        "cannot_send_verification_code",
                        error,
                        operation="send reauthentication SMS",
                    )
            except SgccPowerInteractiveChallengeRequired as error:
                _set_flow_error(
                    errors,
                    placeholders,
                    "interactive_challenge_unsupported",
                    error,
                    operation="automatic password reauthentication",
                )
            except SgccPowerAuthenticationError as error:
                return await self.async_step_reauth_credentials(previous_error=error)
            except SgccPowerNetworkError as error:
                _set_flow_error(
                    errors,
                    placeholders,
                    "cannot_connect",
                    error,
                    operation="automatic password reauthentication",
                )
            except SgccPowerApiError as error:
                _set_flow_error(
                    errors,
                    placeholders,
                    "server_error",
                    error,
                    operation="automatic password reauthentication",
                )
            except ValueError as error:
                _set_flow_error(
                    errors,
                    placeholders,
                    "local_error",
                    error,
                    operation="build automatic reauthentication request",
                )
            except Exception as error:  # noqa: BLE001
                _set_flow_error(
                    errors,
                    placeholders,
                    "unknown",
                    error,
                    operation="automatic password reauthentication",
                    unexpected=True,
                )
            else:
                return await self._finish()
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_CONTINUE, default=True): bool}),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_reauth_credentials(
        self,
        user_input=None,
        *,
        previous_error: SgccPowerAuthenticationError | None = None,
    ):
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if previous_error is not None:
            _set_flow_error(
                errors,
                placeholders,
                "invalid_auth",
                previous_error,
                operation="automatic password reauthentication",
            )
        if user_input is not None:
            assert self._api is not None
            password = str(user_input[CONF_PASSWORD])
            self._api.password = password
            self._pending[CONF_PASSWORD] = password
            try:
                await self._api.async_login()
            except SgccPowerDeviceVerificationRequired:
                try:
                    return await self._send_device_verification_sms()
                except SgccPowerNetworkError as error:
                    _set_flow_error(
                        errors,
                        placeholders,
                        "cannot_connect",
                        error,
                        operation="send device verification SMS after password update",
                    )
                except SgccPowerApiError as error:
                    _set_flow_error(
                        errors,
                        placeholders,
                        "cannot_send_verification_code",
                        error,
                        operation="send device verification SMS after password update",
                    )
            except SgccPowerInteractiveChallengeRequired as error:
                _set_flow_error(
                    errors,
                    placeholders,
                    "interactive_challenge_unsupported",
                    error,
                    operation="password login with updated credentials",
                )
            except SgccPowerAuthenticationError as error:
                _set_flow_error(
                    errors,
                    placeholders,
                    "invalid_auth",
                    error,
                    operation="password login with updated credentials",
                )
            except SgccPowerNetworkError as error:
                _set_flow_error(
                    errors,
                    placeholders,
                    "cannot_connect",
                    error,
                    operation="password login with updated credentials",
                )
            except SgccPowerApiError as error:
                _set_flow_error(
                    errors,
                    placeholders,
                    "server_error",
                    error,
                    operation="password login with updated credentials",
                )
            except ValueError as error:
                _set_flow_error(
                    errors,
                    placeholders,
                    "local_error",
                    error,
                    operation="build login request with updated credentials",
                )
            except Exception as error:  # noqa: BLE001
                _set_flow_error(
                    errors,
                    placeholders,
                    "unknown",
                    error,
                    operation="password login with updated credentials",
                    unexpected=True,
                )
            else:
                return await self._finish()
        return self.async_show_form(
            step_id="reauth_credentials",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): _password_selector()}),
            errors=errors,
            description_placeholders=placeholders,
        )


class SgccPowerOptionsFlow(config_entries.OptionsFlow):
    """Configure polling depth, interval and the legacy compatibility writer."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self.entry = entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_HISTORY_MONTHS,
                        default=self.entry.options.get(
                            CONF_HISTORY_MONTHS, DEFAULT_HISTORY_MONTHS
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=3)),
                    vol.Required(
                        CONF_UPDATE_INTERVAL_HOURS,
                        default=self.entry.options.get(
                            CONF_UPDATE_INTERVAL_HOURS,
                            DEFAULT_UPDATE_INTERVAL_HOURS,
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=6, max=24)),
                    vol.Required(
                        CONF_COMPAT_LEGACY_STORE,
                        default=self.entry.options.get(CONF_COMPAT_LEGACY_STORE, True),
                    ): bool,
                    vol.Optional(
                        CONF_PRICE_PER_KWH,
                        description={
                            "suggested_value": self.entry.options.get(
                                CONF_PRICE_PER_KWH, DEFAULT_PRICE_PER_KWH
                            )
                        },
                    ): vol.All(vol.Coerce(float), vol.Range(min=0, max=3)),
                }
            ),
        )
