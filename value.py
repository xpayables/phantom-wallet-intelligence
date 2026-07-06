"""Projected-value report -- the growth-PM "so what": what the model recommends and what it's worth.

Reads `scored` + `features`, aggregates the target segments, and applies the economic model in
valuation.py across conservative/base/upside assumptions. All figures scaled to the full base (x60)
and PROJECTED (a holdout test is required to prove causal lift). Run:  python value.py
"""
import os

import duckdb

import config
import valuation as v

if os.path.exists("snapshot/scored.parquet"):            # same source the app + README figures use
    df = duckdb.sql("""
        SELECT s.recommended_action, s.idle_stablecoin_usd, s.predicted_value,
               s.is_whale, f.value_label_volume_usd AS future_volume
        FROM 'snapshot/scored.parquet' s JOIN 'snapshot/features.parquet' f USING (wallet)
    """).df()
else:
    con = duckdb.connect(config.DB_PATH, read_only=True)
    df = con.execute("""
        SELECT s.recommended_action, s.idle_stablecoin_usd, s.predicted_value,
               s.is_whale, f.value_label_volume_usd AS future_volume
        FROM scored s JOIN features f USING (wallet)
    """).df()
    con.close()

card = df[df.recommended_action.str.startswith("Card")]
winback = df[df.recommended_action.str.startswith("Win-back")]
consumer = df[~df.is_whale]

idle_sample = card.idle_stablecoin_usd.sum()
at_risk_fee_sample = (winback.predicted_value * v.SWAP_FEE).sum()
consumer_fee_sample = (consumer.future_volume * v.SWAP_FEE).sum()
n = v.SAMPLE_FACTOR


def money(x):
    return f"${x:,.0f}"


print("=== RECOMMENDATION + PROJECTED VALUE (full base, x60; PROJECTED -- needs holdout test) ===\n")

print("target sizes (full base):")
print(f"  Card/CASH candidates : {len(card) * n:>9,}   (idle-balance floor {money(idle_sample * n)})")
print(f"  Win-back at-risk     : {len(winback) * n:>9,}")
print(f"  Consumer base (ex-whale): {len(consumer) * n:>6,}\n")

print(f"1) PRIORITIZATION -- target the top decile with the model vs. a past-volume sort")
prio_q = v.prioritization_value(consumer_fee_sample)
print(f"   consumer fee revenue / quarter: {money(consumer_fee_sample * n)}  (annualized {money(v.annualize(consumer_fee_sample * n))})")
print(f"   extra reachable at top 10% (lift {v.LIFT_TOP_DECILE:.0%}): {money(prio_q)}/qtr = {money(v.annualize(prio_q))}/yr\n")

print(f"2) CASH CROSS-SELL -- convert idle balances to CASH ({v.SWAP_FEE:.2%} fee once + {v.FLOAT_RATE_YR:.2%}/yr float)")
print(f"   {'conversion':<14}{'converted':>14}{'one-time fee':>15}{'float /yr':>13}")
for name, conv in v.CONVERSION.items():
    cv = v.cash_value(idle_sample, conv)
    tag = " (base)" if name == "base" else ""
    print(f"   {name + tag:<14}{money(cv['converted_usd']):>14}{money(cv['one_time_fee']):>15}{money(cv['annual_float']):>13}")

print(f"\n3) RETENTION -- save fee revenue in flagged at-risk high-value wallets")
print(f"   value-at-risk / quarter: {money(at_risk_fee_sample * n)}  (annualized {money(v.annualize(at_risk_fee_sample * n))})")
for name, sr in v.SAVE_RATE.items():
    rv = v.retention_value(at_risk_fee_sample, sr)
    tag = " (base)" if name == "base" else ""
    print(f"   save-rate {name + tag:<10} ({sr:.0%}): {money(v.annualize(rv['saved']))}/yr saved")

# base-case summary -- keep NET-NEW revenue separate from targeting EFFICIENCY (not additive)
cv_b = v.cash_value(idle_sample, v.CONVERSION["base"])
rv_b = v.retention_value(at_risk_fee_sample, v.SAVE_RATE["base"])
cash_new = cv_b["one_time_fee"] + cv_b["annual_float"]
retn_new = v.annualize(rv_b["saved"])
print(f"\nBASE-CASE (conversion {v.CONVERSION['base']:.0%}, save-rate {v.SAVE_RATE['base']:.0%}, float {v.FLOAT_RATE_YR:.2%}/yr, fee {v.SWAP_FEE:.2%}, lift {v.LIFT_TOP_DECILE:.0%}):")
print(f"  NET-NEW revenue/yr  = CASH {money(cash_new)} + retention saved {money(retn_new)} = {money(cash_new + retn_new)}")
print(f"  TARGETING EFFICIENCY = {money(v.annualize(prio_q))}/yr of existing value reachable in the top 10% vs a")
print(f"                         volume-sort (a multiplier on campaign spend, NOT additive net-new revenue)")
print(f"  NOTE: CASH is a CONSERVATIVE FLOOR (idle stablecoin only; portfolio-wide addressable is larger -- v2).")
print("  All assumptions tunable; PROJECTED -- causal lift requires a treatment/holdout test.")
