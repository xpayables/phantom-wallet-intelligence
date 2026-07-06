"""Copy `scored` + `features` into a small, committable snapshot (`snapshot/demo.duckdb`) that the
deployed app reads - the full pipeline DB (`data/`) is gitignored. Run:  python export_demo.py
"""
import os

import duckdb

import config

SNAPSHOT = "snapshot/demo.duckdb"
os.makedirs("snapshot", exist_ok=True)

con = duckdb.connect(SNAPSHOT)
con.execute(f"ATTACH '{config.DB_PATH}' AS src (READ_ONLY)")
for t in ("scored", "features"):
    try:
        con.execute(f"SELECT 1 FROM src.{t} LIMIT 1")
    except Exception:
        raise SystemExit(f"'{t}' not found in {config.DB_PATH} -- run features.py then model.py first.")
con.execute("CREATE OR REPLACE TABLE scored   AS SELECT * FROM src.scored")
con.execute("CREATE OR REPLACE TABLE features AS SELECT * FROM src.features")
n_s = con.execute("SELECT count(*) FROM scored").fetchone()[0]
n_f = con.execute("SELECT count(*) FROM features").fetchone()[0]
con.execute("DETACH src")
con.close()

print(f"wrote {SNAPSHOT}: scored={n_s:,} rows, features={n_f:,} rows")
