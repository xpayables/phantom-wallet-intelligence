"""Phantom Wallet Intelligence: cross-sell & retention targeting dashboard.
Reads `scored` + `features` (the snapshot if present, else the full DB). Run:  streamlit run app.py
"""
import os

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

import config
import valuation as vln

REPO_URL = "https://github.com/xpayables/phantom-wallet-intelligence"

st.set_page_config(page_title="Phantom Wallet Intelligence", layout="wide")

# Compact layout: clear the top toolbar, shrink the metric numbers so more fits per screen.
st.markdown("""
<style>
  .block-container, [data-testid="stMainBlockContainer"] { padding-top: 3rem !important; }
  [data-testid="stSidebarUserContent"],
  section[data-testid="stSidebar"] > div:first-child { padding-top: 0.25rem !important; }
  h3 { font-size:1.1rem !important; font-weight:400 !important; margin:0.3rem 0 0.15rem !important; }
  /* KPI cards: soft Phantom-purple color blocks, no border */
  [data-testid="stMetric"] { background:#f1eefc; border:none; border-radius:12px;
      padding:12px 16px; box-shadow:none; }
  [data-testid="stMetricLabel"] p { font-size:0.78rem !important; color:#6b6f86 !important; }
  [data-testid="stMetricValue"] { font-size:1.4rem !important; font-weight:700 !important; color:#6E56CF !important; }
  /* slider thumb: darker purple than the (lighter) filled track */
  [data-testid="stSlider"] [role="slider"] { background-color:#4c3b9c !important; }
</style>
""", unsafe_allow_html=True)


@st.cache_data
def load():
    db = "snapshot/demo.duckdb" if os.path.exists("snapshot/demo.duckdb") else config.DB_PATH
    con = duckdb.connect(db, read_only=True)
    df = con.execute("""
        SELECT s.*, f.value_label_volume_usd AS actual_future_volume
        FROM scored s JOIN features f USING (wallet)
    """).df()
    con.close()
    return df


df = load()

# ---- sidebar: title + filters ----
st.sidebar.markdown(
    "<div style='font-size:1.8rem;font-weight:800;line-height:1.05;margin:0 0 .3rem;color:#4a4a4a;'>"
    "Phantom<br>Wallet<br>Intelligence</div>", unsafe_allow_html=True)
st.sidebar.markdown(
    f'<a href="{REPO_URL}" target="_blank" style="text-decoration:none;font-weight:500;color:#6E56CF;">'
    '<svg height="15" width="15" viewBox="0 0 16 16" style="vertical-align:-2px;fill:currentColor;margin-right:6px;"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"></path></svg>Methodology &amp; code</a>',
    unsafe_allow_html=True)
st.sidebar.markdown(
    "<div style='font-size:0.72rem;color:#8a8d9b;margin-top:.4rem;'>Data refreshes monthly (1st, 06:00 UTC)</div>",
    unsafe_allow_html=True)
st.sidebar.divider()

st.sidebar.subheader("Filters")
acts = st.sidebar.multiselect("Recommended action", sorted(df.recommended_action.unique()), placeholder="All actions")
segs = st.sidebar.multiselect("Segment", sorted(df.segment_name.unique()), placeholder="All segments")
min_bal = st.sidebar.number_input("Min idle stablecoin (USDC+USDT, $)", 0, 100000, 0,
    help="Show only wallets holding at least this much idle stablecoin (USDC + USDT, valued in USD). Useful for sizing the card/CASH opportunity.")
seg_mask = df.segment_name.isin(segs) if segs else pd.Series(True, index=df.index)
act_mask = df.recommended_action.isin(acts) if acts else pd.Series(True, index=df.index)
view = df[seg_mask & act_mask & (df.idle_stablecoin_usd >= min_bal)]

with st.sidebar.expander("Value assumptions (projected)", expanded=False):
    st.caption("About these inputs", help="The projected $ rests on three things on-chain data can't measure: "
               "conversion, win-back save-rate, and CASH yield. Enter your own estimates to re-run the projection "
               "(e.g. \"if campaigns convert 6% and save 20% of at-risk, what's it worth?\"). Changes the dollar "
               "estimate only, not the recommended wallets.")
    conv = st.slider("CASH conversion rate", 0, 15, 4, 1, format="%d%%",
                     help="Share of card/CASH candidates who convert idle USDC to CASH.") / 100
    save = st.slider("Win-back save-rate", 0, 30, 15, 1, format="%d%%",
                     help="Share of at-risk wallets a win-back campaign successfully re-engages.") / 100
    floatr = st.slider("CASH float rate /yr", 0.0, 5.0, 3.25, 0.25, format="%.2f%%",
                       help="Annual yield Phantom nets on idle CASH (T-bill yield minus Bridge/Stripe fee).") / 100

# ---- Cohort row: label on the left, cards on the right ----
c0, c1, c2, c3, c4 = st.columns([1.4, 1, 1, 1, 1], vertical_alignment="center")
c0.markdown("<div style='font-size:1.1rem;line-height:1.2'>Cohort</div>", unsafe_allow_html=True)
c0.caption("1/60 sample", help="1/60 deterministic hash sample of wallets that paid Phantom swap fees on Solana, "
           "active in the feature window (Oct 1 2025 to Mar 31 2026). Multiply by about 60 for the full base.")
c1.metric("Wallets in view", f"{len(view):,}")
c2.metric("Card/CASH targets", f"{view.recommended_action.str.startswith('Card').sum():,}")
c3.metric("Meaningful holders (>$10)", f"{view.is_meaningful_holder.sum():,}")
c4.metric("Likely to retain (P>=0.5)", f"{(view.retain_prob >= 0.5).sum():,}")

# ---- Projected annual value row: label on the left, cards on the right ----
_card = df[df.recommended_action.str.startswith("Card")]
_wb = df[df.recommended_action.str.startswith("Win-back")]
_cons = df[~df.is_whale]
cv = vln.cash_value(_card.idle_stablecoin_usd.sum(), conv, float_rate=floatr)
retn_saved = vln.annualize(vln.retention_value((_wb.predicted_value * vln.SWAP_FEE).sum(), save)["saved"])
prio = vln.annualize(vln.prioritization_value((_cons.actual_future_volume * vln.SWAP_FEE).sum()))
cash_new = cv["one_time_fee"] + cv["annual_float"]
v0, v1, v2, v3, v4 = st.columns([1.4, 1, 1, 1, 1], vertical_alignment="center")
v0.markdown("<div style='font-size:1.1rem;line-height:1.2'>Projected annual value</div><span style='color:#667085;font-size:0.8rem;'>$/yr · full base ×60</span>", unsafe_allow_html=True)
v1.metric("Net-new revenue /yr", f"${cash_new + retn_saved:,.0f}")
v2.metric("CASH (fee + float)", f"${cash_new:,.0f}")
v3.metric("Retention saved", f"${retn_saved:,.0f}")
v4.metric("Targeting efficiency /yr", f"${prio:,.0f}",
          help="Value reached in the top decile vs. a plain past-volume sort. A multiplier on campaign spend, not additive net-new. Projected under the sidebar assumptions.")

# ---- Value concentration + Recommended actions, side by side ----
left, right = st.columns(2)
with left:
    st.subheader("Value concentration",
                 help="Target the top X% of wallets by priority (x-axis); the curve is the % of next-quarter volume they capture. "
                      "It rises fast then flattens, so a small group captures most of the value (README has the model's edge over a plain volume sort).")
    order = df.sort_values("priority_score", ascending=False).reset_index(drop=True)
    curve = pd.DataFrame({
        "pct_wallets": (order.index + 1) / len(order),
        "pct_value": order.actual_future_volume.cumsum() / order.actual_future_volume.sum(),
    })
    chart = alt.Chart(curve).mark_line(color="#AB9FF2", strokeWidth=3).encode(
        x=alt.X("pct_wallets", title="% of wallets targeted (by priority)", axis=alt.Axis(format="%")),
        y=alt.Y("pct_value", title="% of future volume captured", axis=alt.Axis(format="%")),
        tooltip=[alt.Tooltip("pct_wallets", title="% wallets targeted", format=".1%"),
                 alt.Tooltip("pct_value", title="% volume captured", format=".1%")],
    ).properties(height=300, width="container")
    st.altair_chart(chart)
with right:
    st.subheader("Recommended actions",
                 help="Share of wallets in each recommended-action bucket.")
    tm = view.recommended_action.value_counts().reset_index()
    tm.columns = ["action", "wallets"]
    tm["pct"] = tm.wallets / tm.wallets.sum()
    base = alt.Chart(tm).encode(
        x=alt.X("pct:Q", title="% of wallets", axis=alt.Axis(format="%")),
        y=alt.Y("action:N", sort="-x", title=None,
                axis=alt.Axis(labelLimit=200,
                              labelExpr="indexof(datum.label,'(') > 0 ? "
                                        "[substring(datum.label,0,indexof(datum.label,'(')), substring(datum.label,indexof(datum.label,'('))] "
                                        ": datum.label")),
    )
    bar = base.mark_bar(color="#AB9FF2").encode(
        tooltip=[alt.Tooltip("action:N", title="Action"),
                 alt.Tooltip("wallets:Q", title="Wallets", format=","),
                 alt.Tooltip("pct:Q", title="Share", format=".1%")])
    labels = base.mark_text(align="left", dx=3, color="#333").encode(text=alt.Text("pct:Q", format=".1%"))
    st.altair_chart((bar + labels).properties(height=300, width="container"))

# ---- Segment profiles ----
st.subheader("Segment profiles (descriptive)")
prof = view.groupby("segment_name").agg(
    wallets=("wallet", "size"),
    retain_prob=("retain_prob", "mean"),
    pred_value=("predicted_value", "median"),
    idle_usd=("idle_stablecoin_usd", "median"),
    swaps=("total_swaps", "median"),
    recency_days=("recency_days", "median"),
).round(2)
st.dataframe(prof, width="stretch")
st.caption("Descriptive only. Segments don't drive the priority score or actions (see README).")

# ---- WHO / WHY / HOW: the target list ----
st.subheader("Target list")
show = view.sort_values("priority_score", ascending=False).head(500).copy()
show["wallet"] = show["wallet"].str.slice(0, 4) + "…" + show["wallet"].str.slice(-4)   # truncate for a public demo
cols = ["wallet", "segment_name", "recommended_action", "priority_score", "retain_prob",
        "predicted_value", "idle_stablecoin_usd", "total_volume_usd", "total_swaps", "recency_days"]
st.dataframe(
    show[cols], width="stretch", hide_index=True, height=300,
    column_config={
        "wallet": st.column_config.TextColumn("Wallet", help="On-chain wallet address (truncated for the public demo)."),
        "segment_name": st.column_config.TextColumn("Segment", help="Behavioral cluster (descriptive; not a targeting input)."),
        "recommended_action": st.column_config.TextColumn("Recommended action", help="Suggested next step, from the retention/value models plus simple rules."),
        "priority_score": st.column_config.NumberColumn("Priority score", format="$%.0f",
            help="Ranking metric = retain prob × predicted value. A probability (0 to 1) times a dollar volume, so it reads in dollars: the wallet's expected next-quarter swap volume."),
        "retain_prob": st.column_config.NumberColumn("Retain prob", format="%.2f",
            help="Model 1 output: probability the wallet stays active next quarter (0 to 1)."),
        "predicted_value": st.column_config.NumberColumn("Predicted value", format="$%.0f",
            help="Model 2 output: expected next-quarter swap volume in dollars if the wallet stays. A volume forecast for the wallet, not Phantom revenue."),
        "idle_stablecoin_usd": st.column_config.NumberColumn("Idle stablecoin", format="$%.2f",
            help="USDC + USDT held idle at the cutoff ($). The CASH/card signal."),
        "total_volume_usd": st.column_config.NumberColumn("Total volume", format="$%.0f",
            help="Swap volume in the 6-month feature window ($)."),
        "total_swaps": st.column_config.NumberColumn("Swaps", format="%d", help="Swap count in the feature window."),
        "recency_days": st.column_config.NumberColumn("Recency (days)", format="%d",
            help="Days since the wallet's last activity before the cutoff."),
    })
st.caption("Ranked by priority; top 500, addresses truncated. Counts are sample-level (×~60 for the full base). "
           "Card targeting is a hypothesis (ignores KYC/geo eligibility); see README.")
