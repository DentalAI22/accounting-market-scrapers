"""
Poe Group Advisors — accounting/CPA practice listings scraper.

Poe (poegroupadvisors.com) lists active US firms at:
    https://poegroupadvisors.com/buying/usa-cpa-firms-for-sale/
each linking to a clean detail page:
    https://poegroupadvisors.com/practice/{code}/     e.g. ca2006, tx2011, fl2003

Detail pages expose structured spec fields:
    .specs-annual-gross-value   -> "$3,556,732"   (UNAMBIGUOUS — captured)
    .specs-asking-price-value   -> "$3,600,000"   (UNAMBIGUOUS — captured)
    body text "Location: California"               -> state
The listing code prefix (ca/tx/fl...) is also the state.

Source: https://poegroupadvisors.com/buying/usa-cpa-firms-for-sale/
Output: output/poe_raw.csv
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
logger = logging.getLogger("poe")

BASE_URL = "https://poegroupadvisors.com"
LISTINGS_URL = "{}/buying/usa-cpa-firms-for-sale/".format(BASE_URL)
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "poe_raw.csv")

FIELDNAMES = [
    "source_id", "title", "city", "state", "asking_price", "annual_revenue",
    "practice_type", "description", "broker_name", "listing_url",
    "client_count", "listing_code",
]


def spec_value(soup, cls: str) -> str:
    el = soup.select_one("." + cls)
    return clean_text(el.get_text()) if el else ""


def scrape_detail(session, url: str, code: str) -> Optional[Dict]:
    try:
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            return None
    except Exception as e:
        logger.warning("Poe detail failed %s: %s", code, e)
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    h1 = soup.find("h1")
    title = clean_text(h1.get_text()) if h1 else code.upper()

    page_text = soup.get_text(" ", strip=True)
    if re.search(r"\bthis\s+(?:practice|firm)\s+(?:has\s+)?sold\b|\bsale\s+pending\b|\bsold\b",
                 title, re.I):
        return None

    annual_revenue = parse_price(spec_value(soup, "specs-annual-gross-value"))
    asking_price = parse_price(spec_value(soup, "specs-asking-price-value"))
    if annual_revenue is not None and annual_revenue < 10_000:
        annual_revenue = None
    if asking_price is not None and asking_price < 10_000:
        asking_price = None

    # state: body "Location: <State>", else code prefix, else title
    state = ""
    m = re.search(r"Location[:\s]+([A-Za-z .]+?)(?:Reason|Asking|Annual|Cash|Employees|$)",
                  page_text)
    if m:
        loc = m.group(1).strip()
        state = STATE_NAME_TO_ABBR.get(loc.lower(), "")
        if not state:
            _, state = parse_location(loc)
    if not state:
        state = state_from_code(code)
    if not state:
        _, state = parse_location(title)

    # skip the "registered buyers, email us" CTA boilerplate; take the first
    # substantive firm-description paragraph
    description = ""
    for p in soup.find_all("p"):
        t = clean_text(p.get_text())
        if len(t) > 100 and not re.search(
                r"registered\s+buyers|email\s+us\s+at|request\s+a\s+full\s+profile",
                t, re.I):
            description = t[:600]
            break

    practice_type = infer_firm_type(title + " " + description)

    return {
        "source_id": "poe-{}".format(code.lower()),
        "title": title,
        "city": "",
        "state": state,
        "asking_price": asking_price,
        "annual_revenue": annual_revenue,
        "practice_type": practice_type,
        "description": description,
        "broker_name": "Poe Group Advisors",
        "listing_url": url,
        "client_count": None,
        "listing_code": code.upper(),
    }


def run() -> List[Dict]:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    session = get_session()
    logger.info("Fetching Poe USA index: %s", LISTINGS_URL)
    try:
        resp = session.get(LISTINGS_URL, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.error("Failed to fetch Poe index: %s", e)
        with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    codes = {}
    for a in soup.find_all("a", href=True):
        m = re.search(r"/practice/([a-z]{2}\w{2,6})/?$", a["href"].split("?")[0], re.I)
        if m:
            code = m.group(1)
            url = a["href"].split("?")[0]
            if not url.startswith("http"):
                url = BASE_URL + url
            codes[code.lower()] = url
    logger.info("Found %d Poe /practice/ links", len(codes))

    all_listings, seen = [], set()
    items = list(codes.items())
    for i, (code, url) in enumerate(items, 1):
        polite_delay(1.5, 3.0)
        listing = scrape_detail(session, url, code)
        if listing and listing["source_id"] not in seen:
            seen.add(listing["source_id"])
            all_listings.append(listing)
            logger.info("  [%d/%d] %s — %s — gross $%s / ask $%s",
                        i, len(items), listing["source_id"], listing["state"] or "?",
                        listing.get("annual_revenue") or "N/A",
                        listing.get("asking_price") or "N/A")

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_listings)
    logger.info("Wrote %d Poe listings to %s", len(all_listings), OUTPUT_FILE)
    return all_listings


if __name__ == "__main__":
    results = run()
    print("Done. {} listings saved to {}".format(len(results), OUTPUT_FILE))
