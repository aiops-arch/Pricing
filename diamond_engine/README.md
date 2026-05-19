# Diamond AI Pricing Engine

An AI-powered diamond pricing system that analyses weekly Base Reports, generates
discount recommendations using Claude AI, scores sell-probability with XGBoost,
and tracks DANY manufacturing orders — all from a single Streamlit dashboard.

---

## Project Overview

The engine ingests:
- **Fancy Base Report** and **Asscher-Heart Base Report** Excel files (weekly)
- Optionally a **DANY ORDER LIST** Excel file (two-sheet format)
- Optionally a company **pricing rulebook PDF**

It produces:
- AI pricing recommendations (INCREASE_DISC / DECREASE_DISC / KEEP) with confidence scores
- ML sell-probability scores (XGBoost, 0-100)
- Order tracker with traffic-light status and overdue stone alerts
- Full activity audit log in SQLite

---

## Prerequisites

- Python 3.11 or higher
- pip
- (Optional) Playwright system dependencies for the RapNet scraper

---

## Setup

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Install Playwright browsers (for RapNet scraper, optional)

```bash
playwright install chromium
```

### 3. Configure environment variables

```bash
# Windows
copy .env.example .env

# macOS / Linux
cp .env.example .env
```

Edit `.env` and fill in your keys:

```
ANTHROPIC_API_KEY=your_real_anthropic_api_key
RAPNET_USERNAME=your_rapnet_username
RAPNET_PASSWORD=your_rapnet_password
```

### 4. Create required directories

```bash
mkdir data\raw data\processed db models
```

### 5. Copy your Base Report files

Copy all weekly Base Report xlsx files (filenames must contain "BASE REPORT") into:

```
data\raw\
```

Example filenames:
- `FANCY BASE REPORT 2024-05-13.xlsx`
- `ASSCHER-HEART BASE REPORT 13-05-2024.xlsx`

---

## Running the Pipeline

### Load Base Reports into the database

```bash
python scripts/run_pipeline.py
```

This finds all `*BASE REPORT*.xlsx` files in `data/raw/`, runs the full
load → normalise → DB-write pipeline, and logs a summary.

### Run AI Pricing

```bash
python scripts/run_ai_pricing.py
```

Options:
```bash
# With a PDF rulebook
python scripts/run_ai_pricing.py --pdf path/to/rulebook.pdf

# With custom concurrency (default: 3 parallel API calls)
python scripts/run_ai_pricing.py --concurrency 5
```

### Train the ML Model

```bash
python scripts/run_ml_train.py
```

This reads all historical data from the database, creates a synthetic target
label (sell_within_7d), trains an XGBoost classifier, prints accuracy and a
classification report, saves the model to `models/sell_model.pkl`, and saves
a feature importance chart to `data/processed/feature_importance.png`.

---

## Launching the Dashboard

```bash
streamlit run src/dashboard/app.py
```

Open http://localhost:8501 in your browser.

### Dashboard Tabs

| Tab | Function |
|-----|----------|
| Upload & Process | Upload Base Report xlsx files, run pipeline, view column mapping |
| AI Pricing | View inventory with colour coding, run AI pricing, approve/export |
| ML Sell Scores | View sell-probability scores, feature importance chart |
| Order Tracker | Load DANY ORDER LIST, traffic-light status, overdue stones |
| Activity Log | Full audit trail, export CSV |

---

## Adding More Weekly Base Reports (for better ML training)

The more historical data you load, the better the ML model will perform:

1. Copy new weekly xlsx files to `data/raw/`
2. Run `python scripts/run_pipeline.py`
3. Retrain the model: `python scripts/run_ml_train.py`

The pipeline uses upsert (INSERT OR REPLACE) so re-running on existing files
is safe.

---

## Database Schema

SQLite database at `db/diamond.db`.

### `base_report_rows`
All normalised columns from loaded Base Reports. Upsert key: `(criteria_key, report_date)`.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Auto-increment primary key |
| criteria_key | TEXT | Shape\|SizeFrom\|SizeTo\|Clarity\|Color |
| report_date | TEXT | ISO date from filename |
| source_file | TEXT | Original filename |
| shape, clarity, color, cut, fluor | TEXT | Diamond attributes |
| current_disc, last_week_disc, avg_disc, ... | REAL | Discount percentages |
| inv_days, stock, sold_3m, sold_1w | REAL | Inventory & sales data |
| rapnet_pos_india, _world, _usa | REAL | RapNet position |
| avg_disc_gap, is_program, trigger_count, ... | REAL/INT | Derived features |

### `pricing_results`
AI pricing decisions. Upsert key: `(criteria_key, report_date)`.

| Column | Type | Description |
|--------|------|-------------|
| action | TEXT | INCREASE_DISC / DECREASE_DISC / KEEP |
| suggested_disc | REAL | Recommended discount % |
| change_pct | REAL | Delta from current_disc |
| confidence | TEXT | HIGH / MEDIUM / LOW |
| needs_review | INTEGER | 1 if manual review recommended |
| approved | INTEGER | 1 if approved by user |

### `rapnet_snapshots`
Point-in-time RapNet listing data per criteria.

### `ml_predictions`
XGBoost sell-probability scores (0-100) per criteria per report date.

### `activity_log`
Audit trail of all LOAD, AI_PRICING, APPROVE, PIPELINE_RUN events.

---

## Project Structure

```
diamond_engine/
├── .env.example              # Environment variable template
├── requirements.txt          # Pinned Python dependencies
├── README.md
├── data/
│   ├── raw/                  # Drop your xlsx files here
│   └── processed/            # Generated charts and intermediate data
├── db/
│   └── diamond.db            # SQLite database (auto-created)
├── models/
│   └── sell_model.pkl        # Trained XGBoost model (auto-created)
├── scripts/
│   ├── run_pipeline.py       # Load Base Reports -> DB
│   ├── run_ai_pricing.py     # Run AI pricing on latest report
│   └── run_ml_train.py       # Train ML sell-probability model
└── src/
    ├── pipeline/
    │   ├── loader.py          # Multi-row header Excel loader
    │   ├── normalizer.py      # Column normalisation & feature engineering
    │   └── db_writer.py       # SQLite upsert utilities
    ├── ai_brain/
    │   ├── system_prompt.py   # Pricing rules + PDF extraction
    │   ├── pricer.py          # Single-row Claude API call
    │   └── batch_pricer.py    # Async batch processing with retries
    ├── ml_model/
    │   ├── features.py        # Feature engineering for ML
    │   ├── train.py           # XGBoost training pipeline
    │   ├── predict.py         # Score new data
    │   └── model_store.py     # joblib save/load
    ├── scraper/
    │   └── rapnet_scraper.py  # Playwright RapNet scraper
    ├── orders/
    │   └── order_tracker.py   # DANY ORDER LIST loader & analysis
    └── dashboard/
        └── app.py             # Streamlit 5-tab dashboard
```
