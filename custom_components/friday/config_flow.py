"""Config flow: one main entry, one subentry per agent."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    OptionsFlow,
    ConfigSubentry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.helpers.selector import (
    AreaSelector,
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_AREA_ID, CONF_HOST, CONF_PORT, CONF_PROXMOX_NODE, CONF_PROXMOX_TOKEN_ID,
    CONF_PROXMOX_TOKEN_SECRET, CONF_PROXMOX_URL, CONF_PROXMOX_VMID, CONF_TOKEN,
    DEFAULT_PORT_SATELLITE, DEFAULT_PORT_SERVER, DEFAULT_PROXMOX_NODE, DEFAULT_PROXMOX_URL, DOMAIN,
)
from . import room_climate
from .const import SOURCE_HA_ENTITY, SOURCE_SATELLITE
from .wifi import async_set_wifi

CONF_SSID = "ssid"  # form field names only; neither value is ever stored
CONF_WIFI_PASSWORD = "password"

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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return FridayOptionsFlow()

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
        sat_id = props.get("id") or f"{host}:{port}"
        await self.async_set_unique_id(f"satellite:{sat_id}")
        for sub in entries[0].subentries.values():
            if sub.unique_id in (sat_id, f"{host}:{port}"):
                return self.async_abort(reason="already_configured")
        self._discovered = {CONF_HOST: host, CONF_PORT: port, "id": sat_id}
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
                    title=f"Satellite {self._discovered['id']}", unique_id=self._discovered["id"],
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


DEFAULT_BUDGET_BRL = 150
SPEAKING_RATES = ["slow", "normal", "fast"]


def _select(options: list[str], current: str | None, translation_key: str | None = None) -> SelectSelector:
    opts = list(options)
    if current and current not in opts:
        opts.insert(0, current)
    cfg = {"options": opts, **({"translation_key": translation_key} if translation_key else {})}
    return SelectSelector(SelectSelectorConfig(**cfg))


class FridayOptionsFlow(OptionsFlow):
    """Global settings of the F.R.I.D.A.Y. server: models per role, voice, monthly budget."""

    _connection = None
    _listing: dict[str, Any]
    _models: dict[str, str]

    def _server_connection(self):
        for sub_id, sub in self.config_entry.subentries.items():
            if sub.subentry_type == "server":
                conn = (getattr(self.config_entry, "runtime_data", None) or {}).get(sub_id)
                if conn is not None and conn.connected:
                    return conn
        return None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is None:
            self._connection = self._server_connection()
            if self._connection is None:
                return self.async_abort(reason="server_unavailable")
            try:
                self._listing = await self._connection.send("list_models", timeout=20)
            except HomeAssistantError:
                return self.async_abort(reason="server_unavailable")
        current = self._listing.get("current", {})
        if user_input is not None:
            self._models = user_input
            return await self.async_step_voice()
        # Embeddings are read-only: the server agent cannot reindex into an isolated store (reindex_unsupported).
        return self.async_show_form(step_id="init",
                                    description_placeholders={"embeddings": current.get("embeddings", "?")},
                                    data_schema=vol.Schema({
            vol.Required("voice", default=current.get("voice")): _select(self._listing["voice"], current.get("voice")),
            vol.Required("brain", default=current.get("brain")): _select(self._listing["brain"], current.get("brain")),
            vol.Required("lucid", default=current.get("lucid")): _select(self._listing["lucid"], current.get("lucid")),
        }))

    async def async_step_voice(self, user_input: dict[str, Any] | None = None) -> Any:
        current = self._listing.get("current", {})
        if user_input is not None:
            self._voice = user_input
            return await self.async_step_budget()
        return self.async_show_form(step_id="voice", data_schema=vol.Schema({
            vol.Required("voice", default=current.get("voice_name", "Charon")): _select(
                self._listing.get("voices", []), current.get("voice_name")),
            vol.Required("speaking_rate", default=current.get("speaking_rate", "normal")): _select(
                SPEAKING_RATES, None, "speaking_rate"),
            vol.Required("brief_mode", default=bool(current.get("brief_mode"))): BooleanSelector(),
        }))

    async def async_step_budget(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is not None:
            return await self._apply(user_input["budget_brl"])
        default = (self._connection.state or {}).get("budget_brl") or DEFAULT_BUDGET_BRL
        return self.async_show_form(step_id="budget", data_schema=vol.Schema({
            vol.Required("budget_brl", default=default): NumberSelector(
                NumberSelectorConfig(min=1, max=100000, step=1, unit_of_measurement="BRL")),
        }))

    async def _apply(self, budget: float) -> Any:
        current = self._listing.get("current", {})
        changed = {role: self._models[role] for role in ("voice", "brain", "lucid")
                   if self._models.get(role) not in (None, current.get(role))}
        try:
            if changed:
                await self._connection.send("set_models", args=changed)
            await self._connection.send("set_voice_settings", args=dict(self._voice))
            await self._connection.send("set", key="budget_brl", value=budget)
        except HomeAssistantError as err:
            reason = str(err)
            return self.async_abort(reason=reason if reason == "embeddings_unavailable" else "apply_failed")
        return self.async_create_entry(data={})


class ServerSubentryFlow(ConfigSubentryFlow):
    """Add the F.R.I.D.A.Y. server as an agent."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title="Server",
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
                title=f"Satellite {user_input[CONF_HOST]}", data=data,
                unique_id=f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}",
            )
        return self.async_show_form(step_id="user", data_schema=self._schema({}))

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return self.async_show_menu(step_id="reconfigure", menu_options=["connection", "wifi", "room_sensors"])

    async def async_step_wifi(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change Wi-Fi: the credentials go straight to the agent and are never stored."""
        errors: dict[str, str] = {}
        if user_input is not None:
            connection = self._get_entry().runtime_data.get(self._get_reconfigure_subentry().subentry_id)
            try:
                if connection is None:
                    raise ServiceValidationError("not connected")
                await async_set_wifi(connection, user_input[CONF_SSID], user_input[CONF_WIFI_PASSWORD])
            except HomeAssistantError:
                errors["base"] = "wifi_failed"
            else:
                return self.async_abort(reason="wifi_changed")
        return self.async_show_form(
            step_id="wifi",
            errors=errors,
            data_schema=vol.Schema({
                vol.Required(CONF_SSID): str,
                vol.Required(CONF_WIFI_PASSWORD): _PASSWORD,
            }),
        )

    async def async_step_room_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose where the room temperature and humidity come from (each on its own)."""
        subentry = self._get_reconfigure_subentry()
        old = dict(subentry.data)
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {k: v for k, v in old.items()
                    if k not in {f(key) for key in room_climate.CONSUMED_KEYS
                                 for f in (room_climate.source_key, room_climate.entity_key)}}
            for key in room_climate.CONSUMED_KEYS:
                source = user_input.get(room_climate.source_key(key))
                entity = user_input.get(room_climate.entity_key(key))
                if source == SOURCE_HA_ENTITY and not entity:
                    errors[room_climate.entity_key(key)] = "entity_required"
                if source:
                    data[room_climate.source_key(key)] = source
                if source == SOURCE_HA_ENTITY and entity:
                    data[room_climate.entity_key(key)] = entity
            if not errors:
                return self.async_update_and_abort(self._get_entry(), subentry, data=data)
            old = {**old, **user_input}
        fields: dict = {}
        for quantity, (key, _name, _unit, device_class) in room_climate.QUANTITIES.items():
            # the satellite's own sensor is always offered: it stays unavailable until one is wired
            options = [SOURCE_SATELLITE, SOURCE_HA_ENTITY]
            src, ent = room_climate.source_key(key), room_climate.entity_key(key)
            fields[vol.Optional(src, description=(
                {"suggested_value": old[src]} if old.get(src) in options else {}))] = SelectSelector(
                    SelectSelectorConfig(options=options, translation_key="room_source"))
            fields[vol.Optional(ent, description=(
                {"suggested_value": old[ent]} if old.get(ent) else {}))] = EntitySelector(
                    EntitySelectorConfig(domain="sensor", device_class=device_class))
        return self.async_show_form(
            step_id="room_sensors", errors=errors, data_schema=vol.Schema(fields))

    async def async_step_connection(
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
            step_id="connection", data_schema=self._schema(old, token_optional=True)
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
