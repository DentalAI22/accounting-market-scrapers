"""
NAAB Consulting — accounting/CPA practice listings scraper.

NAAB (naabconsulting.com) lists on a single index page:
    https://www.naabconsulting.com/practices-for-sale/
each linking to a detail page:
    /practice-listing/{ST}-{code}-{area}/    e.g. il-6622-northwest-chicago-suburb
    /practice-listing/{descriptive-slug}/    e.g. cpa-practice-for-sale-cleveland-suburb-ohio

The URL slug carries the state (leading 2 letters, or a state name in the
words). Detail pages state the gross clearly: "Grossing: $1,062,500".

Source: https://www.naabconsulting.com/practices-for-sale/
Output: output/naab_raw.csv
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
logger = logging.getLogger("naab")

BASE_URL = "https://www.naabconsulting.com"
LISTINGS_URL = "{}/practices-for-sale/".format(BASE_URL)
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "naab_raw.csv")

FIELDNAMES = [
    "source_id", "title", "city", "state", "asking_price", "annual_revenue",
    "practice_type", "description", "broker_name", "listing_url",
    "client_count", "listing_code",
]

GROSS_RE = re.compile(
    r"(?:grossing|gross(?:\s+revenue)?|revenues?\s+of|annual\s+(?:gross|revenue))"
    r"[:\s]*\$?\s*([\d.,]+\s*(?:mil(?:lion)?|k)?)", re.I)
ASK_RE = re.compile(
    r"(?:asking\s*(?:price)?|listed\s+at|offered\s+at)[:\s]*\$?\s*"
    r"([\d.,]+\s*(?:mil(?:lion)?|k)?)", re.I)


def state_from_slug(slug: str) -> str:
    """Pull a state from a NAAB slug like 'il-6622-...' or 'cleveland-...-ohio'."""
    s = slug.lower()
    m = re.match(r"^([a-z]{2})[-0-9]", s)
    if m and m.group(1).upper() in {v for v in STATE_NAME_TO_ABBR.values()}:
        return m.group(1).upper()
    if m:
        st = state_from_code(m.group(1))
        if st:
            return st
    # state name embedded in the slug words
    words = s.replace("-", " ")
    for name, abbr in STATE_NAME_TO_ABBR.items():
        if re.search(r"\b" + re.escape(name) + r"\b", words):
            return abbr
    return ""


def code_from_slug(slug: str) -> str:
    """Broker code from slug, e.g. 'il-6622-...' -> 'IL6622'."""
    m = re.match(r"^([a-z]{2})-?(\d{3,6})", slug.lower())
    if m:
        return (m.group(1) + m.group(2)).upper()
    return ""


def scrape_detail(session, url: str, slug: str) -> Optional[Dict]:
    try:
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            return None
    except Exception as e:
        logger.warning("NAAB detail failed %s: %s", slug, e)
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    # title: prefer the H1
    h1 = soup.find("h1")
    title = clean_text(h1.get_text()) if h1 else slug.replace("-", " ").title()

    page_text = soup.get_text(" ", strip=True)
    if re.search(r"\bthis\s+practice\s+(?:has\s+)?sold\b|\bstatus:\s*sold\b", page_text, re.I):
        return None

    annual_revenue = None
    m = GROSS_RE.search(page_text)
    if m:
        v = parse_price(m.group(1))
        if v and v >= 10_000:
            annual_revenue = v

    asking_price = None
    m = ASK_RE.search(page_text)
    if m:
        v = parse_price(m.group(1))
        if v and v >= 10_000:
            asking_price = v

    state = state_from_slug(slug)
    city, st2 = parse_location(title)
    if not state:
        state = st2

    # a short clean description from the first substantive paragraph
    description = ""
    for p in soup.find_all("p"):
        t = clean_text(p.get_text())
        if len(t) > 100 and ("practice" in t.lower() or "firm" in t.lower()
                             or "gross" in t.lower() or "revenue" in t.lower()):
            description = t[:600]
            break

    code = code_from_slug(slug)
    source_id = "naab-{}".format(code.lower()) if code else "naab-{}".format(
        re.sub(r"[^a-z0-9]+", "-", slug.lower())[:40])

    practice_type = infer_firm_type(title + " " + description)

    return {
        "source_id": source_id,
        "title": title,
        "city": city,
        "state": state,
        "asking_price": asking_price,
        "annual_revenue": annual_revenue,
        "practice_type": practice_type,
        "description": description,
        "broker_name": "NAAB Consulting",
        "listing_url": url,
        "client_count": None,
        "listing_code": code,
    }


def run() -> List[Dict]:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    session = get_session()
    logger.info("Fetching NAAB index: %s", LISTINGS_URL)
    try:
        resp = session.get(LISTINGS_URL, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.error("Failed to fetch NAAB index: %s", e)
        with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    urls = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.search(r"/practice-listing/([a-z0-9-]+)/?", href)
        if not m:
            continue
        slug = m.group(1)
        full = href.split("?")[0]
        if not full.startswith("http"):
            full = BASE_URL + full
        urls[slug] = full
    logger.info("Found %d NAAB detail links", len(urls))

    all_listings, seen = [], set()
    items = list(urls.items())
    for i, (slug, url) in enumerate(items, 1):
        polite_delay(1.5, 3.0)
        listing = scrape_detail(session, url, slug)
        if listing and listing["source_id"] not in seen:
            seen.add(listing["source_id"])
            all_listings.append(listing)
            logger.info("  [%d/%d] %s — %s — gross $%s",
                        i, len(items), listing["source_id"],
                        listing["state"] or "?",
                        listing.get("annual_revenue") or "N/A")

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_listings)
    logger.info("Wrote %d NAAB listings to %s", len(all_listings), OUTPUT_FILE)
    return all_listings


if __name__ == "__main__":
    results = run()
    print("Done. {} listings saved to {}".format(len(results), OUTPUT_FILE))
