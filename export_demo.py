"""Copy `scored` + `features` into small, committable Parquet snapshots that the deployed app reads.
Parquet is an open, version-independent format, so the app never breaks on a DuckDB storage-version
mismatch. The full pipeline DB (`data/`) is gitignored. Run:  python export_demo.py
"""
import duckdb

import config

con = duckdb.connect(config.DB_PATH, read_only=True)
for t in ("scored", "features"):
    try:
        con.execute(f"SELECT 1 FROM {t} LIMIT 1")
    except Exception:
        raise SystemExit(f"'{t}' not found in {config.DB_PATH} -- run features.py then model.py first.")

# consistency gate: the only unscored feature wallets may be the flagged bots (excluded from scoring).
gap = con.execute("""
    SELECT count(*) FROM features
    WHERE wallet NOT IN (SELECT wallet FROM scored) AND NOT is_likely_bot
""").fetchone()[0]
if gap:
    raise SystemExit(f"{gap} non-bot feature wallets are unscored -- scored/features disagree; rerun model.py")

con.execute("COPY scored   TO 'snapshot/scored.parquet'   (FORMAT PARQUET)")
con.execute("COPY features TO 'snapshot/features.parquet' (FORMAT PARQUET)")
n_s = con.execute("SELECT count(*) FROM scored").fetchone()[0]
n_f = con.execute("SELECT count(*) FROM features").fetchone()[0]
con.close()
print(f"wrote snapshot/scored.parquet ({n_s:,}) + features.parquet ({n_f:,})")
