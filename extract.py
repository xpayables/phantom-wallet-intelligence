"""Extract Phantom wallet activity + balances from Dune into local DuckDB.

Calls the Dune API directly (requests) to pin the engine to Small - the free tier's only option, and
dune-client hard-codes medium/large. Credit-safe: hash-sampled queries, a PULL_BALANCES gate, and a
hard row cap on every fetch. Run:  python extract.py
"""
import os
import time
from datetime import datetime, timedelta

import duckdb
import pandas as pd
import requests

import config

API = "https://api.dune.com/api/v1"
HEADERS = {"X-Dune-API-Key": config.DUNE_API_KEY}
PAGE = 32000
MAX_ROWS = 500_000   # safety cap: abort a fetch bigger than this (protects credits ~ rows x 1.4/1000)
POLL_SECONDS = 8     # execution-status poll interval (s)


def _req(method, url, **kw):
    """HTTP with retry + backoff on 429 (free-tier rate limit) and transient 5xx."""
    for attempt in range(8):
        r = requests.request(method, url, headers=HEADERS, **kw)
        if r.status_code == 429 or r.status_code >= 500:
            wait = int(r.headers.get("Retry-After", 0)) or min(60, 2 ** attempt)
            print(f"  {r.status_code} - backing off {wait}s (attempt {attempt + 1})")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()  # retries exhausted -> raise the last error
    return r


def run_query(query_id, params):
    """Execute a saved query on Small; retry transient execution failures; return DataFrame."""
    body = {"performance": "small", "query_parameters": params}
    for attempt in range(4):  # retry the whole execution on FAILED/CANCELLED/EXPIRED
        execution_id = _req("POST", f"{API}/query/{query_id}/execute", json=body).json()["execution_id"]
        state = _req("GET", f"{API}/execution/{execution_id}/status").json()["state"]
        while state in ("QUERY_STATE_PENDING", "QUERY_STATE_EXECUTING"):
            time.sleep(POLL_SECONDS)
            state = _req("GET", f"{API}/execution/{execution_id}/status").json()["state"]
        if state == "QUERY_STATE_COMPLETED":
            break
        print(f"  execution ended in {state}; retrying ({attempt + 1}/4)")
        time.sleep(min(30, 5 * (attempt + 1)))
    else:
        raise RuntimeError(f"query {query_id} failed after 4 attempts (last state: {state})")

    rows, offset = [], 0
    while True:  # fetch results, paginated
        batch = _req("GET", f"{API}/execution/{execution_id}/results",
                     params={"limit": PAGE, "offset": offset}).json()["result"]["rows"]
        rows.extend(batch)
        if len(rows) > MAX_ROWS:
            raise RuntimeError(f"query {query_id} returned >{MAX_ROWS} rows; aborting to protect credits")
        if len(batch) < PAGE:
            break
        offset += PAGE
    return pd.DataFrame(rows)


os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)   # data/ may not exist on a fresh runner
con = duckdb.connect(config.DB_PATH)
con.execute("""
CREATE TABLE IF NOT EXISTS activity (
    wallet VARCHAR, activity_day TIMESTAMP, fee_swaps BIGINT, fee_usd DOUBLE
)""")

# Rolling-window store: git holds the committed activity Parquet between ephemeral runs (our "memory").
# Seed it, pull only the new chunks below (already-loaded ones are skipped), then re-write a fixed
# ~9-month window at the end -- so each run adds a new month and drops the oldest.
ACTIVITY_STORE = "snapshot/activity.parquet"
if os.path.exists(ACTIVITY_STORE):
    con.execute(f"INSERT INTO activity SELECT * FROM '{ACTIVITY_STORE}'")
    print(f"seeded {con.execute('SELECT count(*) FROM activity').fetchone()[0]:,} rows from {ACTIVITY_STORE}")


def already_loaded(start, end):
    return con.execute(
        "SELECT count(*) FROM activity WHERE activity_day >= ? AND activity_day < ?",
        [start, end],
    ).fetchone()[0] > 0


# 1. Transfers: loop CHUNK_DAYS-wide windows (resumable).
start = datetime.fromisoformat(config.WINDOW_START)
end = datetime.fromisoformat(config.WINDOW_END)
d = start
while d < end:
    chunk_end = min(d + timedelta(days=config.CHUNK_DAYS), end)
    if already_loaded(d, chunk_end):
        print(f"skip  {d.date()} -> {chunk_end.date()} (already loaded)")
        d = chunk_end
        continue
    print(f"pull  {d.date()} -> {chunk_end.date()}")
    df = run_query(config.TRANSFERS_QUERY_ID, {
        "start_date": d.strftime("%Y-%m-%d %H:%M:%S"),
        "end_date": chunk_end.strftime("%Y-%m-%d %H:%M:%S"),
    })
    if len(df):
        # Parse as UTC so the day is independent of the host timezone.
        df["activity_day"] = pd.to_datetime(df["activity_day"], utc=True, errors="coerce").dt.tz_localize(None)
        df["fee_swaps"] = pd.to_numeric(df["fee_swaps"], errors="coerce")
        df["fee_usd"] = pd.to_numeric(df["fee_usd"], errors="coerce")   # "<nil>" -> NaN -> NULL
        con.execute(
            "INSERT INTO activity (wallet, activity_day, fee_swaps, fee_usd) "
            "SELECT wallet, activity_day, fee_swaps, fee_usd FROM df"
        )
    d = chunk_end
    time.sleep(1)

# Roll the window + dedupe: keep only [WINDOW_START, WINDOW_END), one row per wallet-day, so the store
# stays a fixed ~9-month size (a new month in, the oldest out) and re-runs never double-count.
con.execute(f"""
    CREATE OR REPLACE TABLE activity AS
    SELECT wallet, activity_day, any_value(fee_swaps) AS fee_swaps, any_value(fee_usd) AS fee_usd
    FROM activity
    WHERE CAST(activity_day AS DATE) >= DATE '{config.WINDOW_START[:10]}'
      AND CAST(activity_day AS DATE) <  DATE '{config.WINDOW_END[:10]}'
    GROUP BY wallet, activity_day""")
os.makedirs("snapshot", exist_ok=True)
con.execute(f"COPY activity TO '{ACTIVITY_STORE}' (FORMAT PARQUET)")   # persist the rolling store to git
print(f"wrote {ACTIVITY_STORE}: {con.execute('SELECT count(*) FROM activity').fetchone()[0]:,} rows (rolling window)")

# 2. Balances at cutoff T (gated -- only when you turn it on for the full run).
if config.PULL_BALANCES:
    print("pull  balances at T")
    bal = run_query(config.BALANCES_QUERY_ID, {"balance_day": config.BALANCE_DAY})
    con.execute("DROP TABLE IF EXISTS balances_at_t")
    con.execute("CREATE TABLE balances_at_t (wallet VARCHAR, stablecoin_usd DOUBLE)")
    if len(bal):
        bal["stablecoin_usd"] = pd.to_numeric(bal["stablecoin_usd"], errors="coerce")
        con.execute("INSERT INTO balances_at_t SELECT wallet, stablecoin_usd FROM bal")
    print("balance rows: ", con.execute("SELECT count(*) FROM balances_at_t").fetchone()[0])
else:
    print("skip  balances at T (PULL_BALANCES = False)")

print("activity rows:", con.execute("SELECT count(*) FROM activity").fetchone()[0])
con.close()
print("done ->", config.DB_PATH)
