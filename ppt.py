"""
Private Practice Transitions (PPT) — accounting/tax slice scraper.

PPT (privatepracticetransitions.com) is a multi-vertical practice broker. Its
accounting-tax category page renders full listing cards server-side (no detail
fetch needed):
    https://privatepracticetransitions.com/business-industry/accounting-tax/

Each card (article.business-listing / .industryItem-content-area) carries:
    location line (State, County)
    "1241 – Established Portland Metro Tax & Accounting Firm"   (number + title)
    Gross Revenue: $1,026,839    (UNAMBIGUOUS — captured)
    SDE: $358,466
    EBITDA: $138,466
    Asking Price: $1,500,000     (UNAMBIGUOUS — captured)
    link -> /business-listing/{slug}/

The accounting slice is small (single digits) — this is a breadth/redundancy
source, not a volume driver.

Source: https://privatepracticetransitions.com/business-industry/accounting-tax/
Output: output/ppt_raw.csv
"""

from __future__ import annotations

import csv
import logging
import os
import re
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

from utils import (get_session, parse_price, clean_text, parse_location,
                   infer_firm_type, STATE_NAME_TO_ABBR)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ppt")

BASE_URL = "https://privatepracticetransitions.com"
LISTINGS_URL = "{}/business-industry/accounting-tax/".format(BASE_URL)
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "ppt_raw.csv")

FIELDNAMES = [
    "source_id", "title", "city", "state", "asking_price", "annual_revenue",
    "practice_type", "description", "broker_name", "listing_url",
    "client_count", "listing_code",
]


# Common US metros/counties that appear in accounting listing titles without an
# explicit state. Kept small + unambiguous; a wrong state fails buyer diligence.
METRO_TO_STATE = {
    "portland metro": "OR", "portland": "OR", "king county": "WA",
    "seattle": "WA", "tacoma": "WA", "spokane": "WA", "puget sound": "WA",
    "bay area": "CA", "los angeles": "CA", "san diego": "CA",
    "sacramento": "CA", "san francisco": "CA", "orange county": "CA",
    "phoenix": "AZ", "tucson": "AZ", "denver": "CO", "las vegas": "NV",
    "reno": "NV", "boise": "ID", "salt lake": "UT", "twin cities": "MN",
}


def metro_hint(text: str) -> str:
    low = (text or "").lower()
    for metro, abbr in METRO_TO_STATE.items():
        if metro in low:
            return abbr
    return ""


def labeled_money(text: str, label: str) -> Optional[int]:
    m = re.search(re.escape(label) + r"\s*:?\s*\$?\s*([\d.,]+\s*(?:mil(?:lion)?|k)?)",
                  text, re.I)
    if not m:
        return None
    v = parse_price(m.group(1))
    return v if (v and v >= 10_000) else None


def parse_card(card) -> Optional[Dict]:
    text = card.get_text(" ", strip=True)
    if "Gross Revenue" not in text and "Asking Price" not in text:
        return None

    # detail link + slug
    a = card.find("a", href=re.compile(r"/business-listing/"))
    listing_url = a["href"] if a else ""
    if listing_url and not listing_url.startswith("http"):
        listing_url = BASE_URL + listing_url
    slug = ""
    m = re.search(r"/business-listing/([^/]+)/?", listing_url)
    if m:
        slug = m.group(1)

    # title: the "1241 – ..." heading
    title = ""
    for tag in card.find_all(["h2", "h3", "h4"]):
        t = clean_text(tag.get_text())
        if t and not re.fullmatch(r"[A-Za-z ,]+", t):  # skip pure location headers
            title = t
            break
    if not title and slug:
        title = slug.replace("-", " ").title()
    if not title:
        return None

    # listing number prefix (e.g. "1241 – ...")
    code = ""
    m = re.match(r"^\s*(\d{3,5})\s*[–-]", title)
    if m:
        code = m.group(1)

    # state — PPT titles use metro/county names (e.g. "Portland Metro",
    # "King County") not state names, so try (1) an explicit state name,
    # (2) a metro/county hint, (3) parse_location. "Virtual" stays blank
    # (genuinely locationless — honest null, same as vet's confidential titles).
    state = ""
    for name, abbr in STATE_NAME_TO_ABBR.items():
        if re.search(r"\b" + re.escape(name) + r"\b", text, re.I):
            state = abbr
            break
    if not state:
        state = metro_hint(title)
    if not state:
        _, state = parse_location(text)

    annual_revenue = labeled_money(text, "Gross Revenue")
    asking_price = labeled_money(text, "Asking Price")

    practice_type = infer_firm_type(title)

    source_id = "ppt-{}".format(code) if code else "ppt-{}".format(
        re.sub(r"[^a-z0-9]+", "-", slug.lower())[:40])
    if source_id in ("ppt-", "ppt"):
        return None

    return {
        "source_id": source_id,
        "title": title,
        "city": "",
        "state": state,
        "asking_price": asking_price,
        "annual_revenue": annual_revenue,
        "practice_type": practice_type,
        "description": "",
        "broker_name": "Private Practice Transitions",
        "listing_url": listing_url or LISTINGS_URL,
        "client_count": None,
        "listing_code": code,
    }


def run() -> List[Dict]:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    session = get_session()
    logger.info("Fetching PPT accounting-tax: %s", LISTINGS_URL)
    try:
        resp = session.get(LISTINGS_URL, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.error("Failed to fetch PPT: %s", e)
        with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    cards = soup.select("article.business-listing") or soup.select(".industryItem-content-area")
    logger.info("Found %d PPT accounting cards", len(cards))

    all_listings, seen = [], set()
    for card in cards:
        listing = parse_card(card)
        if listing and listing["source_id"] not in seen:
            seen.add(listing["source_id"])
            all_listings.append(listing)
            logger.info("  %s — %s — gross $%s / ask $%s",
                        listing["source_id"], listing["state"] or "?",
                        listing.get("annual_revenue") or "N/A",
                        listing.get("asking_price") or "N/A")

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_listings)
    logger.info("Wrote %d PPT listings to %s", len(all_listings), OUTPUT_FILE)
    return all_listings


if __name__ == "__main__":
    results = run()
    print("Done. {} listings saved to {}".format(len(results), OUTPUT_FILE))
