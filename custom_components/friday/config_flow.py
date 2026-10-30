"""Config flow: one main entry, one subentry per agent."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigSubentry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.helpers.selector import (
    AreaSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_AREA_ID, CONF_HOST, CONF_PORT, CONF_PROXMOX_NODE, CONF_PROXMOX_TOKEN_ID,
    CONF_PROXMOX_TOKEN_SECRET, CONF_PROXMOX_URL, CONF_PROXMOX_VMID, CONF_TOKEN,
    DEFAULT_PORT_SATELLITE, DEFAULT_PORT_SERVER, DEFAULT_PROXMOX_NODE, DEFAULT_PROXMOX_URL, DOMAIN,
)

_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _proxmox_schema(defaults: dict[str, Any]) -> dict:
    """Optional Proxmox fields (the Reboot VM button exists only when all are filled)."""
    def d(key: str, fallback: Any = None) -> dict:
        value = defaults.get(key, fallback)
        return {"suggested_value": value} if value not in (None, "") else {}

    return {
        vol.Optional(CONF_PROXMOX_URL, description=d(CONF_PROXMOX_URL, DEFAULT_PROXMOX_URL)): str,
        vol.Optional(CONF_PROXMOX_NODE, description=d(CONF_PROXMOX_NODE, DEFAULT_PROXMOX_NODE)): str,
        vol.Optional(CONF_PROXMOX_VMID, description=d(CONF_PROXMOX_VMID)): int,
        vol.Optional(CONF_PROXMOX_TOKEN_ID, description=d(CONF_PROXMOX_TOKEN_ID)): str,
        vol.Optional(CONF_PROXMOX_TOKEN_SECRET): _PASSWORD,
    }


def _proxmox_data(user_input: dict[str, Any], old: dict[str, Any]) -> dict[str, Any]:
    """Filled Proxmox fields; a blank secret keeps the stored one."""
    out = {k: v for k, v in user_input.items()
           if k.startswith("proxmox_") and v not in (None, "")}
    if CONF_PROXMOX_TOKEN_SECRET not in out and old.get(CONF_PROXMOX_TOKEN_SECRET):
        out[CONF_PROXMOX_TOKEN_SECRET] = old[CONF_PROXMOX_TOKEN_SECRET]
    return out


class FridayConfigFlow(ConfigFlow, domain=DOMAIN):
    """The single F.R.I.D.A.Y. entry (no fields)."""

    VERSION = 1
    _discovered: dict[str, Any]

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        # single_config_entry is not set in the manifest: it would also block discovery.
        if self.hass.config_entries.async_entries(DOMAIN):
            return self.async_abort(reason="single_instance_allowed")
        await self.async_set_unique_id(DOMAIN)
        if user_input is not None:
            return self.async_create_entry(title="F.R.I.D.A.Y.", data={})
        return self.async_show_form(step_id="user")

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {"server": ServerSubentryFlow, "satellite": SatelliteSubentryFlow}

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """A satellite agent announced itself: offer to add it as a subentry."""
        props = discovery_info.properties
        if props.get("kind") != "satellite":
            return self.async_abort(reason="not_supported")
        entries = self.hass.config_entries.async_entries(DOMAIN)
        if not entries:
            return self.async_abort(reason="no_entry")
        try:
            port = int(props.get("port") or discovery_info.port or DEFAULT_PORT_SATELLITE)
        except ValueError:
            port = DEFAULT_PORT_SATELLITE
        host = str(discovery_info.ip_address)
        uid = f"{host}:{port}"
        for sub in entries[0].subentries.values():
            if sub.unique_id == uid:
                return self.async_abort(reason="already_configured")
        self._discovered = {CONF_HOST: host, CONF_PORT: port}
        self.context["title_placeholders"] = {"name": props.get("id", host)}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            entry = self.hass.config_entries.async_entries(DOMAIN)[0]
            host, port = self._discovered[CONF_HOST], self._discovered[CONF_PORT]
            data = {CONF_HOST: host, CONF_PORT: port, CONF_TOKEN: user_input[CONF_TOKEN]}
            if user_input.get(CONF_AREA_ID):
                data[CONF_AREA_ID] = user_input[CONF_AREA_ID]
            self.hass.config_entries.async_add_subentry(
                entry,
                ConfigSubentry(
                    data=MappingProxyType(data), subentry_type="satellite",
                    title="F.R.I.D.A.Y. Satellite", unique_id=f"{host}:{port}",
                ),
            )
            return self.async_abort(reason="satellite_added")
        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={"host": self._discovered[CONF_HOST]},
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TOKEN): _PASSWORD,
                    vol.Optional(CONF_AREA_ID): AreaSelector(),
                }
            ),
        )


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
                    **_proxmox_data(user_input, {}),
                },
                unique_id=f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}",
            )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_PORT, default=DEFAULT_PORT_SERVER): int,
                    vol.Required(CONF_TOKEN): _PASSWORD,
                    **_proxmox_schema({}),
                }
            ),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Fill or change the connection and Proxmox fields of an existing Server."""
        subentry = self._get_reconfigure_subentry()
        old = dict(subentry.data)
        if user_input is not None:
            data = {
                CONF_HOST: user_input[CONF_HOST],
                CONF_PORT: user_input[CONF_PORT],
                CONF_TOKEN: user_input.get(CONF_TOKEN) or old[CONF_TOKEN],
                **_proxmox_data(user_input, old),
            }
            return self.async_update_and_abort(
                self._get_entry(), subentry, data=data,
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=old[CONF_HOST]): str,
                    vol.Required(CONF_PORT, default=old[CONF_PORT]): int,
                    vol.Optional(CONF_TOKEN): _PASSWORD,
                    **_proxmox_schema(old),
                }
            ),
        )


class SatelliteSubentryFlow(ConfigSubentryFlow):
    """Add a voice satellite as an agent (also reachable from zeroconf discovery)."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            data = {
                CONF_HOST: user_input[CONF_HOST],
                CONF_PORT: user_input[CONF_PORT],
                CONF_TOKEN: user_input[CONF_TOKEN],
            }
            if user_input.get(CONF_AREA_ID):
                data[CONF_AREA_ID] = user_input[CONF_AREA_ID]
            return self.async_create_entry(
                title="F.R.I.D.A.Y. Satellite", data=data,
                unique_id=f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}",
            )
        return self.async_show_form(step_id="user", data_schema=self._schema({}))

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        old = dict(subentry.data)
        if user_input is not None:
            data = {
                CONF_HOST: user_input[CONF_HOST],
                CONF_PORT: user_input[CONF_PORT],
                CONF_TOKEN: user_input.get(CONF_TOKEN) or old[CONF_TOKEN],
            }
            if user_input.get(CONF_AREA_ID):
                data[CONF_AREA_ID] = user_input[CONF_AREA_ID]
            return self.async_update_and_abort(self._get_entry(), subentry, data=data)
        return self.async_show_form(
            step_id="reconfigure", data_schema=self._schema(old, token_optional=True)
        )

    @staticmethod
    def _schema(old: dict[str, Any], token_optional: bool = False) -> vol.Schema:
        token = vol.Optional(CONF_TOKEN) if token_optional else vol.Required(CONF_TOKEN)
        area = vol.Optional(CONF_AREA_ID, description=(
            {"suggested_value": old[CONF_AREA_ID]} if old.get(CONF_AREA_ID) else {}))
        host = vol.Required(CONF_HOST, **({"default": old[CONF_HOST]} if old else {}))
        return vol.Schema({
            host: str,
            vol.Required(CONF_PORT, default=old.get(CONF_PORT, DEFAULT_PORT_SATELLITE)): int,
            token: _PASSWORD,
            area: AreaSelector(),
        })
