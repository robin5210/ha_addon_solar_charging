"""Constants for the Solar Surplus EV Charging integration."""

DOMAIN = "solar_charger"

# Daheim Lader Modbus holding register addresses (0-based PDU)
REG_STATUS = 0
REG_CURRENT_L1 = 6
REG_CURRENT_L2 = 8
REG_CURRENT_L3 = 10
REG_CURRENT_LIMIT = 91   # write: amps × 10  (60 = 6 A, 160 = 16 A)
REG_CHARGING_MODE = 93   # write: 1 = RFID/Modbus control mode
REG_CHARGE_CMD = 95      # write: 1 = Start, 2 = Stop
REG_PHASE_SWITCH = 186   # write: 1 = 1-phase, 3 = 3-phase

CURRENT_LIMIT_MIN = 60   # 6 A × 10
CURRENT_LIMIT_MAX = 160  # 16 A × 10

# Config / options keys
CONF_CHARGER_HOST = "charger_host"
CONF_CHARGER_PORT = "charger_port"
CONF_CHARGER_SLAVE_ID = "charger_slave_id"
CONF_SOLAR_EXPORT_SENSOR = "solar_export_sensor"
CONF_MIN_CURRENT = "min_current"
CONF_MAX_CURRENT = "max_current"
CONF_VOLTAGE = "voltage"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_PHASE_SWITCH_REGISTER = "phase_switch_register"
CONF_PHASE_1_VALUE = "phase_1_value"
CONF_PHASE_3_VALUE = "phase_3_value"
CONF_PHASE_SWITCH_PAUSE = "phase_switch_pause"
CONF_MIN_POWER_1PHASE = "min_power_1phase"
CONF_MIN_POWER_3PHASE = "min_power_3phase"
CONF_HYSTERESIS_W = "hysteresis_w"
CONF_MAX_GRID_POWER_W = "max_grid_power_w"
CONF_CHARGE_NOW_POWER_W = "charge_now_power_w"
CONF_CAR_SOC_SENSOR = "car_soc_sensor"
CONF_PRICE_SENSOR = "price_sensor"
CONF_GRID_IMPORT_SENSOR = "grid_import_sensor"
CONF_MONTHLY_PEAK_SENSOR = "monthly_peak_sensor"
CONF_CAR_BATTERY_KWH = "car_battery_kwh"

# Charging modes (stored in entry.options["charging_mode"])
CHARGING_MODE_SOLAR_ONLY = "solar_only"
CHARGING_MODE_SOLAR_ASSISTED = "solar_assisted"
CHARGING_MODE_CHARGE_NOW = "charge_now"
CHARGING_MODE_CHEAP_GRID = "cheap_grid"

# Defaults
DEFAULT_PORT = 502
DEFAULT_SLAVE_ID = 255
DEFAULT_MIN_CURRENT = 6
DEFAULT_MAX_CURRENT = 16
DEFAULT_VOLTAGE = 230
DEFAULT_UPDATE_INTERVAL = 30
DEFAULT_PHASE_SWITCH_REGISTER = REG_PHASE_SWITCH
DEFAULT_PHASE_1_VALUE = 1
DEFAULT_PHASE_3_VALUE = 3
DEFAULT_PHASE_SWITCH_PAUSE = 120
DEFAULT_MIN_POWER_1PHASE = 1400   # 6 A × 230 V
DEFAULT_MIN_POWER_3PHASE = 4500   # ~6.5 A × 3 × 230 V
DEFAULT_HYSTERESIS_W = 200
DEFAULT_MAX_GRID_POWER_W = 1400   # 6 A × 230 V — grid baseline for solar-assisted mode
DEFAULT_CHARGE_NOW_POWER_W = 3680  # 16 A × 1-phase × 230 V
DEFAULT_TARGET_SOC_PCT = 80        # stop cheap-grid charging at 80 % SoC
DEFAULT_MAX_GRID_PRICE = 0.10      # safety cap: don't charge above this price even in scheduled hours
DEFAULT_CAR_BATTERY_KWH = 60       # used to compute charge hours from remaining SoC
DEFAULT_MIN_CHARGE_HOURS = 4       # fallback hours per day when SoC / battery data unavailable
DEFAULT_PEAK_POWER_LIMIT_W = 0     # 0 = disabled; set to your capacity tariff limit in W
