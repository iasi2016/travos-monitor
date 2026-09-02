# Configurare Travos Monitor

# Data centrala (format ZZ/LL/AAAA)
BASE_DATE = "17/09/2026"

# Interval fata de data centrala
DAYS_BEFORE = 7
DAYS_AFTER = 7

# Cautarea
SEARCH_PARAMS = {
    "country": "125",
    "region": "193",
    "days": "7",
    "dest_country": "125",
    "dep_country": "113",
    "dep_city": "193",      # Iasi
    "dest_region": "124",   # Antalya
    "a_1": "2",             # 2 adulti
    "mmax": "1400",
    "meal[5]": "5",         # All Inclusive
    "meal[6]": "6",         # Ultra All Inclusive
}

MAX_PAGES = 8
REQUEST_DELAY_SECONDS = 2

DATABASE_FILE = "prices.db"
EXCLUDED_HOTELS_FILE = "excluded_hotels.txt"

# Momentan False. Vom activa dupa ce testam ca Travos este extras corect.
USE_PLAYWRIGHT = False
