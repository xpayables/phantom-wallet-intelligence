"""Pipeline configuration. Secrets come from the environment.

Windows roll monthly. T is set LABEL_MONTHS back from the first of the current month, so its label
window is fully observed by run time; the feature window is the FEATURE_MONTHS before T. Pin a run with
PIPELINE_ASOF=YYYY-MM-DD for a reproducible local rebuild.
"""
import os
from datetime import date, datetime, timedelta, timezone

DUNE_API_KEY = os.environ.get("DUNE_API_KEY")   # .get() so non-extract scripts import without the key set
DB_PATH = "data/phantom.duckdb"

# Dune query ids (the number in dune.com/queries/<ID>).
TRANSFERS_QUERY_ID = 7887859
BALANCES_QUERY_ID = 7887855

# Phantom's Solana swap-fee wallets (DefiLlama fees/phantom.ts). Passed to the transfers query as a
# parameter (not hardcoded in SQL), so the roster lives here in one place.
FEE_WALLETS = [
    "25mYnjJ2MXHZH6NvTTdA63JvjgRVcuiaj6MRiEQNs1Dq",
    "9yj3zvLS3fDMqi1F8zhkaWfq8TZpZWHe6cz1Sgt7djXf",
    "8psNvWTrdNTiVRNzAgsou9kETXNJm2SXZyaKuJraVRtf",
    "CnmA6Zb8hLrG33AT4RTzKdGv1vKwRBKQQr8iNckvv8Yg",
    "2rQZb9xqQGwoCMDkpabbzDB9wyPTjSPj9WNhJodTaRHm",
    "9gnLg6NtVxaASvxtADLFKZ9s8yHft1jXb1Vu6gVKvh1J",
    "wtpXRqKLdGc7vpReogsRugv6EFCw4HBHcxm8pFcR84a",
    "D1NJy3Qq3RKBG29EDRj28ozbGwnhmM5yBUp8PonSYUnm",
]

CHUNK_DAYS = 3            # extraction chunk width (keeps each Small-engine query under the ~2-min limit)
PULL_BALANCES = True      # gate the one-day balance snapshot
FEATURE_MONTHS = 6        # feature-window length
LABEL_MONTHS = 3          # label-window length; also how far T lags "now" so labels are observed


def _add_months(d, months):
    total = d.year * 12 + (d.month - 1) + months
    y, m = divmod(total, 12)
    return date(y, m + 1, 1)


_asof = (date.fromisoformat(os.environ["PIPELINE_ASOF"]) if os.environ.get("PIPELINE_ASOF")
         else datetime.now(timezone.utc).date())
_end = date(_asof.year, _asof.month, 1)          # first of the current month; labels observed before this
_cutoff = _add_months(_end, -LABEL_MONTHS)
_start = _add_months(_cutoff, -FEATURE_MONTHS)

WINDOW_START = _start.strftime("%Y-%m-%d 00:00:00")
WINDOW_END = _end.strftime("%Y-%m-%d 00:00:00")
CUTOFF_T = _cutoff.strftime("%Y-%m-%d")          # date-only (UTC); features < T, labels >= T
BALANCE_DAY = (_cutoff - timedelta(days=1)).strftime("%Y-%m-%d 00:00:00")  # T-1: last feature day, so the idle-balance feature can't leak label-window activity
