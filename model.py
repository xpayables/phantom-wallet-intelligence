"""Segment, model retention + value, score every wallet, and evaluate. Writes `scored`.

priority = P(active) x E[value | active]; the value model trains on retained wallets only.
Run:  python model.py
"""
import duckdb
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.cluster import KMeans
from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
from sklearn.metrics import brier_score_loss, mean_absolute_error, r2_score, roc_auc_score, silhouette_score
from sklearn.model_selection import cross_val_score, train_test_split
from sklearn.preprocessing import StandardScaler

import config

# trend/consistency candidates (active_days_last_30d, activity_density, recent_*_share) are computed
# in features.py but excluded here: no cross-validated gain over recency.
FEATURES = ["recency_days", "tenure_days", "active_days", "total_swaps",
            "total_volume_usd", "avg_swaps_per_active_day", "idle_stablecoin_usd"]
MEANINGFUL_USD = 10    # non-dust stablecoin balance -> a genuine card/CASH prospect
WHALE_PCTL = 0.999     # feature-window volume at/above this = whale/institutional (handled separately)


def gbc():
    return GradientBoostingClassifier(random_state=42)


con = duckdb.connect(config.DB_PATH)
df = con.execute("SELECT * FROM features WHERE NOT is_likely_bot").df()
print(f"modeling on {len(df):,} wallets\n")

Xdf = df[FEATURES].fillna(0)
df["retained"] = (~df["churn_label"]).astype(int)
y_val = np.log1p(df["value_label_volume_usd"])
df["is_whale"] = df["total_volume_usd"] >= df["total_volume_usd"].quantile(WHALE_PCTL)

# segmentation: pick k by silhouette (subsampled, O(n^2)); name segments from medians
Xs = StandardScaler().fit_transform(np.log1p(Xdf.clip(lower=0)))
sils = {}
for k in (3, 4, 5, 6):
    labels_k = KMeans(n_clusters=k, random_state=42, n_init=10).fit_predict(Xs)
    sils[k] = silhouette_score(Xs, labels_k, sample_size=5000, random_state=42)
K = max(sils, key=sils.get)
df["segment"] = KMeans(n_clusters=K, random_state=42, n_init=10).fit_predict(Xs)

_prof = df.groupby("segment")[["total_volume_usd", "recency_days", "tenure_days"]].median()
seg_name = {}
seg_name[_prof["total_volume_usd"].idxmax()] = "Power / high-value"
_rem = [s for s in _prof.index if s not in seg_name]
seg_name[_prof.loc[_rem, "recency_days"].idxmax()] = "Dormant / at-risk"
_rem = [s for s in _prof.index if s not in seg_name]
seg_name[_prof.loc[_rem, "tenure_days"].idxmin()] = "Newcomers"
_c = 0
for s in _prof.index:
    if s not in seg_name:
        seg_name[s] = "Steady / core" if _c == 0 else f"Steady / core {_c}"
        _c += 1
df["segment_name"] = df["segment"].map(seg_name)

# held-out split for out-of-sample evaluation
tr, te = train_test_split(df.index, test_size=0.30, random_state=42, stratify=df["retained"])
tr_ret = df.loc[tr].query("retained == 1").index          # two-stage: value trained on RETAINED only
te_ret = df.loc[te].query("retained == 1").index

# churn: raw + calibrated (isotonic), evaluated out-of-sample
clf = gbc().fit(Xdf.loc[tr], df.loc[tr, "retained"])
clf_cal = CalibratedClassifierCV(gbc(), method="isotonic", cv=5).fit(Xdf.loc[tr], df.loc[tr, "retained"])
p_raw = clf.predict_proba(Xdf.loc[te])[:, 1]
p_cal = clf_cal.predict_proba(Xdf.loc[te])[:, 1]
auc = roc_auc_score(df.loc[te, "retained"], p_raw)
cv_auc = cross_val_score(gbc(), Xdf, df["retained"], cv=5, scoring="roc_auc")
brier_raw = brier_score_loss(df.loc[te, "retained"], p_raw)
brier_cal = brier_score_loss(df.loc[te, "retained"], p_cal)

# value (two-stage): fit on retained train; report log-space AND dollar-space accuracy
reg = GradientBoostingRegressor(random_state=42).fit(Xdf.loc[tr_ret], y_val.loc[tr_ret])
pred_log = reg.predict(Xdf.loc[te_ret])
pred_usd = np.clip(np.expm1(pred_log), 0, None)
actual_usd = df.loc[te_ret, "value_label_volume_usd"].values
r2_log = r2_score(y_val.loc[te_ret], pred_log)
r2_usd = r2_score(actual_usd, pred_usd)
mae_usd = mean_absolute_error(actual_usd, pred_usd)
med_ae = float(np.median(np.abs(actual_usd - pred_usd)))
notw = ~df.loc[te_ret, "is_whale"].values
r2_usd_exw = r2_score(actual_usd[notw], pred_usd[notw])

ev = pd.DataFrame({
    "priority": p_cal * np.expm1(reg.predict(Xdf.loc[te])),
    "past_volume": df.loc[te, "total_volume_usd"].values,
    "actual_future": df.loc[te, "value_label_volume_usd"].values,
    "is_whale": df.loc[te, "is_whale"].values,
})

def capture(sub, by, frac):
    if sub["actual_future"].sum() == 0:
        return 0.0
    top = sub.sort_values(by, ascending=False).head(max(1, int(frac * len(sub))))
    return top["actual_future"].sum() / sub["actual_future"].sum()

def boot_lift(sub, frac, B=1000, seed=42):
    """95% CI on the model-minus-baseline capture lift, by resampling the test set."""
    rng = np.random.default_rng(seed)
    arr, n = sub.reset_index(drop=True), len(sub)
    lifts = np.array([capture(s := arr.iloc[rng.integers(0, n, n)], "priority", frac)
                      - capture(s, "past_volume", frac) for _ in range(B)])
    return lifts.mean(), np.percentile(lifts, 2.5), np.percentile(lifts, 97.5)

def boot_auc(y, p, B=1000, seed=42):
    """95% CI on held-out AUC by resampling the test set."""
    rng = np.random.default_rng(seed)
    y, p, n = np.asarray(y), np.asarray(p), len(y)
    aucs = []
    for _ in range(B):
        i = rng.integers(0, n, n)
        if np.unique(y[i]).size == 2:
            aucs.append(roc_auc_score(y[i], p[i]))
    return np.percentile(aucs, 2.5), np.percentile(aucs, 97.5)

# --- final models on ALL data -> the scored product table (calibrated churn; two-stage value) ---
ret_idx = df.index[df["retained"] == 1]
clf_imp = gbc().fit(Xdf, df["retained"])                                    # for feature importances
clf_cal_full = CalibratedClassifierCV(gbc(), method="isotonic", cv=5).fit(Xdf, df["retained"])
reg_full = GradientBoostingRegressor(random_state=42).fit(Xdf.loc[ret_idx], y_val.loc[ret_idx])
df["retain_prob"] = clf_cal_full.predict_proba(Xdf)[:, 1]                   # calibrated -> safe for thresholds
df["predicted_value"] = np.clip(np.expm1(reg_full.predict(Xdf)), 0, None)   # E[value | active]
df["priority_score"] = df["retain_prob"] * df["predicted_value"]            # P(active) x E[value|active]
df["is_meaningful_holder"] = df["idle_stablecoin_usd"] >= MEANINGFUL_USD

hi_val = df["predicted_value"].quantile(0.75)

def action(r):
    if r.is_whale:
        return "High-volume outlier"
    if r.retain_prob >= 0.5 and r.is_meaningful_holder:
        return "Card/CASH cross-sell"
    if r.retain_prob >= 0.5 and r.predicted_value >= hi_val:
        return "Deepen (perps / features)"
    if r.retain_prob < 0.3 and r.predicted_value >= hi_val:
        return "Win-back (high-value at-risk)"
    return "Monitor"

df["recommended_action"] = df.apply(action, axis=1)

# NOTE: use a distinct relation name -- `FROM scored` would bind to the existing `scored` TABLE
# (name-shadowing), silently ignoring this DataFrame and persisting stale data.
scored_out = df[["wallet", "segment", "segment_name", "retain_prob", "predicted_value", "priority_score",
                 "idle_stablecoin_usd", "is_meaningful_holder", "is_whale", "total_volume_usd",
                 "total_swaps", "recency_days", "recommended_action"]]
con.execute("CREATE OR REPLACE TABLE scored AS SELECT * FROM scored_out")
con.close()

# ---- diagnostics ----
top_share = df["value_label_volume_usd"].max() / df["value_label_volume_usd"].sum()
print("=== MODEL DIAGNOSTICS ===")
print(f"churn AUC (held-out test):        {auc:.3f}")
print(f"churn AUC (5-fold CV):            {cv_auc.mean():.3f} +/- {cv_auc.std():.3f}")
_alo, _ahi = boot_auc(df.loc[te, "retained"].values, p_raw)
print(f"churn AUC 95% CI (bootstrap):     [{_alo:.3f}, {_ahi:.3f}]")
print(f"churn Brier raw -> calibrated:    {brier_raw:.4f} -> {brier_cal:.4f}  (lower = better)")
print(f"value R2 log-space (test):        {r2_log:.3f}   <- flattering: heavy tail compressed")
print(f"value R2 dollar-space (test):     {r2_usd:.3f}   (ex-whales: {r2_usd_exw:.3f})")
print(f"value MAE / median-AE ($, test):  {mae_usd:,.0f} / {med_ae:,.0f}")
print(f"whales flagged: {df.is_whale.sum()}  |  single largest wallet = {top_share:.0%} of future volume")
print(f"segmentation silhouette: {{{', '.join(f'k{k}:{v:.3f}' for k, v in sils.items())}}} -> chose k={K}")
print("segments: " + " | ".join(f"{name} (n={(df.segment == sid).sum():,})" for sid, name in sorted(seg_name.items())) + "\n")

print("churn feature importance:")
for f, w in sorted(zip(FEATURES, clf_imp.feature_importances_), key=lambda x: -x[1]):
    print(f"  {f:26s} {w:.3f}")
print("value feature importance:")
for f, w in sorted(zip(FEATURES, reg_full.feature_importances_), key=lambda x: -x[1]):
    print(f"  {f:26s} {w:.3f}")

print("\nVALUE-CAPTURE LIFT (out-of-sample) -- model priority vs. rank-by-past-volume baseline:")
for name, sub in [("all      ", ev), ("ex-whales", ev[~ev.is_whale])]:
    for frac in (0.05, 0.10, 0.20):
        m, b = capture(sub, "priority", frac), capture(sub, "past_volume", frac)
        print(f"  {name} top {frac:>3.0%}:  model {m:5.1%} | baseline {b:5.1%} | lift {m - b:+5.1%}")

print("\nLIFT SIGNIFICANCE (bootstrap 95% CI on model-minus-baseline lift):")
for name, sub in [("all      ", ev), ("ex-whales", ev[~ev.is_whale])]:
    for frac in (0.05, 0.10, 0.20):
        m, lo, hi = boot_lift(sub, frac)
        sig = "SIGNIFICANT" if lo > 0 else "n.s. (CI spans 0)"
        print(f"  {name} top {frac:>3.0%}:  lift {m:+5.1%}  95% CI [{lo:+5.1%}, {hi:+5.1%}]  {sig}")

print("\nstablecoin balance tiers (card target size):")
for lo in (0, 10, 100, 1000):
    print(f"  > ${lo:>5,}: {(df.idle_stablecoin_usd > lo).mean():5.1%}")

print("\nrecommended-action breakdown:")
print(df["recommended_action"].value_counts().to_string())
print("\ndone -> `scored` table in", config.DB_PATH)
