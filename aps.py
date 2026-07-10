"""
Accounting Practice Sales (APS) — the category giant — listings scraper.

APS (accountingpracticesales.com) is the largest dedicated accounting-practice
brokerage in North America. Its public listing browser renders clean, fully
structured cards on region pages:

    https://accountingpracticesales.com/Worldwide/all/{page}/

Each card is an <a class="apslistingitem_n"> holding:
    .apslistingitem_lname        title (e.g. "Dallas, TX CPA Practice")
    .apslistingitem_lstatus      "New" / status
    .listingstatscontainer
        .listingstat > .listingstattitle / .listingstatinfo  (label/value pairs)
            Listing #      -> broker code (e.g. TXN6465, state-prefixed)
            Location       -> state name ("Texas") or region
            Annual Revenue -> $ (UNAMBIGUOUS — captured)
            Asking Price   -> $ (UNAMBIGUOUS — captured)
            Type           -> "TAX AUDIT" / "TAX" / "CPA" etc.

Everything the card needs is on the index — NO detail-page fetch required for
core fields (fast + polite). We paginate until a page returns zero cards.

Source: https://accountingpracticesales.com/Worldwide/all/
Output: output/aps_raw.csv
"""

from __future__ import annotations

import csv
import logging
import os
import re
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

from utils import (get_session, polite_delay, parse_price, clean_text,
                   parse_location, state_from_code, infer_firm_type,
                   STATE_NAME_TO_ABBR)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("aps")

BASE_URL = "https://accountingpracticesales.com"
REGION_URL = "{}/Worldwide/all".format(BASE_URL)   # /{page}/ appended
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "aps_raw.csv")

MAX_PAGES = 20   # 147 listings / 20-per-page ~= 8 pages; 20 is a safe ceiling

FIELDNAMES = [
    "source_id", "title", "city", "state", "asking_price", "annual_revenue",
    "practice_type", "description", "broker_name", "listing_url",
    "client_count", "listing_code",
]


def stat_value(card, label_substr: str) -> str:
    """Return the .listingstatinfo text whose sibling .listingstattitle contains
    label_substr (case-insensitive), or ''. """
    for stat in card.select(".listingstat"):
        title_el = stat.select_one(".listingstattitle")
        info_el = stat.select_one(".listingstatinfo")
        if not title_el or not info_el:
            continue
        if label_substr.lower() in clean_text(title_el.get_text()).lower():
            return clean_text(info_el.get_text())
    return ""


def parse_card(card) -> Optional[Dict]:
    name_el = card.select_one(".apslistingitem_lname")
    title = clean_text(name_el.get_text()) if name_el else ""
    if not title:
        return None

    listing_url = card.get("href", "")
    if listing_url and not listing_url.startswith("http"):
        listing_url = BASE_URL + listing_url

    code = stat_value(card, "Listing")            # e.g. TXN6465
    location = stat_value(card, "Location")       # e.g. "Texas"
    revenue_raw = stat_value(card, "Revenue")     # "$334,000"
    price_raw = stat_value(card, "Asking")        # "$400,000"
    type_raw = stat_value(card, "Type")           # "TAX AUDIT"
    status = ""
    st_el = card.select_one(".apslistingitem_lstatus")
    if st_el:
        status = clean_text(st_el.get_text())

    # skip anything explicitly sold / off-market
    if re.search(r"\bsold\b|under\s+contract|off\s*market", (status + " " + title), re.I):
        return None

    # state: prefer the Location field (state name), fall back to the code prefix
    city, state = "", ""
    if location:
        city, state = parse_location(location)
        if not state:
            state = STATE_NAME_TO_ABBR.get(location.strip().lower(), "")
    if not state:
        state = state_from_code(code)

    # money — APS shows these unambiguously on the card, so parse directly
    annual_revenue = parse_price(revenue_raw) if revenue_raw else None
    asking_price = parse_price(price_raw) if price_raw else None
    if annual_revenue is not None and annual_revenue < 10_000:
        annual_revenue = None
    if asking_price is not None and asking_price < 10_000:
        asking_price = None

    practice_type = infer_firm_type("{} {} {}".format(type_raw, title, ""))

    # source_id: prefer the broker code; else derive from the listing url tail
    if code:
        source_id = "aps-{}".format(code.lower())
    else:
        tail = re.search(r"/(\d{4,6})-", listing_url or "")
        source_id = "aps-{}".format(tail.group(1)) if tail else ""
    if not source_id:
        return None

    description = ""  # APS gates the narrative behind buyer registration; card is enough

    return {
        "source_id": source_id,
        "title": title,
        "city": city,
        "state": state,
        "asking_price": asking_price,
        "annual_revenue": annual_revenue,
        "practice_type": practice_type,
        "description": description,
        "broker_name": "Accounting Practice Sales",
        "listing_url": listing_url,
        "client_count": None,
        "listing_code": code,
    }


def run() -> List[Dict]:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    session = get_session()
    all_listings: List[Dict] = []
    seen = set()          # de-dupes active listings across pages (source_id)
    seen_cards = set()     # de-dupes ALL cards (incl. sold) to detect real end-of-list

    # Page URLs: the bare region page is the first page, then /1/, /2/, ...
    # Cards carry a per-listing "SOLD" status which parse_card drops, so a page
    # can legitimately yield 0 *new active* listings while later pages have more
    # — we page until we hit a page with no NEW cards at all (pagination looped
    # / ran out), NOT when active-new hits 0.
    page_urls = ["{}/".format(REGION_URL)] + \
                ["{}/{}/".format(REGION_URL, p) for p in range(1, MAX_PAGES + 1)]

    for idx, url in enumerate(page_urls):
        logger.info("Fetching APS page %d: %s", idx, url)
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            logger.warning("APS page %d failed: %s", idx, e)
            break

        soup = BeautifulSoup(resp.text, "lxml")
        cards = soup.select(".apslistingitem_n")
        if not cards:
            logger.info("No cards on page %d — end of listings.", idx)
            break

        page_new_cards = 0   # any card (active or sold) not seen before
        page_new_active = 0
        for card in cards:
            name_el = card.select_one(".apslistingitem_lname")
            code = stat_value(card, "Listing")
            card_key = code or (clean_text(name_el.get_text()) if name_el else "")
            if card_key and card_key in seen_cards:
                continue
            if card_key:
                seen_cards.add(card_key)
                page_new_cards += 1
            listing = parse_card(card)
            if listing and listing["source_id"] not in seen:
                seen.add(listing["source_id"])
                all_listings.append(listing)
                page_new_active += 1
        logger.info("  page %d: %d cards, %d new-cards, %d new-active",
                    idx, len(cards), page_new_cards, page_new_active)

        # stop only when a page shows no NEW cards at all (pagination exhausted)
        if page_new_cards == 0:
            logger.info("  page %d added no new cards — end of pagination.", idx)
            break
        polite_delay(1.5, 3.0)

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_listings)
    logger.info("Wrote %d APS listings to %s", len(all_listings), OUTPUT_FILE)
    return all_listings


if __name__ == "__main__":
    results = run()
    print("Done. {} listings saved to {}".format(len(results), OUTPUT_FILE))
