"""
app.py
------
Streamlit dashboard for the Diamond AI Pricing Engine.

Tabs:
  1. Upload & Process   — Upload Base Report xlsx files and run the pipeline.
  2. AI Pricing         — View current inventory with colour coding, run AI pricing,
                          approve decisions, and export.
  3. ML Sell Scores     — View ML sell-probability scores as a heatmap table.
  4. Order Tracker      — DANY ORDER LIST with traffic-light status and charts.
  5. Activity Log       — Full audit trail with CSV export.
"""

import io
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

import pandas as pd
from pandas.io.formats.style import Styler
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ------------------------------------------------------------------ #
# Path resolution: allow running from any working directory            #
# ------------------------------------------------------------------ #
_REPO_ROOT = Path(__file__).resolve().parents[2]  # diamond_engine/
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.pipeline.loader import load_base_report
from src.pipeline.normalizer import run_full_normalisation
from src.pipeline.db_writer import (
    init_db,
    upsert_base_report,
    load_latest_base_report,
    load_activity_log,
    upsert_pricing_results,
    log_activity,
)
from src.orders.order_tracker import (
    load_orders,
    get_order_summary,
    get_at_risk_lines,
    get_stones_by_department,
    get_overdue_stones,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ------------------------------------------------------------------ #
# Configuration                                                        #
# ------------------------------------------------------------------ #
DB_PATH = _REPO_ROOT / "db" / "diamond.db"
DATA_RAW = _REPO_ROOT / "data" / "raw"
MODEL_PATH = _REPO_ROOT / "models" / "sell_model.pkl"
FEATURE_IMPORTANCE_IMG = _REPO_ROOT / "data" / "processed" / "feature_importance.png"

st.set_page_config(
    page_title="Diamond AI Pricing Engine",
    page_icon="💎",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ------------------------------------------------------------------ #
# Helpers                                                             #
# ------------------------------------------------------------------ #

def _get_api_key() -> str:
    """Return the Anthropic API key from env or st.secrets."""
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        try:
            key = st.secrets.get("ANTHROPIC_API_KEY", "")
        except Exception:
            pass
    return key


def _color_inv_remark(val: str) -> str:
    """Return CSS background colour for inv_remark cell."""
    if isinstance(val, str):
        if "reduction" in val.lower():
            return "background-color: #ffcccc"  # red
        if "raised" in val.lower():
            return "background-color: #ccffcc"  # green
    return ""


def _style_base_report(df: pd.DataFrame) -> Styler:
    """Apply conditional formatting to the base report DataFrame for display."""
    def row_style(row):
        styles = [""] * len(row)
        inv_remark = str(row.get("inv_remark", "")).lower()
        is_program = row.get("is_program", False)

        if is_program:
            color = "background-color: #fffacd"  # yellow
        elif "reduction" in inv_remark:
            color = "background-color: #ffcccc"  # red
        elif "raised" in inv_remark:
            color = "background-color: #ccffcc"  # green
        else:
            color = ""

        return [color] * len(row)

    return df.style.apply(row_style, axis=1)


def _style_pricing_results(df: pd.DataFrame) -> Styler:
    """Apply colour coding to the pricing results table."""
    action_colors = {
        "INCREASE_DISC": "#ffe0b3",   # orange
        "DECREASE_DISC": "#c8e6c9",   # green
        "KEEP": "#bbdefb",            # blue
    }

    def row_style(row):
        action = str(row.get("action", ""))
        color = action_colors.get(action, "")
        return [f"background-color: {color}" if color else ""] * len(row)

    return df.style.apply(row_style, axis=1)


def _score_color(score: float) -> str:
    """Return background colour for a sell score value."""
    if score >= 70:
        return "background-color: #c8e6c9"   # green
    if score >= 40:
        return "background-color: #fff9c4"   # yellow
    return "background-color: #ffcdd2"       # red


# ------------------------------------------------------------------ #
# Session State Initialisation                                         #
# ------------------------------------------------------------------ #
def _init_session_state():
    defaults = {
        "loaded_df": None,
        "pricing_results_df": None,
        "approved_keys": set(),
        "summary_df": None,
        "detail_df": None,
        "pipeline_log": [],
        "api_key_input": _get_api_key(),
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


# ------------------------------------------------------------------ #
# TAB 1 — Upload & Process                                             #
# ------------------------------------------------------------------ #
def tab_upload():
    """Render the Upload & Process tab."""
    st.header("Upload & Process Base Reports")

    uploaded_files = st.file_uploader(
        "Upload Base Report Excel files (.xlsx)",
        type=["xlsx"],
        accept_multiple_files=True,
        key="base_report_uploader",
    )

    if uploaded_files and st.button("Run Pipeline", key="run_pipeline_btn"):
        init_db(DB_PATH)
        all_logs = []

        for uploaded in uploaded_files:
            try:
                # Save to temp path
                tmp_path = DATA_RAW / uploaded.name
                tmp_path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path.write_bytes(uploaded.read())

                with st.spinner(f"Processing {uploaded.name}..."):
                    raw_df = load_base_report(tmp_path)
                    norm_df = run_full_normalisation(raw_df)
                    rows_written = upsert_base_report(norm_df, DB_PATH)

                all_logs.append(
                    {
                        "file": uploaded.name,
                        "rows_loaded": rows_written,
                        "report_date": norm_df["report_date"].iloc[0] if "report_date" in norm_df.columns else "N/A",
                        "status": "OK",
                    }
                )

                st.success(f"{uploaded.name}: {rows_written} rows loaded.")

                # Show column mapping
                with st.expander(f"Column mapping for {uploaded.name}"):
                    raw_cols = list(raw_df.columns)
                    norm_cols = [c for c in norm_df.columns if c in raw_cols or True]
                    mapping_data = {
                        "Raw Column": raw_cols[: len(raw_cols)],
                        "Mapped To": [norm_df.columns[i] if i < len(norm_df.columns) else "N/A"
                                      for i in range(len(raw_cols))],
                    }
                    st.dataframe(pd.DataFrame(mapping_data), use_container_width=True)

            except Exception as exc:
                st.error(f"Error processing {uploaded.name}: {exc}")
                all_logs.append({"file": uploaded.name, "rows_loaded": 0, "status": str(exc)})

        if all_logs:
            st.subheader("Pipeline Summary")
            st.dataframe(pd.DataFrame(all_logs), use_container_width=True)
            st.session_state["pipeline_log"].extend(all_logs)

    # Show last loaded report row counts
    try:
        df = load_latest_base_report(DB_PATH)
        if not df.empty:
            st.subheader("Latest Base Report in Database")
            st.metric("Total Rows", len(df))
            st.metric("Report Date", df["report_date"].iloc[0] if "report_date" in df.columns else "N/A")
            st.dataframe(df.head(20), use_container_width=True)
    except Exception:
        st.info("No base report data in database yet. Upload a file to get started.")


# ------------------------------------------------------------------ #
# TAB 2 — AI Pricing                                                   #
# ------------------------------------------------------------------ #
def tab_ai_pricing():
    """Render the AI Pricing tab."""
    st.header("AI Pricing Engine")

    # Load data
    try:
        base_df = load_latest_base_report(DB_PATH)
    except Exception:
        base_df = pd.DataFrame()

    if base_df.empty:
        st.warning("No base report data found. Please upload and process a Base Report first.")
        return

    # ---- Filters ----
    col1, col2, col3 = st.columns(3)
    with col1:
        shapes = ["All"] + sorted(base_df["shape"].dropna().unique().tolist()) if "shape" in base_df.columns else ["All"]
        selected_shape = st.selectbox("Filter by Shape", shapes, key="pricing_shape_filter")
    with col2:
        filter_program = st.toggle("Show Program rows only", value=False, key="filter_program")
    with col3:
        api_key = st.text_input("Anthropic API Key", value=st.session_state["api_key_input"], type="password", key="api_key_field")
        st.session_state["api_key_input"] = api_key

    display_df = base_df.copy()
    if selected_shape != "All" and "shape" in display_df.columns:
        display_df = display_df[display_df["shape"] == selected_shape]
    if filter_program and "is_program" in display_df.columns:
        display_df = display_df[display_df["is_program"].astype(bool)]

    # ---- Base Report Table with colour coding ----
    st.subheader(f"Current Inventory ({len(display_df)} rows)")
    display_cols = [c for c in ["criteria_key", "shape", "size_from", "size_to", "clarity", "color",
                                 "cut", "fluor", "current_disc", "last_week_disc", "avg_disc",
                                 "inv_days", "stock", "sold_1w", "sold_3m",
                                 "rapnet_pos_india", "inv_remark", "triggers", "is_program"]
                    if c in display_df.columns]

    try:
        styled = _style_base_report(display_df[display_cols])
        st.dataframe(styled, use_container_width=True, height=350)
    except Exception:
        st.dataframe(display_df[display_cols], use_container_width=True, height=350)

    st.caption("Red = Price Reduction | Green = Price Raised | Yellow = Program criteria")

    # ---- Run AI Pricing ----
    st.subheader("Run AI Pricing")

    col_run, col_concurrency = st.columns([3, 1])
    with col_concurrency:
        concurrency = st.number_input("Concurrency", min_value=1, max_value=10, value=3, key="concurrency_input")

    with col_run:
        if st.button("Run AI Pricing", key="run_ai_pricing_btn", type="primary"):
            if not api_key:
                st.error("Please enter your Anthropic API Key.")
            else:
                with st.spinner("Running AI Pricing... this may take several minutes."):
                    try:
                        from src.ai_brain.system_prompt import build_system_prompt
                        from src.ai_brain.batch_pricer import process_batch

                        system_prompt = build_system_prompt()
                        progress_placeholder = st.empty()
                        progress_placeholder.info("Processing rows...")

                        summary = process_batch(
                            df=base_df,
                            db_path=DB_PATH,
                            system_prompt=system_prompt,
                            api_key=api_key,
                            concurrency=concurrency,
                            batch_save_size=10,
                        )

                        progress_placeholder.empty()
                        st.success(
                            f"Done! Processed: {summary['processed']} | "
                            f"Increased: {summary['increased']} | "
                            f"Decreased: {summary['decreased']} | "
                            f"Kept: {summary['kept']} | "
                            f"Errors: {summary['errors']}"
                        )
                    except Exception as exc:
                        st.error(f"AI Pricing failed: {exc}")

    # ---- Display Results ----
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            results_df = pd.read_sql(
                "SELECT criteria_key, report_date, action, suggested_disc, change_pct, "
                "confidence, needs_review, primary_reason, approved FROM pricing_results "
                "ORDER BY created_at DESC",
                conn,
            )
    except Exception:
        results_df = pd.DataFrame()

    if results_df.empty:
        st.info("No pricing results yet. Click 'Run AI Pricing' to generate recommendations.")
        return

    # ---- Filters for results ----
    st.subheader("Pricing Results")
    col_f1, col_f2, col_f3, col_f4 = st.columns(4)
    with col_f1:
        action_filter = st.multiselect(
            "Action",
            ["INCREASE_DISC", "DECREASE_DISC", "KEEP"],
            default=["INCREASE_DISC", "DECREASE_DISC", "KEEP"],
            key="action_filter",
        )
    with col_f2:
        conf_filter = st.multiselect(
            "Confidence",
            ["HIGH", "MEDIUM", "LOW"],
            default=["HIGH", "MEDIUM", "LOW"],
            key="conf_filter",
        )
    with col_f3:
        needs_review_only = st.toggle("Needs Review Only", value=False, key="needs_review_filter")
    with col_f4:
        approved_filter = st.selectbox("Approved", ["All", "Approved", "Not Approved"], key="approved_filter")

    filtered_results = results_df.copy()
    if action_filter:
        filtered_results = filtered_results[filtered_results["action"].isin(action_filter)]
    if conf_filter:
        filtered_results = filtered_results[filtered_results["confidence"].isin(conf_filter)]
    if needs_review_only:
        filtered_results = filtered_results[filtered_results["needs_review"] == 1]
    if approved_filter == "Approved":
        filtered_results = filtered_results[filtered_results["approved"] == 1]
    elif approved_filter == "Not Approved":
        filtered_results = filtered_results[filtered_results["approved"] == 0]

    # Add selection checkboxes
    filtered_results = filtered_results.reset_index(drop=True)
    filtered_results.insert(0, "Select", False)

    try:
        edited_df = st.data_editor(
            _style_pricing_results(filtered_results.drop(columns=["Select"])),
            use_container_width=True,
            hide_index=True,
            key="results_editor",
        )
    except Exception:
        st.dataframe(filtered_results, use_container_width=True)
        edited_df = filtered_results

    # ---- Approval buttons ----
    col_a1, col_a2, col_a3 = st.columns(3)

    with col_a1:
        if st.button("Approve All HIGH Confidence", key="approve_high_btn"):
            high_conf_keys = results_df[
                (results_df["confidence"] == "HIGH") & (results_df["approved"] == 0)
            ]["criteria_key"].tolist()
            if high_conf_keys:
                with sqlite3.connect(str(DB_PATH)) as conn:
                    for k in high_conf_keys:
                        conn.execute(
                            "UPDATE pricing_results SET approved=1, approved_at=datetime('now') WHERE criteria_key=?",
                            (k,),
                        )
                    conn.commit()
                st.success(f"Approved {len(high_conf_keys)} HIGH confidence rows.")
                log_activity("APPROVE", f"Approved {len(high_conf_keys)} HIGH confidence rows", {"count": len(high_conf_keys)}, DB_PATH)
            else:
                st.info("No HIGH confidence unapproved rows found.")

    with col_a2:
        if st.button("Approve Selected", key="approve_selected_btn"):
            st.info("Use the 'Select' checkboxes in the table and click this button.")

    with col_a3:
        # Export approved results to Excel
        try:
            with sqlite3.connect(str(DB_PATH)) as conn:
                approved_df = pd.read_sql(
                    "SELECT pr.*, brr.shape, brr.size_from, brr.size_to, brr.clarity, brr.color, "
                    "brr.cut, brr.fluor, brr.current_disc as orig_disc "
                    "FROM pricing_results pr "
                    "LEFT JOIN base_report_rows brr ON pr.criteria_key = brr.criteria_key "
                    "WHERE pr.approved = 1",
                    conn,
                )
        except Exception:
            approved_df = pd.DataFrame()

        if not approved_df.empty:
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                approved_df.to_excel(writer, index=False, sheet_name="Approved Pricing")
            buffer.seek(0)
            st.download_button(
                label="Export Approved to Excel",
                data=buffer,
                file_name="approved_pricing.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="export_approved_btn",
            )
        else:
            st.button("Export Approved to Excel", disabled=True, key="export_approved_btn_disabled")


# ------------------------------------------------------------------ #
# TAB 3 — ML Sell Scores                                               #
# ------------------------------------------------------------------ #
def tab_ml_scores():
    """Render the ML Sell Scores tab."""
    st.header("ML Sell Probability Scores")

    # ---- Load predictions ----
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            ml_df = pd.read_sql(
                "SELECT criteria_key, report_date, sell_score, created_at FROM ml_predictions ORDER BY sell_score ASC",
                conn,
            )
    except Exception:
        ml_df = pd.DataFrame()

    # ---- Run scoring button ----
    col1, col2 = st.columns([3, 1])
    with col1:
        if st.button("Run ML Scoring on Latest Report", key="run_ml_btn"):
            try:
                from src.ml_model.model_store import load_model as load_ml_model
                from src.ml_model.predict import score_dataframe
                from src.pipeline.db_writer import upsert_ml_predictions

                model = load_ml_model(MODEL_PATH)
                base_df = load_latest_base_report(DB_PATH)
                if base_df.empty:
                    st.warning("No base report data found.")
                else:
                    with st.spinner("Scoring rows..."):
                        scored = score_dataframe(base_df, model)
                        report_date = scored["report_date"].iloc[0] if "report_date" in scored.columns else ""
                        preds_df = scored[["criteria_key", "sell_score"]].copy()
                        preds_df["report_date"] = report_date
                        preds_df["features_json"] = "{}"
                        upsert_ml_predictions(preds_df, DB_PATH)
                        st.success(f"Scored {len(scored)} rows.")

                        with sqlite3.connect(str(DB_PATH)) as conn:
                            ml_df = pd.read_sql(
                                "SELECT criteria_key, report_date, sell_score, created_at FROM ml_predictions ORDER BY sell_score ASC",
                                conn,
                            )
            except FileNotFoundError:
                st.warning("Trained model not found. Run `scripts/run_ml_train.py` first to train the model.")
            except Exception as exc:
                st.error(f"Scoring failed: {exc}")

    if ml_df.empty:
        st.info("No ML predictions yet. Run scoring or train the model first.")
    else:
        # ---- Score table with colour coding ----
        st.subheader(f"Sell Scores ({len(ml_df)} rows)")

        def _score_bg(val):
            try:
                v = float(val)
            except (TypeError, ValueError):
                return ""
            if v >= 70:
                return "background-color: #c8e6c9"
            if v >= 40:
                return "background-color: #fff9c4"
            return "background-color: #ffcdd2"

        styled_ml = ml_df.style.applymap(_score_bg, subset=["sell_score"])
        st.dataframe(styled_ml, use_container_width=True, height=400)

        # ---- Score distribution histogram ----
        st.subheader("Score Distribution")
        fig_hist = px.histogram(
            ml_df,
            x="sell_score",
            nbins=20,
            color_discrete_sequence=["steelblue"],
            title="Distribution of Sell Scores",
            labels={"sell_score": "Sell Score (0-100)"},
        )
        fig_hist.update_layout(bargap=0.1)
        st.plotly_chart(fig_hist, use_container_width=True)

    # ---- Feature importance chart ----
    st.subheader("Feature Importance")
    if FEATURE_IMPORTANCE_IMG.exists():
        st.image(str(FEATURE_IMPORTANCE_IMG), caption="XGBoost Feature Importance", use_column_width=True)
    else:
        st.info("Feature importance chart not available. Train the model to generate it.")


# ------------------------------------------------------------------ #
# TAB 4 — Order Tracker (DANY)                                         #
# ------------------------------------------------------------------ #
def tab_order_tracker():
    """Render the Order Tracker (DANY) tab."""
    st.header("DANY Order Tracker")

    order_file = st.file_uploader(
        "Upload DANY ORDER LIST.xlsx",
        type=["xlsx"],
        key="dany_order_uploader",
    )

    if order_file:
        try:
            tmp_path = DATA_RAW / order_file.name
            tmp_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_bytes(order_file.read())
            summary_df, detail_df = load_orders(tmp_path)
            st.session_state["summary_df"] = summary_df
            st.session_state["detail_df"] = detail_df
            st.success(f"Loaded {len(summary_df)} order lines and {len(detail_df)} stones.")
        except Exception as exc:
            st.error(f"Could not load order file: {exc}")

    summary_df = st.session_state.get("summary_df")
    detail_df = st.session_state.get("detail_df")

    if summary_df is None:
        st.info("Upload the DANY ORDER LIST.xlsx file above to begin.")
        return

    # ---- Order Summary Table ----
    st.subheader("Order Summary")
    summary_with_stats = get_order_summary(summary_df)

    # Traffic light column as emoji for table display
    def _traffic_light(flag):
        return {"green": "🟢", "amber": "🟡", "red": "🔴"}.get(flag, "⚪")

    display_summary = summary_with_stats.copy()
    display_summary["traffic"] = display_summary["status_flag"].map(_traffic_light)

    show_cols = [c for c in ["traffic", "order_name", "shape", "size_from", "size_to",
                              "color", "clarity", "qty_ordered", "in_process",
                              "polish_ready", "pending", "pct_complete", "deadline"]
                 if c in display_summary.columns]
    st.dataframe(display_summary[show_cols], use_container_width=True, height=300)

    # ---- Progress bars ----
    st.subheader("Order Completion Progress")
    for _, row in summary_with_stats.head(15).iterrows():
        label = f"{row.get('order_name', 'N/A')} | {row.get('shape', '')} {row.get('clarity', '')} {row.get('color', '')}"
        pct = float(row.get("pct_complete", 0)) / 100.0
        col_l, col_p = st.columns([2, 3])
        with col_l:
            st.write(label)
        with col_p:
            st.progress(min(pct, 1.0))

    # ---- At-risk order lines ----
    st.subheader("At-Risk Order Lines (deadline within 7 days)")
    at_risk = get_at_risk_lines(summary_with_stats, days_to_deadline=7)
    if at_risk.empty:
        st.success("No at-risk order lines. All orders are on track.")
    else:
        st.warning(f"{len(at_risk)} at-risk order lines found.")
        st.dataframe(at_risk, use_container_width=True)

    if detail_df is not None and not detail_df.empty:
        # ---- Stones by Department bar chart ----
        st.subheader("Stones by Department")
        dept_df = get_stones_by_department(detail_df)
        if not dept_df.empty:
            fig_dept = px.bar(
                dept_df,
                x="department",
                y="stone_count",
                color="avg_days_in_dept",
                color_continuous_scale="RdYlGn_r",
                title="Stone Count by Department (colour = avg days in dept)",
                labels={"stone_count": "Stone Count", "department": "Department"},
            )
            st.plotly_chart(fig_dept, use_container_width=True)

        # ---- Overdue stones ----
        st.subheader("Overdue Stones (>5 days in department)")
        overdue = get_overdue_stones(detail_df, threshold_days=5)
        if overdue.empty:
            st.success("No overdue stones found.")
        else:
            st.warning(f"{len(overdue)} overdue stones.")
            st.dataframe(overdue, use_container_width=True)


# ------------------------------------------------------------------ #
# TAB 5 — Activity Log                                                 #
# ------------------------------------------------------------------ #
def tab_activity_log():
    """Render the Activity Log tab."""
    st.header("Activity Log")

    try:
        log_df = load_activity_log(DB_PATH)
    except Exception:
        log_df = pd.DataFrame()

    if log_df.empty:
        st.info("No activity logged yet.")
        return

    # Filters
    event_types = ["All"] + sorted(log_df["event_type"].dropna().unique().tolist())
    selected_event = st.selectbox("Filter by Event Type", event_types, key="log_event_filter")

    display_log = log_df.copy()
    if selected_event != "All":
        display_log = display_log[display_log["event_type"] == selected_event]

    st.dataframe(
        display_log[["created_at", "event_type", "description", "metadata_json"]],
        use_container_width=True,
        height=500,
    )

    # Export CSV
    csv_buffer = io.StringIO()
    display_log.to_csv(csv_buffer, index=False)
    st.download_button(
        label="Export CSV",
        data=csv_buffer.getvalue(),
        file_name="activity_log.csv",
        mime="text/csv",
        key="export_log_btn",
    )


# ------------------------------------------------------------------ #
# Main                                                                 #
# ------------------------------------------------------------------ #
def main():
    """Entry point for the Streamlit dashboard."""
    _init_session_state()

    st.title("Diamond AI Pricing Engine")
    st.caption("Powered by Claude AI + XGBoost | Fancy & Asscher-Heart Base Reports")

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "Upload & Process",
        "AI Pricing",
        "ML Sell Scores",
        "Order Tracker",
        "Activity Log",
    ])

    with tab1:
        tab_upload()

    with tab2:
        tab_ai_pricing()

    with tab3:
        tab_ml_scores()

    with tab4:
        tab_order_tracker()

    with tab5:
        tab_activity_log()


if __name__ == "__main__":
    main()
