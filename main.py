import datetime
import sqlite3
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from config import (
    BASE_DATE, DAYS_BEFORE, DAYS_AFTER, SEARCH_PARAMS,
    MAX_PAGES, REQUEST_DELAY_SECONDS, DATABASE_FILE,
    EXCLUDED_HOTELS_FILE,
)

BASE_URL = "https://www.travos.ro/search.php"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux aarch64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.8",
}

PRICE_RE = re.compile(r"(\d[\d.,]*)\s*€")
RATING_RE = re.compile(r"Nota\s*([\d.,]+)", re.I)
MEALS = ["Ultra All Inclusive", "Ultra All inclusive", "All Inclusive", "All inclusive"]


def normalize_name(name):
    """Normalize hotel name to lowercase with single spaces"""
    return " ".join(name.lower().strip().split())


def load_excluded():
    """Load excluded hotels from file"""
    excluded = set()
    try:
        with open(EXCLUDED_HOTELS_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    excluded.add(normalize_name(line))
    except FileNotFoundError:
        pass
    return excluded


def date_range():
    """Generate date range around BASE_DATE"""
    center = datetime.datetime.strptime(BASE_DATE, "%d/%m/%Y").date()
    for offset in range(-DAYS_BEFORE, DAYS_AFTER + 1):
        yield center + datetime.timedelta(days=offset)


def init_db(conn):
    """Initialize database with required tables"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            checked_at TEXT NOT NULL,
            departure_date TEXT NOT NULL,
            hotel TEXT NOT NULL,
            price_eur REAL,
            meal TEXT,
            rating TEXT,
            url TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_prices_hotel_date
        ON prices(hotel, departure_date, checked_at)
    """)
    conn.commit()


def fetch_page(session, departure_date, page):
    """Fetch a search results page"""
    params = dict(SEARCH_PARAMS)
    params["from"] = departure_date.strftime("%d/%m/%Y")
    params["page"] = page

    response = session.get(
        BASE_URL,
        params=params,
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    return response.text, response.url


def extract_price(text):
    """
    Extract price from text with proper handling of European and US number formats.
    Supports: 123€, 1.234,50€, 1,234.50€, 1234,50€, etc.
    """
    matches = PRICE_RE.findall(text)
    if not matches:
        return None
    
    raw = matches[-1]
    
    # Count separators
    dots = raw.count(".")
    commas = raw.count(",")
    
    if dots > 1:
        # Multiple dots: European format with thousand separators
        # e.g., 1.234.567,89 -> remove dots, replace comma with dot
        if commas > 0:
            raw = raw.replace(".", "").replace(",", ".")
        else:
            # e.g., 1.234.567 -> remove dots (no decimals)
            raw = raw.replace(".", "")
    elif dots == 1 and commas == 0:
        # Single dot, no comma: could be 999.99 (US) or 1.234 (EU thousands)
        # If 3+ digits after dot, it's thousands separator (EU)
        after_dot = raw.split(".")[1]
        if len(after_dot) > 2:
            raw = raw.replace(".", "")
        # Else keep dot as decimal separator
    elif commas == 1 and dots == 0:
        # Single comma, no dot: European decimal separator
        # e.g., 1.234,56 or 999,99
        raw = raw.replace(",", ".")
    elif dots == 1 and commas == 1:
        # Both dot and comma: determine which is decimal by position
        if raw.rindex(".") > raw.rindex(","):
            # Dot is last: US format 1,234.56
            raw = raw.replace(",", "")
        else:
            # Comma is last: EU format 1.234,56
            raw = raw.replace(".", "").replace(",", ".")
    
    try:
        return float(raw)
    except ValueError:
        return None


def parse_hotels(html, page_url):
    """Parse hotels from HTML"""
    soup = BeautifulSoup(html, "html.parser")
    results = {}
    excluded = load_excluded()

    for a in soup.find_all("a", href=True):
        name = " ".join(a.get_text(" ", strip=True).split())
        href = urljoin(page_url, a["href"])

        # Ignoram linkurile care clar nu par hoteluri.
        if not name or len(name) < 3:
            continue

        container = a
        best_text = ""
        for _ in range(8):
            if container.parent is None:
                break
            container = container.parent
            text = container.get_text(" ", strip=True)
            if "€" in text:
                best_text = text
                if any(m.lower() in text.lower() for m in MEALS):
                    break

        if "€" not in best_text:
            continue

        # Incercam sa evitam linkurile din meniuri / alte sectiuni.
        if normalize_name(name) in excluded:
            continue

        price = extract_price(best_text)
        if price is None:
            continue

        meal = ""
        for m in MEALS:
            if m.lower() in best_text.lower():
                meal = m
                break

        rating_match = RATING_RE.search(best_text)
        rating = rating_match.group(1) if rating_match else ""

        key = (normalize_name(name), href)
        results[key] = {
            "hotel": name,
            "price_eur": price,
            "meal": meal,
            "rating": rating,
            "url": href,
        }

    return list(results.values())


def scrape_date(session, departure_date):
    """Scrape all hotels for a given departure date"""
    all_hotels = {}

    for page in range(1, MAX_PAGES + 1):
        html, page_url = fetch_page(session, departure_date, page)
        hotels = parse_hotels(html, page_url)

        if not hotels:
            break

        for hotel in hotels:
            key = normalize_name(hotel["hotel"])
            all_hotels[key] = hotel

        time.sleep(REQUEST_DELAY_SECONDS)

    return list(all_hotels.values())


def previous_price(conn, hotel, departure_date):
    """Get the previous price for a hotel on a given departure date"""
    row = conn.execute("""
        SELECT price_eur
        FROM prices
        WHERE hotel = ? AND departure_date = ?
        ORDER BY checked_at DESC, id DESC
        LIMIT 1
    """, (hotel, departure_date)).fetchone()
    return row[0] if row else None


def save_and_report(conn, departure_date, hotels):
    """Save hotel prices and report changes"""
    checked_at = datetime.datetime.now().isoformat(timespec="seconds")
    changes = []

    for h in hotels:
        old_price = previous_price(conn, h["hotel"], departure_date.isoformat())

        conn.execute("""
            INSERT INTO prices
            (checked_at, departure_date, hotel, price_eur, meal, rating, url)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            checked_at,
            departure_date.isoformat(),
            h["hotel"],
            h["price_eur"],
            h["meal"],
            h["rating"],
            h["url"],
        ))

        if old_price is not None:
            diff = h["price_eur"] - old_price
            if abs(diff) > 0.001:
                changes.append((h["hotel"], old_price, h["price_eur"], diff))

    conn.commit()
    return changes


def main():
    """Main entry point"""
    print("=" * 70)
    print("TRAVOS HOTEL MONITOR")
    print(f"Data centrala: {BASE_DATE}")
    print(f"Interval: -{DAYS_BEFORE} / +{DAYS_AFTER} zile")
    print("=" * 70)

    conn = sqlite3.connect(DATABASE_FILE)
    init_db(conn)

    session = requests.Session()
    total_hotels = 0
    all_changes = []

    for departure_date in date_range():
        print(f"\nVerific: {departure_date.strftime('%d/%m/%Y')}")

        try:
            hotels = scrape_date(session, departure_date)
        except requests.RequestException as e:
            print(f"EROARE la cautare: {e}")
            continue

        print(f"  Hoteluri extrase: {len(hotels)}")
        total_hotels += len(hotels)

        changes = save_and_report(conn, departure_date, hotels)
        for hotel, old, new, diff in changes:
            sign = "+" if diff > 0 else ""
            print(f"  {'📈' if diff > 0 else '📉'} {hotel}: "
                  f"{old:.0f}€ -> {new:.0f}€ ({sign}{diff:.0f}€)")
            all_changes.append((departure_date, hotel, old, new, diff))

    conn.close()

    print("\n" + "=" * 70)
    print(f"TOTAL HOTELURI EXTRASE: {total_hotels}")
    print(f"MODIFICARI DETECTATE: {len(all_changes)}")
    print("=" * 70)

    if not all_changes:
        print("Prima rulare sau nu au fost detectate modificari.")


if __name__ == "__main__":
    main()
