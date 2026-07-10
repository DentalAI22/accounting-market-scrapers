# accounting-market-scrapers

Public scraper rig for **The Accounting Market** network vertical. Aggregates
real, public-source accounting/CPA-firm-for-sale listings from dedicated
accounting practice-sales brokers and publishes a single canonical
`listings.json` that the live sites consume.

**Live sites fed by this repo:**
- https://theaccountingmarket.com (Vercel project `accounting`)
- https://theaccountingpracticemarket.com (Vercel project `accountingpractice`)

Everything in this repo is scraper code + public listing data. **No secrets, no
tokens, no seller PII.** Firm names are generically redacted (`CPA Firm`,
`Tax Practice`, …); the final dataset contains 0 emails / 0 phones.

## What it does

```
run_all.py  ->  per-source scrapers (aps, accountingbiz, naab, poe, ppt)
             ->  output/*_raw.csv  ->  normalizer.py
             ->  listings.json  (canonical, TAM-XXXXX siteIds, deduped)
```

- `utils.py` — real UA + polite 1.5–3.5s delays + price/state helpers + `infer_firm_type()`.
- `broker_codes.json` — source registry, `site_prefix = TAM`.
- `site_id_registry.json` — persistent TAM- id map. **Never renumber.**
- `listings.json` — the canonical dataset (130 listings, 5 brokers: Accounting
  Biz Brokers, Accounting Practice Sales, NAAB Consulting, Poe Group Advisors,
  Private Practice Transitions). Tracked on purpose; the daily Action regenerates
  and commits it back here.

## Auto-refresh pipeline (refresh -> live)

`.github/workflows/scrape-accounting.yml` runs **daily at 09:00 UTC** (staggered
off vet's 08:30; plus manual `workflow_dispatch`). This repo is **PUBLIC**, so
GitHub Actions minutes are unlimited/free.

The Action is **self-contained — it only ever writes to THIS repo:**

1. checkout -> install deps -> `python run_all.py` (scrape + normalize).
2. **Sanity guard:** if `listings.json` collapses below 20 listings, the job
   **fails and refuses to commit**, preserving the last-good dataset. The live
   sites never get wiped by a bad scrape.
3. commit `listings.json` + `output/*.csv` + `site_id_registry.json` back to this
   repo using the default `GITHUB_TOKEN` (`permissions: contents: write`). No PAT.

**Why no cross-repo push:** the two site repos are SEPARATE git repos. Instead of
this Action reaching into them, each **site pulls `listings.json` from this repo's
public raw URL at build time**:

```
https://raw.githubusercontent.com/DentalAI22/accounting-market-scrapers/main/listings.json
```

So the refresh-to-live path is:

```
daily Action scrapes  ->  commits listings.json to THIS repo
       ->  a site rebuild (`vercel --prod`, or a site-side prebuild fetch step)
           pulls the fresh raw listings.json  ->  republishes.
```

The public raw file is the single source of truth. No cross-repo push credentials
are required anywhere.

## Re-run locally

```bash
pip install -r requirements.txt
python run_all.py               # scrape all 5 sources + normalize -> listings.json
python run_all.py --only aps    # one source
python run_all.py --normalize   # re-normalize existing CSVs (no network)
```

## Constraints honored

- Read-only against public broker pages only; real browser UA; 1.5–3.5s delays.
- Blocked aggregators (BizBuySell / BizQuest / LoopNet / DealStream / Provide /
  PracticeOrbit) are **never** scraped. Login/gated portals (Accounting Practice
  Exchange) skipped.
- APS SOLD cards dropped (honest 22 active, not 147). Honest counts; deduped;
  0 emails/phones/PII in the final dataset.
