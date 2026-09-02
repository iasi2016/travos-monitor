import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
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
    MAX_PRICE, MAX_WORKERS
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
RATING_RE = re.compile(r"(\d+)\s*stele|filter-rating-(\d+)|Nota\s*([\d.,]+)", re.I)

# Doar All Inclusive si Ultra All Inclusive
ALLOWED_MEALS = ["Ultra All Inclusive", "All Inclusive"]


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


def parse_ajax_meal(content):
    """Parse and filter strictly for All Inclusive and Ultra All Inclusive"""
    content_lower = content.lower()
    if "ultra all inclusive" in content_lower or "ultra all-inclusive" in content_lower:
        return "Ultra All Inclusive"
    if "all inclusive" in content_lower or "all-inclusive" in content_lower:
        return "All Inclusive"
    return None


def fetch_hotel_price(session, hotel_info, departure_date, max_price=None):
    """
    Fetch price for a single hotel on a specific date via Travos AJAX.
    Strictly: Charter flight from Iasi (dep_city = 193) & All Inclusive only.
    """
    date_str = departure_date.strftime("%d/%m/%Y")
    dep_city = int(SEARCH_PARAMS.get("dep_city", 193))  # 193 = Iasi
    dest_region = int(SEARCH_PARAMS.get("dest_region", 124))  # 124 = Antalya
    dep_country = int(SEARCH_PARAMS.get("dep_country", 113))  # 113 = Romania
    days = int(SEARCH_PARAMS.get("days", 7))
    a_1 = int(SEARCH_PARAMS.get("a_1", 2))
    hotel_id = int(hotel_info["id"])
    
    # PHP serialized format for Travos search.php AJAX endpoint (force_ts=1 pentru charter)
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
        
        # Filtru strict: Doar All Inclusive si Ultra All Inclusive
        meal = parse_ajax_meal(content)
        if not meal:
            return None
        
        # Rating / Stele
        rating = ""
        r_match = RATING_RE.search(content)
        if r_match:
            stars = r_match.group(1) or r_match.group(2) or r_match.group(3)
            if stars:
                rating = f"{stars} stele" if "stele" not in stars.lower() else stars
        
        return {
            "hotel": hotel_info["name"],
            "price_eur": price,
            "meal": meal,
            "rating": rating,
            "url": "",  # Fara link conform solicitarii
        }
    except Exception:
        return None


def fetch_page(session, departure_date, page):
    """Fetch search results page (HTML fallback compatibility)"""
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
                if any(m.lower() in text.lower() for m in ALLOWED_MEALS):
                    break

        if "€" not in best_text:
            continue

        if normalize_name(name) in excluded:
            continue

        price = extract_price(best_text)
        if price is None:
            continue

        meal = ""
        for m in ALLOWED_MEALS:
            if m.lower() in best_text.lower():
                meal = m
                break
        if not meal:
            continue

        rating_match = RATING_RE.search(best_text)
        rating = rating_match.group(1) or rating_match.group(2) if rating_match else ""

        key = (normalize_name(name), href)
        results[key] = {
            "hotel": name,
            "price_eur": price,
            "meal": meal,
            "rating": rating,
            "url": "",
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
            h.get("url", ""),
        ))

        if old_price is not None:
            diff = h["price_eur"] - old_price
            if abs(diff) > 0.001:
                changes.append((h["hotel"], old_price, h["price_eur"], diff))

    conn.commit()
    return changes


def scrape_date(session, departure_date, hotels_catalog=None, max_price=None, max_workers=MAX_WORKERS):
    """Scrape all available hotel offers for a departure date using concurrent threads"""
    if hotels_catalog is None:
        hotels_catalog = get_hotel_catalog(session)
    
    excluded = load_excluded()
    catalog_filtered = [
        h for h in hotels_catalog.values()
        if normalize_name(h["name"]) not in excluded
    ]
    
    found_hotels = []

    def fetch_task(h_info):
        return fetch_hotel_price(session, h_info, departure_date, max_price=max_price)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = executor.map(fetch_task, catalog_filtered)
        for res in results:
            if res:
                found_hotels.append(res)
    
    # Sortare dupa pret crescator
    found_hotels.sort(key=lambda x: x["price_eur"])
    return found_hotels


def create_configured_session():
    """Create a requests session with connection pooling for high-performance requests"""
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=25,
        pool_maxsize=25,
        max_retries=2
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    # Warm up session with initial page to receive cookies
    try:
        session.get("https://www.travos.ro/", headers=HEADERS, timeout=10)
    except Exception:
        pass
    return session


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="Travos Hotel Price Monitor (Charter Iasi -> Antalya)")
    parser.add_argument("--date", default=BASE_DATE, help="Data centrala (ZZ/LL/AAAA)")
    parser.add_argument("--days-before", type=int, default=DAYS_BEFORE, help="Zile inainte de data centrala")
    parser.add_argument("--days-after", type=int, default=DAYS_AFTER, help="Zile dupa data centrala")
    parser.add_argument("--max-price", type=float, default=MAX_PRICE, help="Pret maxim EUR")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS, help="Numar thread-uri paralele")
    args = parser.parse_args()

    base_date = args.date
    days_before = args.days_before
    days_after = args.days_after
    max_price = args.max_price
    max_workers = args.workers

    print("=" * 70)
    print("TRAVOS HOTEL MONITOR (CHARTER IASI -> ANTALYA)")
    print(f"Data centrala: {base_date}")
    print(f"Interval: -{days_before} / +{days_after} zile")
    print("Regim masa: DOAR All Inclusive & Ultra All Inclusive")
    print("Zbor: DOAR Charter din Iasi")
    if max_price:
        print(f"Filtru pret maxim: {max_price:.0f} EUR")
    print("=" * 70)

    conn = sqlite3.connect(DATABASE_FILE)
    init_db(conn)

    session = create_configured_session()
    print("\nIncarc catalogul de hoteluri din regiunea Antalya...")
    t_cat = time.time()
    hotels_catalog = get_hotel_catalog(session)
    print(f"Catalog incarcat in {time.time() - t_cat:.1f}s: {len(hotels_catalog)} hoteluri.")

    total_hotels = 0
    all_changes = []

    for departure_date in date_range(base_date, days_before, days_after):
        date_str = departure_date.strftime('%d/%m/%Y')
        print(f"\nVerific plecare din Iasi: {date_str}...")
        t_start = time.time()

        try:
            hotels = scrape_date(session, departure_date, hotels_catalog, max_price=max_price, max_workers=max_workers)
        except Exception as e:
            print(f"EROARE la cautare: {e}")
            continue

        elapsed = time.time() - t_start
        print(f"  -> {len(hotels)} oferte All Inclusive gasite ({elapsed:.1f} secunde)")
        total_hotels += len(hotels)

        # Afisam primele cele mai bune oferte (fara link)
        for h in hotels[:8]:
            stars_str = f" | {h['rating']}" if h['rating'] else ""
            print(f"     • {h['hotel']}: {h['price_eur']:.0f}€ | {h['meal']}{stars_str}")
        if len(hotels) > 8:
            print(f"     ... si inca {len(hotels) - 8} hoteluri in baza de date.")

        changes = save_and_report(conn, departure_date, hotels)
        for hotel, old, new, diff in changes:
            sign = "+" if diff > 0 else ""
            print(f"  {'📈' if diff > 0 else '📉'} {hotel}: "
                  f"{old:.0f}€ -> {new:.0f}€ ({sign}{diff:.0f}€)")
            all_changes.append((departure_date, hotel, old, new, diff))

    conn.close()

    print("\n" + "=" * 70)
    print(f"TOTAL OFERTE EXTRASE: {total_hotels}")
    print(f"MODIFICARI DE PRET DETECTATE: {len(all_changes)}")
    print("=" * 70)

    if not all_changes:
        print("Prima rulare sau nu au fost detectate modificari de pret.")


if __name__ == "__main__":
    main()
