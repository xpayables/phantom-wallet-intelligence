"""Pipeline configuration. Secrets come from the environment."""
import os

DUNE_API_KEY = os.environ.get("DUNE_API_KEY")   # .get() so non-extract scripts import without the key set
DB_PATH = "data/phantom.duckdb"

# Dune query ids (the number in dune.com/queries/<ID>).
TRANSFERS_QUERY_ID = 7887859
BALANCES_QUERY_ID = 7887855

# Extraction window; chunked to stay under the Small-engine 2-minute limit.
WINDOW_START = "2025-10-01 00:00:00"
WINDOW_END = "2026-07-01 00:00:00"
CHUNK_DAYS = 3

CUTOFF_T = "2026-03-31"              # date-only (UTC); features < T, labels >= T
BALANCE_DAY = "2026-03-31 00:00:00"
PULL_BALANCES = True                 # gate the one-day balance snapshot (large fetch - off by default in dev)
