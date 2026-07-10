"""Shared utilities for accounting/CPA practice scrapers.

Ported faithfully from the veterinary TVM rig (which was itself ported from
the dental TDPM rig ~/dental-practice-market-live/scrapers/utils.py). Same
polite-fetch discipline: real browser UA, 1.5-3.5s random delays, tolerant
price parsing. State helpers are shared verbatim (accounting listing codes
like 'TXN6465', 'IL-6622', 'ca2006' are also state-prefixed).
"""

from __future__ import annotations

import re
import logging
import time
import random
from typing import Optional, Tuple

import requests

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}


def get_session() -> requests.Session:
    """Create a requests session with browser-like headers."""
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def polite_delay(min_sec: float = 1.5, max_sec: float = 3.5) -> None:
    """Sleep a random interval to be polite to servers."""
    time.sleep(random.uniform(min_sec, max_sec))


def parse_price(text: Optional[str]) -> Optional[int]:
    """Extract a dollar amount from text like '$455,000' or '$1.2M'."""
    if not text:
        return None
    text = text.strip().replace(",", "").replace("$", "")
    # "1.2 mil" / "1.2 million" / "1.2M"
    m = re.search(r"([\d.]+)\s*(?:mil(?:lion)?\b|M\b)", text, re.I)
    if m:
        return int(float(m.group(1)) * 1_000_000)
    # "600 K" / "600k"
    m = re.search(r"([\d.]+)\s*[Kk]\b", text)
    if m:
        return int(float(m.group(1)) * 1_000)
    # plain number — only accept a full contiguous integer (avoid grabbing the
    # "13" out of "$1.35mil"). Require >= 4 digits to be a plausible dollar sum.
    m = re.fullmatch(r"\d+", text)
    if m and len(text) >= 4:
        return int(text)
    m = re.search(r"\b(\d{4,})\b", text)
    if m:
        return int(m.group(1))
    return None


def clean_text(text: Optional[str]) -> str:
    """Collapse whitespace and strip a string."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


# --- US state helpers (vet listings are location-coded heavily) --------------

STATE_ABBRS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}

STATE_NAME_TO_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}


def parse_location(text: Optional[str]) -> Tuple[str, str]:
    """Best-effort (city, state) from a free-text location string.

    Handles 'San Antonio, TX', 'Billings, Montana', 'Central PA', 'Florida'.
    Returns ("", "") if nothing parseable.
    """
    if not text:
        return "", ""
    text = clean_text(text)

    # "City, ST"
    m = re.search(r"([A-Za-z .'-]+?),\s*([A-Z]{2})\b", text)
    if m and m.group(2) in STATE_ABBRS:
        return m.group(1).strip().title(), m.group(2)

    # "City, State Name"
    m = re.search(r"([A-Za-z .'-]+?),\s*([A-Za-z ]+)$", text)
    if m:
        st = STATE_NAME_TO_ABBR.get(m.group(2).strip().lower())
        if st:
            return m.group(1).strip().title(), st

    # bare state name anywhere
    low = text.lower()
    for name, abbr in STATE_NAME_TO_ABBR.items():
        if re.search(r"\b" + re.escape(name) + r"\b", low):
            return "", abbr

    # bare 2-letter code
    m = re.search(r"\b([A-Z]{2})\b", text)
    if m and m.group(1) in STATE_ABBRS:
        return "", m.group(1)

    return "", ""


def state_from_code(code: Optional[str]) -> str:
    """Extract a state abbr from a broker listing code like 'TX134', 'cav3017',
    'az9', 'fl0005', 'TXN6465', 'IL-6622'. The leading 2 letters are the state."""
    if not code:
        return ""
    m = re.match(r"^([A-Za-z]{2})", code.strip())
    if m and m.group(1).upper() in STATE_ABBRS:
        return m.group(1).upper()
    return ""


# --- accounting practice-type inference -------------------------------------

def infer_firm_type(text: Optional[str]) -> str:
    """Best-effort accounting firm TYPE from title/description/broker 'Type'
    field. Accounting listings cluster into a handful of service mixes; a wrong
    guess is worse than the safe default, so only promote off 'CPA Firm' on a
    clear keyword. Order matters (most-specific first)."""
    t = (text or "").lower()
    if any(k in t for k in ("audit", "attest", "peer review", "assurance")):
        return "Audit/Attest"
    if any(k in t for k in ("wealth", "financial planning", "advisory", "consulting", "fractional cfo", "cfo services")):
        return "Advisory/Consulting"
    if any(k in t for k in ("bookkeep", "write-up", "write up", "payroll")):
        return "Bookkeeping/Payroll"
    if any(k in t for k in ("enrolled agent", " ea ", "ea practice", "ea firm", "ea tax")):
        return "Tax (EA)"
    if any(k in t for k in ("tax prep", "tax practice", "tax firm", "tax and accounting",
                            "tax & accounting", "1040", "tax-only", "tax only")):
        return "Tax"
    if "cpa" in t:
        return "CPA Firm"
    return "Accounting Firm"
