"""Config flow for Solar Surplus EV Charging."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_CHARGE_NOW_POWER_W,
    CONF_CHARGER_HOST,
    CONF_CHARGER_PORT,
    CONF_CHARGER_SLAVE_ID,
    CONF_HYSTERESIS_W,
    CONF_MAX_CURRENT,
    CONF_MAX_GRID_POWER_W,
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
    DEFAULT_CHARGE_NOW_POWER_W,
    DEFAULT_HYSTERESIS_W,
    DEFAULT_MAX_CURRENT,
    DEFAULT_MAX_GRID_POWER_W,
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

def _int_box(min_val: int, max_val: int) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=min_val,
            max=max_val,
            step=1,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


_STEP_1_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CHARGER_HOST): str,
        vol.Optional(CONF_CHARGER_PORT, default=DEFAULT_PORT): _int_box(1, 65535),
        vol.Optional(CONF_CHARGER_SLAVE_ID, default=DEFAULT_SLAVE_ID): _int_box(1, 255),
        vol.Required(CONF_SOLAR_EXPORT_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(device_class="power")
        ),
    }
)

_STEP_2_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_MIN_CURRENT, default=DEFAULT_MIN_CURRENT): _int_box(6, 16),
        vol.Optional(CONF_MAX_CURRENT, default=DEFAULT_MAX_CURRENT): _int_box(6, 16),
        vol.Optional(CONF_VOLTAGE, default=DEFAULT_VOLTAGE): _int_box(100, 400),
        vol.Optional(CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL): _int_box(10, 300),
        vol.Optional(CONF_MIN_POWER_1PHASE, default=DEFAULT_MIN_POWER_1PHASE): _int_box(500, 5000),
        vol.Optional(CONF_MIN_POWER_3PHASE, default=DEFAULT_MIN_POWER_3PHASE): _int_box(1000, 15000),
        vol.Optional(CONF_HYSTERESIS_W, default=DEFAULT_HYSTERESIS_W): _int_box(0, 1000),
        vol.Optional(CONF_PHASE_SWITCH_PAUSE, default=DEFAULT_PHASE_SWITCH_PAUSE): _int_box(30, 600),
        vol.Optional(CONF_PHASE_SWITCH_REGISTER, default=DEFAULT_PHASE_SWITCH_REGISTER): _int_box(0, 65535),
        vol.Optional(CONF_PHASE_1_VALUE, default=DEFAULT_PHASE_1_VALUE): _int_box(0, 65535),
        vol.Optional(CONF_PHASE_3_VALUE, default=DEFAULT_PHASE_3_VALUE): _int_box(0, 65535),
        vol.Optional(CONF_MAX_GRID_POWER_W, default=DEFAULT_MAX_GRID_POWER_W): _int_box(100, 11000),
        vol.Optional(CONF_CHARGE_NOW_POWER_W, default=DEFAULT_CHARGE_NOW_POWER_W): _int_box(100, 11000),
    }
)


class SolarChargerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Two-step config flow: connection settings, then charging parameters."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict = {}

    @staticmethod
    @callback
    def async_get_options_flow(_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return SolarChargerOptionsFlow()

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


class SolarChargerOptionsFlow(config_entries.OptionsFlow):
    """Allow reconfiguring all parameters after initial setup."""

    def __init__(self) -> None:
        self._data: dict = {}

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> config_entries.FlowResult:
        """Step 1: connection settings, pre-populated with current values."""
        effective = {**self.config_entry.data, **self.config_entry.options}

        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_charging_params()

        schema = vol.Schema(
            {
                vol.Required(CONF_CHARGER_HOST, default=effective.get(CONF_CHARGER_HOST, "")): str,
                vol.Optional(CONF_CHARGER_PORT, default=int(effective.get(CONF_CHARGER_PORT, DEFAULT_PORT))): _int_box(1, 65535),
                vol.Optional(CONF_CHARGER_SLAVE_ID, default=int(effective.get(CONF_CHARGER_SLAVE_ID, DEFAULT_SLAVE_ID))): _int_box(1, 255),
                vol.Required(
                    CONF_SOLAR_EXPORT_SENSOR,
                    default=effective.get(CONF_SOLAR_EXPORT_SENSOR, ""),
                ): selector.EntitySelector(selector.EntitySelectorConfig(device_class="power")),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_charging_params(
        self, user_input: dict | None = None
    ) -> config_entries.FlowResult:
        """Step 2: charging thresholds, pre-populated with current values."""
        effective = {**self.config_entry.data, **self.config_entry.options}

        if user_input is not None:
            self._data.update(user_input)
            # Preserve enabled flag managed by the switch entity
            return self.async_create_entry(
                title="",
                data={
                    "enabled": self.config_entry.options.get("enabled", True),
                    **self._data,
                },
            )

        schema = vol.Schema(
            {
                vol.Optional(CONF_MIN_CURRENT, default=int(effective.get(CONF_MIN_CURRENT, DEFAULT_MIN_CURRENT))): _int_box(6, 16),
                vol.Optional(CONF_MAX_CURRENT, default=int(effective.get(CONF_MAX_CURRENT, DEFAULT_MAX_CURRENT))): _int_box(6, 16),
                vol.Optional(CONF_VOLTAGE, default=int(effective.get(CONF_VOLTAGE, DEFAULT_VOLTAGE))): _int_box(100, 400),
                vol.Optional(CONF_UPDATE_INTERVAL, default=int(effective.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL))): _int_box(10, 300),
                vol.Optional(CONF_MIN_POWER_1PHASE, default=int(effective.get(CONF_MIN_POWER_1PHASE, DEFAULT_MIN_POWER_1PHASE))): _int_box(500, 5000),
                vol.Optional(CONF_MIN_POWER_3PHASE, default=int(effective.get(CONF_MIN_POWER_3PHASE, DEFAULT_MIN_POWER_3PHASE))): _int_box(1000, 15000),
                vol.Optional(CONF_HYSTERESIS_W, default=int(effective.get(CONF_HYSTERESIS_W, DEFAULT_HYSTERESIS_W))): _int_box(0, 1000),
                vol.Optional(CONF_PHASE_SWITCH_PAUSE, default=int(effective.get(CONF_PHASE_SWITCH_PAUSE, DEFAULT_PHASE_SWITCH_PAUSE))): _int_box(30, 600),
                vol.Optional(CONF_PHASE_SWITCH_REGISTER, default=int(effective.get(CONF_PHASE_SWITCH_REGISTER, DEFAULT_PHASE_SWITCH_REGISTER))): _int_box(0, 65535),
                vol.Optional(CONF_PHASE_1_VALUE, default=int(effective.get(CONF_PHASE_1_VALUE, DEFAULT_PHASE_1_VALUE))): _int_box(0, 65535),
                vol.Optional(CONF_PHASE_3_VALUE, default=int(effective.get(CONF_PHASE_3_VALUE, DEFAULT_PHASE_3_VALUE))): _int_box(0, 65535),
                vol.Optional(CONF_MAX_GRID_POWER_W, default=int(effective.get(CONF_MAX_GRID_POWER_W, DEFAULT_MAX_GRID_POWER_W))): _int_box(100, 11000),
                vol.Optional(CONF_CHARGE_NOW_POWER_W, default=int(effective.get(CONF_CHARGE_NOW_POWER_W, DEFAULT_CHARGE_NOW_POWER_W))): _int_box(100, 11000),
            }
        )
        return self.async_show_form(step_id="charging_params", data_schema=schema)
