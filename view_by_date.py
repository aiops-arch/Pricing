import streamlit as st
import sqlite3
import pandas as pd

DB = "E:/Pricing/diamond_engine/db/training.db"

st.set_page_config(page_title="Diamond Pricing Viewer", layout="wide")
st.title("Diamond Criteria Group Viewer")


@st.cache_data
def get_dates():
    conn = sqlite3.connect(DB)
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT report_date FROM position_stones ORDER BY report_date DESC"
    )]
    conn.close()
    return dates


@st.cache_data
def get_data(date):
    conn = sqlite3.connect(DB)

    stones = pd.read_sql(f"""
        SELECT
            color, clarity, fluor, psize,
            COUNT(*)                                                           AS total_stones,
            SUM(CASE WHEN stone_status = 'In Stock' THEN 1 ELSE 0 END)        AS in_stock,
            SUM(CASE WHEN stone_status LIKE 'Memo%' THEN 1 ELSE 0 END)        AS on_memo,
            SUM(CASE WHEN stone_status = 'Hold Customer' THEN 1 ELSE 0 END)   AS on_hold,
            -- Aging buckets
            SUM(CASE WHEN aging_days < 30  THEN 1 ELSE 0 END)                 AS age_fresh,
            SUM(CASE WHEN aging_days BETWEEN 30 AND 60 THEN 1 ELSE 0 END)     AS age_healthy,
            SUM(CASE WHEN aging_days > 60  THEN 1 ELSE 0 END)                 AS age_old,
            ROUND(AVG(aging_days), 1)                                          AS avg_age,
            MAX(aging_days)                                                    AS max_age,
            -- India breakdown
            SUM(CASE WHEN location LIKE '%Mumbai%' OR location LIKE '%India%' THEN 1 ELSE 0 END) AS india_count,
            SUM(CASE WHEN (location LIKE '%Mumbai%' OR location LIKE '%India%')
                     AND stone_status = 'In Stock' THEN 1 ELSE 0 END)         AS india_in_stock,
            -- Our pricing
            ROUND(AVG(base_pd_disc), 2)                                        AS our_base_pd,
            ROUND(AVG(rapnet_disc), 2)                                         AS our_rapnet,
            ROUND(AVG(limit_1), 2)                                             AS avg_limit1,
            -- Our market position (where WE rank vs competitors)
            ROUND(AVG(rapnet_pos_world), 1)                                    AS our_pos_world,
            ROUND(AVG(rapnet_pcs_pos_world), 1)                                AS our_pcs_world,
            ROUND(AVG(rapnet_pos_ind), 1)                                      AS our_pos_india,
            ROUND(AVG(rapnet_pcs_pos_ind), 1)                                  AS our_pcs_india,
            ROUND(AVG(rapnet_pos_usa), 1)                                      AS our_pos_usa,
            ROUND(AVG(rapnet_pcs_pos_usa), 1)                                  AS our_pcs_usa,
            -- Our base price vs India market (negative = we are cheaper than India 1st)
            ROUND(AVG(base_pd_disc_pos_ind), 2)                                AS base_vs_india,
            -- World top positions
            ROUND(AVG(comp_world_01), 2)     AS w1_disc,
            ROUND(AVG(comp_world_01_pcs), 0) AS w1_pcs,
            ROUND(AVG(comp_world_02), 2)     AS w2_disc,
            ROUND(AVG(comp_world_02_pcs), 0) AS w2_pcs,
            ROUND(AVG(comp_world_03), 2)     AS w3_disc,
            ROUND(AVG(comp_world_03_pcs), 0) AS w3_pcs,
            ROUND(AVG(comp_world_04), 2)     AS w4_disc,
            ROUND(AVG(comp_world_04_pcs), 0) AS w4_pcs,
            ROUND(AVG(comp_world_05), 2)     AS w5_disc,
            ROUND(AVG(comp_world_05_pcs), 0) AS w5_pcs,
            ROUND(AVG(comp_world_06), 2)     AS w6_disc,
            ROUND(AVG(comp_world_06_pcs), 0) AS w6_pcs,
            ROUND(AVG(comp_world_07), 2)     AS w7_disc,
            ROUND(AVG(comp_world_07_pcs), 0) AS w7_pcs,
            ROUND(AVG(comp_world_08), 2)     AS w8_disc,
            ROUND(AVG(comp_world_08_pcs), 0) AS w8_pcs,
            ROUND(AVG(comp_world_09), 2)     AS w9_disc,
            ROUND(AVG(comp_world_09_pcs), 0) AS w9_pcs,
            ROUND(AVG(comp_world_10), 2)     AS w10_disc,
            ROUND(AVG(comp_world_10_pcs), 0) AS w10_pcs,
            ROUND(AVG(world_avg5), 2)        AS world_avg5,
            ROUND(AVG(world_avg10), 2)       AS world_avg10,
            ROUND(AVG(world_total_pcs), 0)   AS world_total_pcs,
            -- India top positions
            ROUND(AVG(comp_india_01), 2)     AS i1_disc,
            ROUND(AVG(comp_india_01_pcs), 0) AS i1_pcs,
            ROUND(AVG(comp_india_02), 2)     AS i2_disc,
            ROUND(AVG(comp_india_02_pcs), 0) AS i2_pcs,
            ROUND(AVG(comp_india_03), 2)     AS i3_disc,
            ROUND(AVG(comp_india_03_pcs), 0) AS i3_pcs,
            ROUND(AVG(comp_india_04), 2)     AS i4_disc,
            ROUND(AVG(comp_india_04_pcs), 0) AS i4_pcs,
            ROUND(AVG(comp_india_05), 2)     AS i5_disc,
            ROUND(AVG(comp_india_05_pcs), 0) AS i5_pcs,
            ROUND(AVG(comp_india_06), 2)     AS i6_disc,
            ROUND(AVG(comp_india_06_pcs), 0) AS i6_pcs,
            ROUND(AVG(comp_india_07), 2)     AS i7_disc,
            ROUND(AVG(comp_india_07_pcs), 0) AS i7_pcs,
            ROUND(AVG(comp_india_08), 2)     AS i8_disc,
            ROUND(AVG(comp_india_08_pcs), 0) AS i8_pcs,
            ROUND(AVG(comp_india_09), 2)     AS i9_disc,
            ROUND(AVG(comp_india_09_pcs), 0) AS i9_pcs,
            ROUND(AVG(comp_india_10), 2)     AS i10_disc,
            ROUND(AVG(comp_india_10_pcs), 0) AS i10_pcs,
            ROUND(AVG(india_avg5), 2)        AS india_avg5,
            ROUND(AVG(india_avg10), 2)       AS india_avg10,
            ROUND(AVG(india_total_pcs), 0)   AS india_total_pcs,
            -- USA top positions
            ROUND(AVG(comp_usa_01), 2)       AS u1_disc,
            ROUND(AVG(comp_usa_01_pcs), 0)   AS u1_pcs,
            ROUND(AVG(comp_usa_02), 2)       AS u2_disc,
            ROUND(AVG(comp_usa_02_pcs), 0)   AS u2_pcs,
            ROUND(AVG(comp_usa_03), 2)       AS u3_disc,
            ROUND(AVG(comp_usa_03_pcs), 0)   AS u3_pcs,
            ROUND(AVG(usa_avg5), 2)          AS usa_avg5,
            ROUND(AVG(usa_avg10), 2)         AS usa_avg10,
            ROUND(AVG(usa_total_pcs), 0)     AS usa_total_pcs
        FROM position_stones
        WHERE report_date = ?
        GROUP BY color, clarity, fluor, psize
        ORDER BY total_stones DESC
    """, conn, params=[date])

    # Base price from backup CSV closest to this date
    base = pd.read_sql("""
        SELECT color, clarity, fluor, from_size, disc_per
        FROM pricing_snapshots
        WHERE snapshot_date = (
            SELECT MAX(snapshot_date) FROM pricing_snapshots WHERE snapshot_date <= ?
        )
    """, conn, params=[date])

    conn.close()
    return stones, base


# --- Sidebar ---
dates = get_dates()
selected_date = st.sidebar.selectbox("Select Date", dates, index=0)

st.sidebar.markdown("---")
st.sidebar.subheader("Filters")
sel_psize  = st.sidebar.selectbox("Size Group", ["All"] + [
    str(p) for p in [0.3,0.35,0.4,0.45,0.5,0.54,0.6,0.7,0.75,
                     0.8,0.85,0.9,0.96,1.0,1.04,1.1,1.2,1.3,1.4,1.5,1.7,1.8,1.9,2.0]
])
sel_color  = st.sidebar.selectbox("Color",  ["All","D","E","F","G","H","I","J","K"])
sel_fluor  = st.sidebar.selectbox("Fluor",  ["All","NONE","FAINT","MEDIUM","STRONG","VERY STRONG","VERY SLIGHT"])
min_stones = st.sidebar.slider("Min stones", 1, 20, 1)

# --- Load ---
stones_df, base_df = get_data(selected_date)

# Filters
if sel_psize != "All": stones_df = stones_df[stones_df["psize"] == float(sel_psize)]
if sel_color != "All": stones_df = stones_df[stones_df["color"] == sel_color]
if sel_fluor != "All": stones_df = stones_df[stones_df["fluor"] == sel_fluor]
stones_df = stones_df[stones_df["total_stones"] >= min_stones]

# Merge base fix price
base_lookup = base_df.set_index(["color","clarity","fluor","from_size"])["disc_per"].to_dict()
stones_df["base_fix_disc"] = stones_df.apply(
    lambda r: base_lookup.get((r["color"], r["clarity"], r["fluor"], r["psize"])), axis=1
)

# How far are we from 1st position
stones_df["gap_world_1st"] = (stones_df["our_rapnet"] - stones_df["w1_disc"]).round(2)
stones_df["gap_india_1st"] = (stones_df["our_rapnet"] - stones_df["i1_disc"]).round(2)

# --- Summary ---
st.subheader(f"Date: {selected_date}")
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Groups",          len(stones_df))
c2.metric("Total Stones",    int(stones_df["total_stones"].sum()))
c3.metric("In Stock",        int(stones_df["in_stock"].sum()))
c4.metric("Age > 60 days",   int(stones_df["age_old"].sum()))
c5.metric("On Memo",         int(stones_df["on_memo"].sum()))
c6.metric("India In Stock",  int(stones_df["india_in_stock"].sum()))

st.markdown("---")

# --- Aging alert ---
old = stones_df[stones_df["age_old"] > 0].sort_values("age_old", ascending=False)
if not old.empty:
    st.subheader(f"Needs attention — stones older than 60 days ({len(old)} groups)")
    attn_cols = ["color","clarity","fluor","psize",
                 "total_stones","in_stock",
                 "age_fresh","age_healthy","age_old","avg_age","max_age",
                 "our_base_pd","base_fix_disc","our_rapnet",
                 "our_pos_world","our_pos_india",
                 "w1_disc","i1_disc","gap_india_1st"]
    st.dataframe(old[[c for c in attn_cols if c in old.columns]],
                 use_container_width=True, hide_index=True)

st.markdown("---")

# --- Full table ---
st.subheader("All Criteria Groups — Full View")

tab1, tab2, tab3, tab4 = st.tabs(["Inventory & Aging", "Market Summary", "Pricing", "Market Depth"])

with tab1:
    inv_cols = ["color","clarity","fluor","psize",
                "total_stones","in_stock","on_memo","on_hold",
                "age_fresh","age_healthy","age_old","avg_age","max_age",
                "india_count","india_in_stock"]
    df_inv = stones_df[[c for c in inv_cols if c in stones_df.columns]].copy()

    def color_age(val):
        try:
            v = float(val)
            if v > 60:  return "background-color: #ffcccc"
            if v >= 30: return "background-color: #fff3cd"
            return "background-color: #d4edda"
        except: return ""

    st.dataframe(
        df_inv.style.map(color_age, subset=["avg_age"]),
        use_container_width=True, hide_index=True, height=600
    )

with tab2:
    st.caption(
        "our_pos_world/india/usa = our rank in market (1 = cheapest) | "
        "our_pcs = stones at our price level | "
        "base_vs_india = our base disc minus India 1st (negative = we are cheaper) | "
        "gap = our rapnet minus competitor 1st (negative = cheaper, can raise)"
    )
    mkt_cols = [
        "color","clarity","fluor","psize","total_stones",
        "our_rapnet",
        "our_pos_world","our_pcs_world",
        "our_pos_india","our_pcs_india",
        "our_pos_usa",
        "base_vs_india",
        "gap_world_1st","gap_india_1st",
        "w1_disc","w1_pcs","world_avg5","world_avg10","world_total_pcs",
        "i1_disc","i1_pcs","india_avg5","india_avg10","india_total_pcs",
        "u1_disc","u1_pcs","usa_total_pcs",
    ]
    df_mkt = stones_df[[c for c in mkt_cols if c in stones_df.columns]].copy()

    def color_pos(val):
        try:
            v = float(val)
            if v <= 3:   return "background-color: #d4edda"
            if v <= 10:  return "background-color: #fff3cd"
            return "background-color: #ffcccc"
        except: return ""

    def color_gap(val):
        try:
            v = float(val)
            if v < -5:  return "background-color: #d4edda"
            if v > 5:   return "background-color: #ffcccc"
            return "background-color: #fff3cd"
        except: return ""

    pos_cols = [c for c in ["our_pos_world","our_pos_india","our_pos_usa"] if c in df_mkt.columns]
    gap_cols = [c for c in ["gap_world_1st","gap_india_1st","base_vs_india"] if c in df_mkt.columns]
    styled = df_mkt.style
    if pos_cols:
        styled = styled.map(color_pos, subset=pos_cols)
    if gap_cols:
        styled = styled.map(color_gap, subset=gap_cols)

    st.dataframe(styled, use_container_width=True, hide_index=True, height=600)

with tab3:
    price_cols = ["color","clarity","fluor","psize",
                  "total_stones","in_stock",
                  "our_base_pd","base_fix_disc","our_rapnet","avg_limit1",
                  "base_vs_india","gap_world_1st","gap_india_1st"]
    df_price = stones_df[[c for c in price_cols if c in stones_df.columns]].copy()

    def color_gap2(val):
        try:
            v = float(val)
            if v < -5:  return "background-color: #d4edda"
            if v > 5:   return "background-color: #ffcccc"
            return "background-color: #fff3cd"
        except: return ""

    gap_cols2 = [c for c in ["gap_world_1st","gap_india_1st","base_vs_india"] if c in df_price.columns]
    styled2 = df_price.style
    if gap_cols2:
        styled2 = styled2.map(color_gap2, subset=gap_cols2)
    st.dataframe(styled2, use_container_width=True, hide_index=True, height=600)
    st.caption("gap = our rapnet minus competitor 1st. Green = we are cheaper (can raise). Red = we are expensive (need to cut). base_vs_india = our base vs India 1st.")

with tab4:
    st.subheader("Market Depth — Positions 1 to 10")
    st.caption("Each row is a criteria group. Columns show competitor discount% and piece count at each price tier.")

    market_choice = st.radio("Market", ["World", "India", "USA"], horizontal=True)
    pfx = {"World": "w", "India": "i", "USA": "u"}[market_choice]

    depth_cols = ["color","clarity","fluor","psize","total_stones","in_stock"]
    if market_choice in ("World", "India"):
        limit = 10
    else:
        limit = 5

    for n in range(1, limit + 1):
        d_col = f"{pfx}{n}_disc"
        p_col = f"{pfx}{n}_pcs"
        if d_col in stones_df.columns:
            depth_cols.append(d_col)
        if p_col in stones_df.columns:
            depth_cols.append(p_col)

    avg5_col    = f"{market_choice.lower()}_avg5"
    avg10_col   = f"{market_choice.lower()}_avg10"
    total_col   = f"{market_choice.lower()}_total_pcs"
    for c in [avg5_col, avg10_col, total_col]:
        if c in stones_df.columns:
            depth_cols.append(c)

    # Also show our position
    pos_col = {"World": "our_pos_world", "India": "our_pos_india", "USA": "our_pos_usa"}[market_choice]
    pcs_col = {"World": "our_pcs_world", "India": "our_pcs_india", "USA": "our_pcs_usa"}[market_choice]
    for c in [pos_col, pcs_col, "our_rapnet"]:
        if c in stones_df.columns:
            depth_cols.insert(6, c)

    df_depth = stones_df[[c for c in depth_cols if c in stones_df.columns]].copy()
    st.dataframe(df_depth, use_container_width=True, hide_index=True, height=600)
