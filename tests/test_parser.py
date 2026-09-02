import pytest
import sys
from pathlib import Path

# Add parent directory to path so we can import main
sys.path.insert(0, str(Path(__file__).parent.parent))

from main import (
    normalize_name, extract_price, parse_hotels, load_excluded
)


def test_normalize_name():
    """Verifica normalizarea numelor de hoteluri"""
    assert normalize_name("Hotel  ABC") == "hotel abc"
    assert normalize_name("  PALACE RESORT  ") == "palace resort"
    assert normalize_name("\n\tLuxury Inn\n") == "luxury inn"


def test_extract_price_valid_eu_format():
    """Verifica extractia preturilor valide in format European"""
    assert extract_price("123€") == 123.0
    assert extract_price("1.234,50€") == 1234.5
    assert extract_price("Price: 999,99€ included") == 999.99


def test_extract_price_us_format():
    """Verifica format US (1,234.56)"""
    assert extract_price("1,234.56€") == 1234.56
    assert extract_price("Price: 999.99€ included") == 999.99


def test_extract_price_invalid():
    """Verifica ca preturile invalide returneaza None"""
    assert extract_price("no price here") is None
    assert extract_price("") is None
    assert extract_price("abc€") is None


def test_extract_price_multiple_matches():
    """Verifica ca se extrage ultimul pret (cel mai relevant)"""
    text = "Old: 100€ New: 200€"
    assert extract_price(text) == 200.0


def test_parse_hotels_empty_html():
    """Verifica parsarea HTML-ului gol"""
    html = "<html><body></body></html>"
    result = parse_hotels(html, "http://example.com")
    assert result == []


def test_parse_hotels_no_prices():
    """Verifica parsarea cand nu sunt preturi"""
    html = '''
    <html>
        <body>
            <a href="/hotel/1">Hotel ABC</a>
        </body>
    </html>
    '''
    result = parse_hotels(html, "http://example.com")
    assert result == []


def test_load_excluded_file_not_found():
    """Verifica ca functia nu crapa daca fisierul nu exista"""
    excluded = load_excluded()
    assert isinstance(excluded, set)
