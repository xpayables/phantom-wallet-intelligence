"""Economic value model: turn scored wallets into projected $. Every rate below is a stated
assumption or a doc-grounded constant, kept here and tunable. Pure functions, shared by app.py
and value.py. Outputs are projected, not proven - see the README's Limitations.
"""
SAMPLE_FACTOR = 60          # ~1/60 hash sample -> scale to the full Phantom Solana-swapper base
SWAP_FEE = 0.0085           # 0.85% Phantom fee -- observed, and it applies to CASH conversion too
FLOAT_RATE_YR = 0.0325      # net float on idle CASH: ~3.7% T-bill yield (Jul 2026) minus Bridge/Stripe
                            # issuance fee, no user pass-through. (5% was the stale 2023-24 rate regime.)
LABEL_MONTHS = 3            # label-window length -> annualize fee revenue with x(12/LABEL_MONTHS)
LIFT_TOP_DECILE = 0.07      # measured ex-whale top-10% lift, 95% CI [+2.9%, +13.8%] (model.py bootstrap)

CONVERSION = {"conservative": 0.02, "base": 0.04, "upside": 0.08}   # warm in-app cross-sell (cold mail ~2-4%)
SAVE_RATE = {"low": 0.10, "base": 0.15, "high": 0.25}               # win-back reactivation (industry ~10-30%)


def annualize(quarterly):
    return quarterly * (12 / LABEL_MONTHS)


def cash_value(idle_usd_sample, conversion, float_rate=FLOAT_RATE_YR, scale=SAMPLE_FACTOR):
    """One-time conversion fee + annual float on converted idle balances (a conservative floor)."""
    converted = idle_usd_sample * conversion * scale
    return {
        "converted_usd": converted,
        "one_time_fee": converted * SWAP_FEE,
        "annual_float": converted * float_rate,
    }


def retention_value(at_risk_fee_sample, save_rate, scale=SAMPLE_FACTOR):
    """Fee revenue saved from flagged at-risk high-value wallets (per label window)."""
    value_at_risk = at_risk_fee_sample * scale
    return {"value_at_risk": value_at_risk, "saved": value_at_risk * save_rate}


def prioritization_value(consumer_fee_sample, lift=LIFT_TOP_DECILE, scale=SAMPLE_FACTOR):
    """Extra fee revenue reachable in the top decile vs a past-volume sort (per label window)."""
    return consumer_fee_sample * scale * lift
