import pytest
import sqlite3
import tempfile
import os
import sys
from pathlib import Path
import datetime

# Add parent directory to path so we can import main
sys.path.insert(0, str(Path(__file__).parent.parent))

from main import init_db, previous_price, save_and_report


@pytest.fixture
def temp_db():
    """Creeaza o database temporara pentru teste"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    init_db(conn)
    yield conn
    conn.close()
    os.unlink(path)


def test_init_db(temp_db):
    """Verifica ca tabelele sunt create corect"""
    cursor = temp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='prices'"
    )
    assert cursor.fetchone() is not None


def test_previous_price_not_found(temp_db):
    """Verifica ca previous_price returneaza None daca nu exista"""
    result = previous_price(temp_db, "Hotel Test", "2026-09-17")
    assert result is None


def test_save_and_report_new_prices(temp_db):
    """Verifica salvarea preturilor noi"""
    departure_date = datetime.date(2026, 9, 17)
    hotels = [
        {
            "hotel": "Hotel ABC",
            "price_eur": 500.0,
            "meal": "All Inclusive",
            "rating": "4.5",
            "url": "http://example.com/hotel1"
        }
    ]
    
    changes = save_and_report(temp_db, departure_date, hotels)
    assert changes == []
    
    cursor = temp_db.execute(
        "SELECT hotel, price_eur FROM prices WHERE hotel = ?",
        ("Hotel ABC",)
    )
    row = cursor.fetchone()
    assert row is not None
    assert row[0] == "Hotel ABC"
    assert row[1] == 500.0


def test_save_and_report_price_change(temp_db):
    """Verifica detectarea schimbarii de pret"""
    departure_date = datetime.date(2026, 9, 17)
    
    hotels_1 = [
        {
            "hotel": "Hotel XYZ",
            "price_eur": 600.0,
            "meal": "All Inclusive",
            "rating": "4.0",
            "url": "http://example.com/hotel2"
        }
    ]
    save_and_report(temp_db, departure_date, hotels_1)
    
    hotels_2 = [
        {
            "hotel": "Hotel XYZ",
            "price_eur": 550.0,
            "meal": "All Inclusive",
            "rating": "4.0",
            "url": "http://example.com/hotel2"
        }
    ]
    changes = save_and_report(temp_db, departure_date, hotels_2)
    
    assert len(changes) == 1
    assert changes[0][0] == "Hotel XYZ"
    assert changes[0][1] == 600.0
    assert changes[0][2] == 550.0
    assert changes[0][3] == -50.0
