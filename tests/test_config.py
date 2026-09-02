import pytest
from config import (
    BASE_DATE, DAYS_BEFORE, DAYS_AFTER, SEARCH_PARAMS,
    MAX_PAGES, REQUEST_DELAY_SECONDS, DATABASE_FILE,
    EXCLUDED_HOTELS_FILE,
)


def test_base_date_format():
    """Verifica ca BASE_DATE are format DD/MM/YYYY"""
    import datetime
    try:
        datetime.datetime.strptime(BASE_DATE, "%d/%m/%Y")
    except ValueError:
        pytest.fail(f"BASE_DATE '{BASE_DATE}' nu are format DD/MM/YYYY")


def test_interval_positive():
    """Verifica ca intervalele sunt pozitive"""
    assert DAYS_BEFORE >= 0, "DAYS_BEFORE trebuie sa fie >= 0"
    assert DAYS_AFTER >= 0, "DAYS_AFTER trebuie sa fie >= 0"


def test_search_params_required_keys():
    """Verifica ca SEARCH_PARAMS are cheile necesare"""
    required = {"country", "region", "days", "dest_country", "dep_country"}
    assert required.issubset(SEARCH_PARAMS.keys()), f"Lipsesc chei: {required - set(SEARCH_PARAMS.keys())}"


def test_max_pages_positive():
    """Verifica ca MAX_PAGES este pozitiv"""
    assert MAX_PAGES > 0, "MAX_PAGES trebuie sa fie > 0"


def test_request_delay_positive():
    """Verifica ca REQUEST_DELAY_SECONDS este pozitiv"""
    assert REQUEST_DELAY_SECONDS >= 0, "REQUEST_DELAY_SECONDS trebuie sa fie >= 0"


def test_database_file_extension():
    """Verifica ca DATABASE_FILE are extensia .db"""
    assert DATABASE_FILE.endswith(".db"), f"DATABASE_FILE '{DATABASE_FILE}' trebuie sa aiba extensia .db"
