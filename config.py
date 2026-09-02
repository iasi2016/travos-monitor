import os

# ==============================================================================
# CONFIGURARE TRAVOS MONITOR
# ==============================================================================

# Data centrala de plecare (format ZZ/LL/AAAA)
# Poate fi modificata direct aici sau prin variabila de mediu BASE_DATE
BASE_DATE = os.getenv("BASE_DATE", "20/09/2026")

# Interval fata de data centrala (numar de zile inainte / dupa)
DAYS_BEFORE = int(os.getenv("DAYS_BEFORE", "2"))
DAYS_AFTER = int(os.getenv("DAYS_AFTER", "2"))

# Pret maxim (in EUR). Daca este None sau 0, nu filtreaza dupa pret maxim.
MAX_PRICE = float(os.getenv("MAX_PRICE", "0")) if os.getenv("MAX_PRICE") else None

# Parametri de cautare
SEARCH_PARAMS = {
    "country": "125",
    "region": "193",
    "days": "7",            # Durata sejur (7 nopti)
    "dest_country": "125",  # Turcia
    "dep_country": "113",   # Romania
    "dep_city": "193",      # Iasi (sau 206 pentru Bucuresti)
    "dest_region": "124",   # Antalya
    "a_1": "2",             # 2 adulti
}

MAX_PAGES = 8
REQUEST_DELAY_SECONDS = 0.5

DATABASE_FILE = "prices.db"
EXCLUDED_HOTELS_FILE = "excluded_hotels.txt"

USE_PLAYWRIGHT = False
