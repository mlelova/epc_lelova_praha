# "Albania",
# "Bosnia and Herzegovina",
# "Switzerland",
# "Serbia",
# "UK",
# "Montenegro",
# "North Macedonia",
# "Norway",
COUNTRIES = [
    "Albania",
    "Austria",
    "Bosnia and Herzegovina",
    "Belgium",
    "Bulgaria",
    "Switzerland",
    "Cyprus",
    "Czech Republic",
    "Germany",
    "Denmark",
    "Estonia",
    "Spain",
    "Finland",
    "France",
    "UK",
    "Greece",
    "Croatia",
    "Hungary",
    "Ireland",
    "Italy",
    "Lithuania",
    "Luxembourg",
    "Latvia",
    "Montenegro",
    "North Macedonia",
    "Malta",
    "Netherlands",
    "Norway",
    "Poland",
    "Portugal",
    "Romania",
    "Serbia",
    "Sweden",
    "Slovenia",
    "Slovakia",
]

COUNTRY_CODE_TO_NAME = {
    "AL": "Albania",
    "AT": "Austria",
    "BA": "Bosnia and Herzegovina",
    "BE": "Belgium",
    "BG": "Bulgaria",
    "CH": "Switzerland",
    "CY": "Cyprus",
    "CZ": "Czech Republic",
    "DE": "Germany",
    "DK": "Denmark",
    "EE": "Estonia",
    "ES": "Spain",
    "FI": "Finland",
    "FR": "France",
    "GB": "UK",
    "GR": "Greece",
    "HR": "Croatia",
    "HU": "Hungary",
    "IE": "Ireland",
    "IT": "Italy",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "LV": "Latvia",
    "ME": "Montenegro",
    "MK": "North Macedonia",
    "MT": "Malta",
    "NL": "Netherlands",
    "NO": "Norway",
    "PL": "Poland",
    "PT": "Portugal",
    "RO": "Romania",
    "RS": "Serbia",
    "SE": "Sweden",
    "SI": "Slovenia",
    "SK": "Slovakia",
}

CARRIERS = [
    "battery",
    "chp",
    "coal",
    "coal-ccs",
    "lignite",
    "lignite-ccs",
    "oil-heavy",
    "oil-light",
    "oil-shale",
    "gas",
    "gas-ccs",
    "h2-ccgt",
    "h2-fuel-cell",
    "hydro-phs",
    "hydro-phs-pure",
    "hydro-pondage",
    "hydro-reservoir",
    "hydro-ror",
    "offwind",
    "onwind",
    "solar-pv-rooftop",
    "solar-pv-utility",
    "solar-thermal",
    "solar-thermal-w-storage",
    "other-res",
    "uranium",
    "nuclear",
    "other-thermal",
    # "electrolyser"
]

##OPTIONS
# "Gas"
# "Hydro"
# "Nuclear"
# "Other RES"
# "Other thermal"
# "Solar"
# "Wind"
# "Battery Storage"
# "DSR Explicit"
# "Electric Vehicle"
# "Pump Storage - Closed Loop (turbine)"
# "Pump Storage - Open Loop (turbine)"

CARRIERS_TO_SIMPLE = {
    "battery": "Battery Storage",
    "chp": "Other thermal",
    "coal": "Other thermal",
    "coal-ccs": "Other thermal",
    "lignite": "Other thermal",
    "lignite-ccs": "Other thermal",
    "oil-heavy": "Other thermal",
    "oil-light": "Other thermal",
    "oil-shale": "Other thermal",
    "gas": "Gas",
    "gas-ccs": "Gas",
    # "electrolyser": "Other thermal",   # conversion tech, not generation
    "other-thermal": "Other thermal",
    "h2-ccgt": "Gas",
    "h2-fuel-cell": "Gas",
    "hydro-phs": "Pump Storage - Open Loop (turbine)",
    "hydro-phs-pure": "Pump Storage - Closed Loop (turbine)",
    "hydro-pondage": "Hydro",
    "hydro-reservoir": "Hydro",
    "hydro-ror": "Hydro",
    "offwind": "Wind",
    "onwind": "Wind",
    "solar-pv-rooftop": "Solar",
    "solar-pv-utility": "Solar",
    "solar-thermal": "Solar",
    "solar-thermal-w-storage": "Solar",
    "other-res": "Other RES",
    "nuclear": "Nuclear",
}


##OPTIONS
# Hydrogen CCGT
# Hydrogen FC
# Light oil
# Lignite
# Lignite biofuel
# Nuclear
# Oil shale
# Oil shale biofuel
# Other RES
# Others non-RES
# Pondage
# Reservoir
# Run-of-River
# Solar PV
# Solar PV Rooftop
# Solar PV Utility
# Solar Thermal
# Wind Offshore
# Wind Onshore
# DSR
# Demand Side Response Explicit
# EV
# Large scale batteries
# PS Closed
# PS Open
# Prosumer
# Pump Storage - Closed Loop (turbine)
# Pump Storage - Open Loop (turbine)
# eMarket
CARRIERS_TO_DETAILED = {
    "battery": "Battery Storage",
    "chp": "Other thermal",
    "coal": "Other thermal",
    "lignite": "Other thermal",
    "oil-heavy": "Other thermal",
    "oil-light": "Other thermal",
    "oil-shale": "Other thermal",
    "gas": "Gas",
    # "electrolyser": "Other thermal",   # conversion tech, not generation
    "h2-ccgt": "Gas",
    "h2-fuel-cell": "Gas",
    "hydro-phs": "Pump Storage - Open Loop (turbine)",
    "hydro-phs-pure": "Pump Storage - Closed Loop (turbine)",
    "hydro-pondage": "Hydro",
    "hydro-reservoir": "Hydro",
    "hydro-ror": "Hydro",
    "offwind": "Wind",
    "onwind": "Wind",
    "solar-pv-rooftop": "Solar",
    "solar-pv-utility": "Solar",
    "solar-thermal": "Solar",
    "solar-thermal-w-storage": "Solar",
    "other-res": "Other RES",
    "uranium": "Nuclear",
}

# PyPSA Carrier Type Mapping
# Categorizes carriers into generators vs storage for network building
GENERATOR_CARRIERS = [
    "coal",
    "coal-ccs",
    "gas",
    "gas-ccs",
    "lignite",
    "lignite-ccs",
    "nuclear",
    "oil-heavy",
    "oil-light",
    "oil-shale",
    "h2",
    "onwind",
    "offwind",
    "solar-pv-utility",
    "solar-pv-rooftop",
    "solar-thermal",
    "solar-thermal-w-storage",
    "hydro-ror",  # Run-of-river (no storage, flow-through generation)
    "other-res",
    "electrolyser",  # Consumes electricity to produce H2
    "other-thermal",
]

STORAGE_CARRIERS = [
    "battery",
    "hydro-phs",  # Pumped hydro storage (open loop) - with natural inflows
    "hydro-phs-pure",  # Pumped hydro storage (closed loop) - no natural inflows
    "hydro-pondage",  # Small hydro reservoir (hours to days) - with natural inflows
    "hydro-reservoir",  # Large hydro reservoir (seasonal) - with natural inflows
]

ALL_CARRIERS = GENERATOR_CARRIERS + STORAGE_CARRIERS

# ============================================================================
# Carrier Groupings for Time Series Application
# ============================================================================

# Variable Renewable Energy (VRE) - Weather-dependent, non-dispatchable
WIND_CARRIERS = [
    "onwind",
    "offwind",
]

SOLAR_CARRIERS = [
    "solar-pv-utility",
    "solar-pv-rooftop",
    "solar-thermal",
    "solar-thermal-w-storage",
]

# Hydro generators (flow-based, non-dispatchable)
HYDRO_ROR_CARRIERS = [
    "hydro-ror",  # Run-of-river: non-dispatchable, follows natural flow (no storage)
]

# Storage technologies (Store+Link architecture in PyPSA)
HYDRO_STORAGE_CARRIERS = [
    "hydro-phs",  # Pumped hydro storage (open loop) - with natural inflows
    "hydro-phs-pure",  # Pumped hydro storage (closed loop) - no natural inflows
    "hydro-pondage",  # Small hydro reservoir (hours to days storage)
    "hydro-reservoir",  # Large hydro reservoir (seasonal storage)
]

BATTERY_CARRIERS = [
    "battery",
]

# Conventional/Thermal generators (dispatchable, fuel-based)
CONVENTIONAL_CARRIERS = [
    "coal",
    "coal-ccs",
    "gas",
    "gas-ccs",
    "h2-ccgt",
    "lignite",
    "lignite-ccs",
    "nuclear",
    "oil-heavy",
    "oil-light",
    "oil-shale",
    "other-thermal",
    "h2",
    "electrolyser",
    "other-res",  ##not sure about this one
]

# Other renewables
OTHER_RES_CARRIERS = [
    "other-res",
]

# ============================================================================
# Aggregate Groupings (for convenience)
# ============================================================================

# All Variable Renewable Energy sources (non-dispatchable, weather-dependent)
VRE_CARRIERS = WIND_CARRIERS + SOLAR_CARRIERS + HYDRO_ROR_CARRIERS + OTHER_RES_CARRIERS

# All Hydro (both generators and storage)
HYDRO_CARRIERS = HYDRO_ROR_CARRIERS + HYDRO_STORAGE_CARRIERS

# All Storage
STORAGE_UNIT_CARRIERS = HYDRO_STORAGE_CARRIERS + BATTERY_CARRIERS

# All Renewable Energy Sources (including all hydro types)
RES_CARRIERS = VRE_CARRIERS + HYDRO_STORAGE_CARRIERS


# ['hydro-phs-reservoir' 'hydro-phs-turbine' 'hydro-phs-pump'
#  'hydro-phs-pure-reservoir' 'hydro-phs-pure-turbine' 'hydro-phs-pure-pump'
#  'battery-charge' 'battery-discharge' 'battery-store']


# "hydro-ror-turbine",

# "hydro-pondage-reservoir",
# "hydro-pondage-turbine",

# "hydro-reservoir-reservoir",
# "hydro-reservoir-turbine",

# "hydro-phs-reservoir",
# "hydro-phs-turbine",
# "hydro-phs-pump",

# "hydro-phs-pure-reservoir",
# "hydro-phs-pure-turbine",
# "hydro-phs-pure-pump",

# "battery-charge",
# "battery-discharge",
# "battery-store"

# battery
# hydro-phs
# hydro-phs-pure
