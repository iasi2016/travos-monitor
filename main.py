import argparse
import base64
import datetime
import os
import re
import sqlite3
import sys
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from config import (
    BASE_DATE, DAYS_BEFORE, DAYS_AFTER,
    REQUEST_DELAY_SECONDS, DATABASE_FILE,
    EXCLUDED_HOTELS_FILE, SEARCH_PARAMS,
    MAX_PRICE
)

BASE_URL = "https://www.travos.ro/search.php"
AJAX_SEARCH_URL = "https://www.travos.ro/ajax/search.php"
AUTOCOMPLETE_URL = "https://www.travos.ro/ajax/input_index_charter.php"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.8",
    "X-Requested-With": "XMLHttpRequest",
}

PRICE_RE = re.compile(r"(\d[\d.,]*)\s*€")
RATING_RE = re.compile(r"(\d+)\s*stele|Nota\s*([\d.,]+)", re.I)
MEALS = [
    "Ultra All Inclusive",
    "Ultra all inclusive",
    "All Inclusive",
    "All inclusive",
    "Demipensiune",
    "Pensiune Completa",
    "Mic Dejun"
]


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


def extract_price(text):
    """
    Extract price from text with proper handling of European and US number formats.
    Supports: 123€, 1.234,50€, 1,234.56€, 1234,50€, etc.
    """
    matches = PRICE_RE.findall(text)
    if not matches:
        return None
    
    raw = matches[-1]
    dots = raw.count(".")
    commas = raw.count(",")
    
    if dots > 1:
        if commas > 0:
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(".", "")
    elif dots == 1 and commas == 0:
        after_dot = raw.split(".")[1]
        if len(after_dot) > 2:
            raw = raw.replace(".", "")
    elif commas == 1 and dots == 0:
        raw = raw.replace(",", ".")
    elif dots == 1 and commas == 1:
        if raw.rindex(".") > raw.rindex(","):
            raw = raw.replace(",", "")
        else:
            raw = raw.replace(".", "").replace(",", ".")
    
    try:
        return float(raw)
    except ValueError:
        return None


def date_range(base_date_str=None, days_before=None, days_after=None):
    """Generate date range around BASE_DATE"""
    base = base_date_str or BASE_DATE
    before = days_before if days_before is not None else DAYS_BEFORE
    after = days_after if days_after is not None else DAYS_AFTER
    center = datetime.datetime.strptime(base, "%d/%m/%Y").date()
    for offset in range(-before, after + 1):
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


def get_hotel_catalog(session, search_terms=None):
    """Fetch list of hotels in the Antalya region from Travos API"""
    if search_terms is None:
        search_terms = ["Antalya", "Alanya", "Side", "Belek", "Kemer", "Lara"]
    
    hotels = {}
    dest_country = SEARCH_PARAMS.get("dest_country", "125")
    
    for term in search_terms:
        try:
            resp = session.get(
                AUTOCOMPLETE_URL,
                params={"dest_country": dest_country, "term": term},
                headers=HEADERS,
                timeout=15
            )
            data = resp.json()
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, dict) and "hotel" in v:
                        h_id = str(v["hotel"])
                        full_name = v.get("value", "")
                        clean_name = full_name.split(",")[0].strip() if full_name else f"Hotel {h_id}"
                        hotels[h_id] = {
                            "id": h_id,
                            "name": clean_name,
                            "full_location": full_name
                        }
        except Exception:
            pass
    return hotels


def parse_ajax_offer(html_content, hotel_info, departure_date_str):
    """Parse offer details from Travos AJAX response HTML"""
    soup = BeautifulSoup(html_content, "html.parser")
    
    meal = ""
    for m in MEALS:
        if m.lower() in html_content.lower():
            meal = m
            break
    
    rating = ""
    rating_match = RATING_RE.search(html_content)
    if rating_match:
        rating = rating_match.group(1) or rating_match.group(2)
        if "stele" not in rating.lower() and rating_match.group(1):
            rating = f"{rating} stele"
    
    link = ""
    onclick = soup.find(attrs={"onclick": re.compile(r"window\.open\('([^']+)'")})
    if onclick:
        m = re.search(r"window\.open\('([^']+)'", onclick["onclick"])
        if m:
            link = m.group(1)
    
    if not link:
        days = SEARCH_PARAMS.get("days", "7")
        dep_city = SEARCH_PARAMS.get("dep_city", "193")
        dest_region = SEARCH_PARAMS.get("dest_region", "124")
        dep_country = SEARCH_PARAMS.get("dep_country", "113")
        link = f"https://www.travos.ro/detail.php?from={departure_date_str}&days={days}&dep_country={dep_country}&dep_city={dep_city}&dest_region={dest_region}&a_1=2&hotel_id={hotel_info['id']}"
    
    return meal, rating, link


def fetch_hotel_price(session, hotel_info, departure_date, max_price=None):
    """Fetch price for a single hotel on a specific date via Travos AJAX"""
    date_str = departure_date.strftime("%d/%m/%Y")
    dep_city = int(SEARCH_PARAMS.get("dep_city", 193))
    dest_region = int(SEARCH_PARAMS.get("dest_region", 124))
    dep_country = int(SEARCH_PARAMS.get("dep_country", 113))
    days = int(SEARCH_PARAMS.get("days", 7))
    a_1 = int(SEARCH_PARAMS.get("a_1", 2))
    hotel_id = int(hotel_info["id"])
    
    php_ser = f'a:9:{{s:4:"from";s:10:"{date_str}";s:4:"days";i:{days};s:8:"force_ts";i:1;s:11:"dep_country";i:{dep_country};s:8:"dep_city";i:{dep_city};s:11:"dest_region";i:{dest_region};s:3:"a_1";i:{a_1};s:8:"hotel_id";i:{hotel_id};s:13:"hotelCityName";N;}}'
    b64_params = base64.b64encode(php_ser.encode("utf-8")).decode("utf-8")
    
    try:
        resp = session.post(
            AJAX_SEARCH_URL,
            data={"params": b64_params, "hotelId": str(hotel_id)},
            headers=HEADERS,
            timeout=15
        )
        if resp.status_code != 200:
            return None
        
        data = resp.json()
        price = float(data.get("price", 0))
        content = data.get("content", "")
        
        if price <= 0 or "Nu avem nici" in content:
            return None
        
        if max_price is not None and max_price > 0 and price > max_price:
            return None
        
        meal, rating, url = parse_ajax_offer(content, hotel_info, date_str)
        
        return {
            "hotel": hotel_info["name"],
            "price_eur": price,
            "meal": meal,
            "rating": rating,
            "url": url,
        }
    except Exception:
        return None


def fetch_page(session, departure_date, page):
    """Fetch search results page (HTML fallback)"""
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


def parse_hotels(html, page_url):
    """Parse hotels from HTML (fallback compatibility)"""
    soup = BeautifulSoup(html, "html.parser")
    results = {}
    excluded = load_excluded()

    for a in soup.find_all("a", href=True):
        name = " ".join(a.get_text(" ", strip=True).split())
        href = urljoin(page_url, a["href"])

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
        rating = rating_match.group(1) or rating_match.group(2) if rating_match else ""

        key = (normalize_name(name), href)
        results[key] = {
            "hotel": name,
            "price_eur": price,
            "meal": meal,
            "rating": rating,
            "url": href,
        }

    return list(results.values())


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


def scrape_date(session, departure_date, hotels_catalog=None, max_price=None):
    """Scrape all available hotel offers for a departure date"""
    if hotels_catalog is None:
        hotels_catalog = get_hotel_catalog(session)
    
    excluded = load_excluded()
    found_hotels = []
    
    for h_id, h_info in hotels_catalog.items():
        if normalize_name(h_info["name"]) in excluded:
            continue
        
        offer = fetch_hotel_price(session, h_info, departure_date, max_price=max_price)
        if offer:
            found_hotels.append(offer)
        
        time.sleep(REQUEST_DELAY_SECONDS)
    
    return found_hotels


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="Travos Hotel Price Monitor")
    parser.add_argument("--date", default=BASE_DATE, help="Data centrala (ZZ/LL/AAAA)")
    parser.add_argument("--days-before", type=int, default=DAYS_BEFORE, help="Zile inainte de data centrala")
    parser.add_argument("--days-after", type=int, default=DAYS_AFTER, help="Zile dupa data centrala")
    parser.add_argument("--max-price", type=float, default=MAX_PRICE, help="Pret maxim EUR")
    args = parser.parse_args()

    base_date = args.date
    days_before = args.days_before
    days_after = args.days_after
    max_price = args.max_price

    print("=" * 70)
    print("TRAVOS HOTEL MONITOR")
    print(f"Data centrala: {base_date}")
    print(f"Interval: -{days_before} / +{days_after} zile")
    if max_price:
        print(f"Filtru pret maxim: {max_price:.0f} EUR")
    print("=" * 70)

    conn = sqlite3.connect(DATABASE_FILE)
    init_db(conn)

    session = requests.Session()
    print("\nIncarc catalogul de hoteluri din regiunea Antalya...")
    hotels_catalog = get_hotel_catalog(session)
    print(f"Catalog incarcat: {len(hotels_catalog)} hoteluri gasite.")

    total_hotels = 0
    all_changes = []

    for departure_date in date_range(base_date, days_before, days_after):
        date_str = departure_date.strftime('%d/%m/%Y')
        print(f"\nVerific data de plecare: {date_str}...")

        try:
            hotels = scrape_date(session, departure_date, hotels_catalog, max_price=max_price)
        except Exception as e:
            print(f"EROARE la cautare: {e}")
            continue

        print(f"  -> Hoteluri cu oferte active: {len(hotels)}")
        total_hotels += len(hotels)

        for h in hotels[:5]:
            print(f"     • {h['hotel']}: {h['price_eur']:.0f}€ ({h['meal']})")
        if len(hotels) > 5:
            print(f"     ... si inca {len(hotels) - 5} oferte.")

        changes = save_and_report(conn, departure_date, hotels)
        for hotel, old, new, diff in changes:
            sign = "+" if diff > 0 else ""
            print(f"  {'📈' if diff > 0 else '📉'} {hotel}: "
                  f"{old:.0f}€ -> {new:.0f}€ ({sign}{diff:.0f}€)")
            all_changes.append((departure_date, hotel, old, new, diff))

    conn.close()

    print("\n" + "=" * 70)
    print(f"TOTAL OFERTE EXTRASE: {total_hotels}")
    print(f"MODIFICARI DETECTATE: {len(all_changes)}")
    print("=" * 70)

    if not all_changes:
        print("Prima rulare sau nu au fost detectate modificari fata de verificarile anterioare.")


if __name__ == "__main__":
    main()
