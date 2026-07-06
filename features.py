"""Build the per-wallet feature table + churn/value labels, split at cutoff T (config.CUTOFF_T):
feature window = activity < T, label window = activity >= T. `activity_day` is cast to DATE so the
split is tz-independent. Writes `features`. Run:  python features.py
"""
import duckdb

import config

T = config.CUTOFF_T           # date-only cutoff (UTC); features < T, labels >= T
BOT_MAX_DAILY_SWAPS = 100     # a wallet exceeding this in a single day is flagged as likely automated

con = duckdb.connect(config.DB_PATH)

con.execute(f"""
CREATE OR REPLACE TABLE features AS
WITH feat AS (                                   -- feature window: activity strictly before T
    SELECT wallet,
           count(*)                           AS active_days,
           sum(fee_swaps)                     AS total_swaps,
           COALESCE(sum(fee_usd), 0) / 0.0085 AS total_volume_usd,   -- volume ~ fee / 0.85%
           max(fee_swaps)                     AS max_daily_swaps,
           date_diff('day', max(CAST(activity_day AS DATE)), DATE '{T}') AS recency_days,
           date_diff('day', min(CAST(activity_day AS DATE)), DATE '{T}') AS tenure_days,
           -- trend: recent 30d [T-30, T) vs preceding 30d [T-60, T-30)
           sum(CASE WHEN CAST(activity_day AS DATE) >= DATE '{T}' - INTERVAL 30 DAY THEN fee_usd ELSE 0 END)   AS vol_30,
           sum(CASE WHEN CAST(activity_day AS DATE) <  DATE '{T}' - INTERVAL 30 DAY
                    AND CAST(activity_day AS DATE) >= DATE '{T}' - INTERVAL 60 DAY THEN fee_usd ELSE 0 END)    AS vol_30_60,
           sum(CASE WHEN CAST(activity_day AS DATE) >= DATE '{T}' - INTERVAL 30 DAY THEN fee_swaps ELSE 0 END) AS sw_30,
           sum(CASE WHEN CAST(activity_day AS DATE) <  DATE '{T}' - INTERVAL 30 DAY
                    AND CAST(activity_day AS DATE) >= DATE '{T}' - INTERVAL 60 DAY THEN fee_swaps ELSE 0 END)  AS sw_30_60,
           count(CASE WHEN CAST(activity_day AS DATE) >= DATE '{T}' - INTERVAL 30 DAY THEN 1 END)              AS active_days_last_30d
    FROM activity
    WHERE CAST(activity_day AS DATE) < DATE '{T}'
    GROUP BY wallet
),
lab AS (                                         -- label window: activity on/after T
    SELECT wallet,
           sum(fee_swaps) AS label_swaps,
           COALESCE(sum(fee_usd), 0) / 0.0085 AS label_volume_usd
    FROM activity
    WHERE CAST(activity_day AS DATE) >= DATE '{T}'
    GROUP BY wallet
)
SELECT f.wallet,
       f.active_days,
       f.total_swaps,
       f.total_volume_usd,
       f.recency_days,
       f.tenure_days,
       f.max_daily_swaps,
       CAST(f.total_swaps AS DOUBLE) / f.active_days             AS avg_swaps_per_active_day,
       f.active_days_last_30d,
       CAST(f.active_days AS DOUBLE) / (f.tenure_days + 1)       AS activity_density,     -- consistency, in (0,1)
       CASE WHEN (f.vol_30 + f.vol_30_60) > 0
            THEN f.vol_30 / (f.vol_30 + f.vol_30_60) ELSE 0.5 END               AS recent_volume_share,   -- >0.5 growing
       CASE WHEN (f.sw_30 + f.sw_30_60) > 0
            THEN CAST(f.sw_30 AS DOUBLE) / (f.sw_30 + f.sw_30_60) ELSE 0.5 END   AS recent_swap_share,
       COALESCE(b.stablecoin_usd, 0)                 AS idle_stablecoin_usd,   -- no row = holds none
       (f.max_daily_swaps > {BOT_MAX_DAILY_SWAPS})   AS is_likely_bot,
       (l.wallet IS NULL)                            AS churn_label,           -- no label-window activity = churned
       COALESCE(l.label_swaps, 0)                    AS value_label_swaps,
       COALESCE(l.label_volume_usd, 0)               AS value_label_volume_usd
FROM feat f
LEFT JOIN balances_at_t b ON f.wallet = b.wallet
LEFT JOIN lab l           ON f.wallet = l.wallet
""")


def scalar(sql):
    return con.execute(sql).fetchone()[0]


# ---- data-quality assertions (fail loudly rather than train on a degenerate table) ----
n = scalar("SELECT count(*) FROM features")
dups = scalar("SELECT count(*) - count(DISTINCT wallet) FROM features")
churn = scalar("SELECT avg(CAST(churn_label AS DOUBLE)) FROM features WHERE NOT is_likely_bot")
bad_density = scalar("SELECT count(*) FROM features WHERE activity_density > 1.0001 OR activity_density < 0")
neg_idle = scalar("SELECT count(*) FROM features WHERE idle_stablecoin_usd < 0")

assert n > 1000, f"cohort too small ({n}) -- extraction likely incomplete"
assert dups == 0, f"{dups} duplicate wallets in features"
assert 0.01 < churn < 0.99, f"churn rate {churn:.1%} outside sane range -- check the T split"
assert bad_density == 0, f"{bad_density} rows with activity_density outside [0,1]"
assert neg_idle == 0, f"{neg_idle} rows with negative idle balance"
print(f"DQ checks passed: {n:,} rows, 0 dups, churn {churn:.1%}, density in range, idle >= 0\n")

# ---- summary (humans = non-bot cohort) ----
bots = scalar("SELECT count(*) FROM features WHERE is_likely_bot")
print(f"features rows (cohort):        {n:,}")
print(f"flagged likely-bot:            {bots:,}  ({bots/n:.1%})")
print("--- humans (is_likely_bot = FALSE) ---")
print(f"churn rate:                    {churn:.1%}")
print(f"hold idle stablecoins:         {scalar('SELECT avg(CAST(idle_stablecoin_usd > 0 AS DOUBLE)) FROM features WHERE NOT is_likely_bot'):.1%}")
print(f"median idle stablecoin (USD):  {scalar('SELECT median(idle_stablecoin_usd) FROM features WHERE NOT is_likely_bot AND idle_stablecoin_usd > 0'):,.0f}")
print(f"growing (recent_volume_share>0.5): {scalar('SELECT avg(CAST(recent_volume_share > 0.5 AS DOUBLE)) FROM features WHERE NOT is_likely_bot'):.1%}")
con.close()
print("done -> `features` table in", config.DB_PATH)
