"""Config flow for the Vorwerk Kobold integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import KoboldApi, KoboldAuth, KoboldAuthError, KoboldError
from .const import (
    CONF_ID_TOKEN,
    CONF_MARKET,
    CONF_OTP,
    CONF_REFRESH_TOKEN,
    DEFAULT_MARKET,
    DOMAIN,
    MARKETS,
)

_LOGGER = logging.getLogger(__name__)


class KoboldConfigFlow(ConfigFlow, domain=DOMAIN):
    """Log in with the e-mail address of the MyKobold app and a one-time code."""

    VERSION = 1

    def __init__(self) -> None:
        self._email: str = ""
        self._market: str = DEFAULT_MARKET

    def _auth(self) -> KoboldAuth:
        return KoboldAuth(async_get_clientsession(self.hass), self._market)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._email = user_input[CONF_EMAIL].strip().lower()
            self._market = user_input[CONF_MARKET]
            await self.async_set_unique_id(self._email)
            self._abort_if_unique_id_configured()
            errors = await self._send_code()
            if not errors:
                return await self.async_step_code()

        schema = vol.Schema(
            {
                vol.Required(CONF_EMAIL, default=self._email): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="email")
                ),
                vol.Required(CONF_MARKET, default=self._market): SelectSelector(
                    SelectSelectorConfig(
                        options=list(MARKETS),
                        translation_key="market",
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def _send_code(self) -> dict[str, str]:
        try:
            await self._auth().send_code(self._email)
        except KoboldAuthError:
            return {"base": "invalid_email"}
        except KoboldError as err:
            _LOGGER.debug("Sending the code failed: %s", err)
            return {"base": "cannot_connect"}
        return {}

    async def async_step_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                tokens = await self._auth().verify_code(self._email, user_input[CONF_OTP])
                session = async_get_clientsession(self.hass)

                async def _token() -> str:
                    return tokens["id_token"]

                robots = await KoboldApi(session, _token, self._market).robots()
            except KoboldAuthError:
                errors["base"] = "invalid_code"
            except KoboldError as err:
                _LOGGER.debug("Login failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                data = {
                    CONF_EMAIL: self._email,
                    CONF_MARKET: self._market,
                    CONF_ID_TOKEN: tokens["id_token"],
                    CONF_REFRESH_TOKEN: tokens.get("refresh_token"),
                }
                if self.source == SOURCE_REAUTH:
                    return self.async_update_reload_and_abort(
                        self._get_reauth_entry(), data_updates=data
                    )
                if not robots:
                    return self.async_abort(reason="no_robots")
                return self.async_create_entry(title=self._email, data=data)

        return self.async_show_form(
            step_id="code",
            data_schema=vol.Schema({vol.Required(CONF_OTP): str}),
            description_placeholders={"email": self._email},
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        self._email = entry_data[CONF_EMAIL]
        self._market = entry_data.get(CONF_MARKET, DEFAULT_MARKET)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._send_code()
            if not errors:
                return await self.async_step_code()
        return self.async_show_form(
            step_id="reauth_confirm",
            description_placeholders={"email": self._email},
            errors=errors,
        )
