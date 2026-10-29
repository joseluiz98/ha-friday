"""Config flow: one main entry, one subentry per agent."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import CONF_HOST, CONF_PORT, CONF_TOKEN, DEFAULT_PORT_SERVER, DOMAIN


class FridayConfigFlow(ConfigFlow, domain=DOMAIN):
    """The single F.R.I.D.A.Y. entry (no fields)."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="F.R.I.D.A.Y.", data={})
        return self.async_show_form(step_id="user")

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {"server": ServerSubentryFlow}


class ServerSubentryFlow(ConfigSubentryFlow):
    """Add the F.R.I.D.A.Y. server as an agent."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title="F.R.I.D.A.Y. Server",
                data={
                    CONF_HOST: user_input[CONF_HOST],
                    CONF_PORT: user_input[CONF_PORT],
                    CONF_TOKEN: user_input[CONF_TOKEN],
                },
                unique_id=f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}",
            )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_PORT, default=DEFAULT_PORT_SERVER): int,
                    vol.Required(CONF_TOKEN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
        )
