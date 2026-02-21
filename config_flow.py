"""Config flow for Solar Surplus EV Charging."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import (
    CONF_CHARGER_HOST,
    CONF_CHARGER_PORT,
    CONF_CHARGER_SLAVE_ID,
    CONF_HYSTERESIS_W,
    CONF_MAX_CURRENT,
    CONF_MIN_CURRENT,
    CONF_MIN_POWER_1PHASE,
    CONF_MIN_POWER_3PHASE,
    CONF_PHASE_1_VALUE,
    CONF_PHASE_3_VALUE,
    CONF_PHASE_SWITCH_PAUSE,
    CONF_PHASE_SWITCH_REGISTER,
    CONF_SOLAR_EXPORT_SENSOR,
    CONF_UPDATE_INTERVAL,
    CONF_VOLTAGE,
    DEFAULT_HYSTERESIS_W,
    DEFAULT_MAX_CURRENT,
    DEFAULT_MIN_CURRENT,
    DEFAULT_MIN_POWER_1PHASE,
    DEFAULT_MIN_POWER_3PHASE,
    DEFAULT_PHASE_1_VALUE,
    DEFAULT_PHASE_3_VALUE,
    DEFAULT_PHASE_SWITCH_PAUSE,
    DEFAULT_PHASE_SWITCH_REGISTER,
    DEFAULT_PORT,
    DEFAULT_SLAVE_ID,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_VOLTAGE,
    DOMAIN,
)

_STEP_1_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CHARGER_HOST): str,
        vol.Optional(CONF_CHARGER_PORT, default=DEFAULT_PORT): vol.All(int, vol.Range(min=1, max=65535)),
        vol.Optional(CONF_CHARGER_SLAVE_ID, default=DEFAULT_SLAVE_ID): vol.All(int, vol.Range(min=1, max=255)),
        vol.Required(CONF_SOLAR_EXPORT_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(device_class="power")
        ),
    }
)

_STEP_2_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_MIN_CURRENT, default=DEFAULT_MIN_CURRENT): vol.All(int, vol.Range(min=6, max=16)),
        vol.Optional(CONF_MAX_CURRENT, default=DEFAULT_MAX_CURRENT): vol.All(int, vol.Range(min=6, max=16)),
        vol.Optional(CONF_VOLTAGE, default=DEFAULT_VOLTAGE): vol.All(int, vol.Range(min=100, max=400)),
        vol.Optional(CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL): vol.All(int, vol.Range(min=10, max=300)),
        vol.Optional(CONF_MIN_POWER_1PHASE, default=DEFAULT_MIN_POWER_1PHASE): vol.All(int, vol.Range(min=500, max=5000)),
        vol.Optional(CONF_MIN_POWER_3PHASE, default=DEFAULT_MIN_POWER_3PHASE): vol.All(int, vol.Range(min=1000, max=15000)),
        vol.Optional(CONF_HYSTERESIS_W, default=DEFAULT_HYSTERESIS_W): vol.All(int, vol.Range(min=0, max=1000)),
        vol.Optional(CONF_PHASE_SWITCH_PAUSE, default=DEFAULT_PHASE_SWITCH_PAUSE): vol.All(int, vol.Range(min=30, max=600)),
        vol.Optional(CONF_PHASE_SWITCH_REGISTER, default=DEFAULT_PHASE_SWITCH_REGISTER): vol.All(int, vol.Range(min=0, max=65535)),
        vol.Optional(CONF_PHASE_1_VALUE, default=DEFAULT_PHASE_1_VALUE): vol.All(int, vol.Range(min=0, max=65535)),
        vol.Optional(CONF_PHASE_3_VALUE, default=DEFAULT_PHASE_3_VALUE): vol.All(int, vol.Range(min=0, max=65535)),
    }
)


class SolarChargerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Two-step config flow: connection settings, then charging parameters."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict = {}

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> config_entries.FlowResult:
        """Step 1: charger host and solar sensor."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_charging_params()

        return self.async_show_form(
            step_id="user",
            data_schema=_STEP_1_SCHEMA,
            errors=errors,
        )

    async def async_step_charging_params(
        self, user_input: dict | None = None
    ) -> config_entries.FlowResult:
        """Step 2: charging thresholds and phase switch parameters."""
        if user_input is not None:
            self._data.update(user_input)
            host = self._data[CONF_CHARGER_HOST]
            return self.async_create_entry(
                title=f"Solar Charger ({host})",
                data=self._data,
            )

        return self.async_show_form(
            step_id="charging_params",
            data_schema=_STEP_2_SCHEMA,
        )
