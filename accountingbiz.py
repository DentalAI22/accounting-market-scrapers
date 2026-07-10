"""
Accounting Biz Brokers (ABB) — accounting/CPA practice listings scraper.

ABB (accountingbizbrokers.com) runs a WordPress + Elementor/JetEngine site.
The public /listings page renders cards client-side (no server HTML), but the
XML sitemap exposes every listing detail URL:
    https://accountingbizbrokers.com/listing-sitemap1.xml
    -> https://accountingbizbrokers.com/listing/{slug}/     e.g. sw-kansas-cpa-firm

The sitemap holds ~315 URLs, but most are SOLD/archived (lastmod 2024). We
keep only recently-touched listings and drop any whose page reads as sold.
Detail pages present the specs as Elementor headings in flattened text:
    "Asking Price ... $520,000"   (UNAMBIGUOUS — captured)
    "Annual Gross ... $520,000"   (UNAMBIGUOUS — captured)
State is derived from the title/slug (e.g. "SW Kansas CPA Firm" -> KS).

Source: https://accountingbizbrokers.com/listings/
Output: output/accountingbiz_raw.csv
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
logger = logging.getLogger("accountingbiz")

BASE_URL = "https://accountingbizbrokers.com"
SITEMAP_URL = "{}/listing-sitemap1.xml".format(BASE_URL)
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "accountingbiz_raw.csv")

# Only keep listings touched on/after this date (drops the 2024 sold archive).
RECENT_CUTOFF = "2025-06-01"

FIELDNAMES = [
    "source_id", "title", "city", "state", "asking_price", "annual_revenue",
    "practice_type", "description", "broker_name", "listing_url",
    "client_count", "listing_code",
]

ASK_RE = re.compile(r"Asking\s+Price\s*\$?\s*([\d.,]+\s*(?:mil(?:lion)?|k)?)", re.I)
GROSS_RE = re.compile(
    r"(?:Annual\s+Gross|Gross(?:\s+Revenue)?)\s*\$?\s*([\d.,]+\s*(?:mil(?:lion)?|k)?)", re.I)


def state_from_title(title: str) -> str:
    low = title.lower()
    for name, abbr in STATE_NAME_TO_ABBR.items():
        if re.search(r"\b" + re.escape(name) + r"\b", low):
            return abbr
    _, st = parse_location(title)
    return st


def scrape_detail(session, url: str, slug: str) -> Optional[Dict]:
    try:
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            return None
    except Exception as e:
        logger.warning("ABB detail failed %s: %s", slug, e)
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    h1 = soup.find("h1")
    title = clean_text(h1.get_text()) if h1 else slug.replace("-", " ").title()

    page_text = soup.get_text(" ", strip=True)
    if re.search(r"\bsold\b|under\s+contract|no\s+longer\s+available", title, re.I):
        return None

    asking_price = None
    m = ASK_RE.search(page_text)
    if m:
        v = parse_price(m.group(1))
        if v and v >= 10_000:
            asking_price = v

    annual_revenue = None
    m = GROSS_RE.search(page_text)
    if m:
        v = parse_price(m.group(1))
        if v and v >= 10_000:
            annual_revenue = v

    state = state_from_title(title) or state_from_code(slug.replace("-", ""))

    description = ""
    for p in soup.find_all("p"):
        t = clean_text(p.get_text())
        if len(t) > 100 and ("firm" in t.lower() or "practice" in t.lower()
                             or "revenue" in t.lower() or "cpa" in t.lower()):
            description = t[:600]
            break

    practice_type = infer_firm_type(title + " " + description)

    return {
        "source_id": "abb-{}".format(re.sub(r"[^a-z0-9]+", "-", slug.lower())[:48]),
        "title": title,
        "city": "",
        "state": state,
        "asking_price": asking_price,
        "annual_revenue": annual_revenue,
        "practice_type": practice_type,
        "description": description,
        "broker_name": "Accounting Biz Brokers",
        "listing_url": url,
        "client_count": None,
        "listing_code": "",
    }


def fetch_sitemap_urls(session) -> List[str]:
    try:
        resp = session.get(SITEMAP_URL, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.error("ABB sitemap failed: %s", e)
        return []
    xml = resp.text
    # pair each <loc> with its <lastmod> if present
    entries = re.findall(
        r"<url>\s*<loc>(https://accountingbizbrokers\.com/listing/[^<]+?)</loc>"
        r"(?:\s*<lastmod>([^<]+)</lastmod>)?", xml)
    keep = []
    for loc, lastmod in entries:
        if loc.rstrip("/").endswith("/listing"):
            continue  # the archive index itself
        if lastmod and lastmod[:10] < RECENT_CUTOFF:
            continue  # stale/sold archive
        keep.append(loc)
    logger.info("ABB sitemap: %d listing URLs, %d recent (>= %s)",
                len(entries), len(keep), RECENT_CUTOFF)
    return keep


def run() -> List[Dict]:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    session = get_session()
    urls = fetch_sitemap_urls(session)
    if not urls:
        with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()
        return []

    all_listings, seen = [], set()
    for i, url in enumerate(urls, 1):
        slug = re.search(r"/listing/([^/]+)/?", url).group(1)
        polite_delay(1.5, 3.0)
        listing = scrape_detail(session, url, slug)
        if listing and listing["source_id"] not in seen:
            seen.add(listing["source_id"])
            all_listings.append(listing)
            logger.info("  [%d/%d] %s — %s — ask $%s / gross $%s",
                        i, len(urls), listing["source_id"], listing["state"] or "?",
                        listing.get("asking_price") or "N/A",
                        listing.get("annual_revenue") or "N/A")

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_listings)
    logger.info("Wrote %d ABB listings to %s", len(all_listings), OUTPUT_FILE)
    return all_listings


if __name__ == "__main__":
    results = run()
    print("Done. {} listings saved to {}".format(len(results), OUTPUT_FILE))
