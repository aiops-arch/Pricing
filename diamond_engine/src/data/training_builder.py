"""
training_builder.py
-------------------
Joins pricing_snapshots + position_stones to build the ML training dataset.

Two label modes:
  1. price_change_7d  — did Disc Per change within next 7 days? (available now)
  2. sold_30d         — was a stone in this criteria group sold within 30 days?
                        (requires sales file — populated by sales_loader.py)

Output table: training_dataset
"""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_HERE   = Path(__file__).resolve()
DB_PATH = _HERE.parents[2] / "db" / "training.db"

DDL_TRAINING = """
CREATE TABLE IF NOT EXISTS training_dataset (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Snapshot identifiers
    snapshot_date        TEXT NOT NULL,
    snapshot_dt          TEXT NOT NULL,
    criteria_key         TEXT NOT NULL,

    -- Criteria group attributes (features)
    color                TEXT,
    clarity              TEXT,
    fluor                TEXT,
    from_size            REAL,
    to_size              REAL,

    -- Current pricing (target variable context)
    disc_per             REAL,

    -- Price history features
    disc_7d_ago          REAL,   -- disc_per 7 days before snapshot_date
    disc_30d_ago         REAL,   -- disc_per 30 days before snapshot_date
    days_since_last_chg  INTEGER,-- days since disc_per last changed

    -- Market context features (from position_stones)
    stone_count          INTEGER,
    avg_aging_days       REAL,
    avg_rapnet_disc      REAL,
    avg_base_pd_disc     REAL,
    min_rapnet_pos_world INTEGER,
    min_rapnet_pos_ind   INTEGER,
    min_rapnet_pos_usa   INTEGER,
    avg_comp_world_1st   REAL,
    avg_comp_india_1st   REAL,
    avg_comp_usa_1st     REAL,
    stones_in_stock      INTEGER,
    stones_on_memo       INTEGER,
    avg_limit_1          REAL,

    -- Labels
    price_change_7d      INTEGER,  -- 0/1: disc_per changed within next 7 calendar days
    price_change_dir     INTEGER,  -- +1 increase (cheaper), -1 decrease (more expensive), 0 no change
    price_change_mag     REAL,     -- magnitude of next change (abs value, percentage points)
    sold_30d             INTEGER,  -- 0/1: stone sold within 30 days (NULL until sales file loaded)

    UNIQUE(criteria_key, snapshot_dt)
)
"""

DDL_IDX = [
    "CREATE INDEX IF NOT EXISTS idx_td_date     ON training_dataset(snapshot_date)",
    "CREATE INDEX IF NOT EXISTS idx_td_key      ON training_dataset(criteria_key)",
    "CREATE INDEX IF NOT EXISTS idx_td_key_date ON training_dataset(criteria_key, snapshot_date)",
]


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute(DDL_TRAINING)
    for idx in DDL_IDX:
        conn.execute(idx)
    conn.commit()


def _build_price_history_features(conn: sqlite3.Connection) -> dict:
    """
    For every (criteria_key, snapshot_date) in pricing_snapshots, compute:
      - disc_7d_ago
      - disc_30d_ago
      - days_since_last_chg
      - price_change_7d (label)
      - price_change_dir
      - price_change_mag

    Returns a dict: (criteria_key, snapshot_dt) → feature dict
    """
    logger.info("Building price history features…")

    rows = conn.execute("""
        SELECT criteria_key, snapshot_date, snapshot_dt, disc_per
        FROM pricing_snapshots
        ORDER BY criteria_key, snapshot_dt
    """).fetchall()

    # Group by criteria_key
    from collections import defaultdict
    from datetime import datetime, timedelta

    key_snapshots: dict[str, list] = defaultdict(list)
    for criteria_key, snap_date, snap_dt, disc_per in rows:
        key_snapshots[criteria_key].append((snap_date, snap_dt, disc_per))

    features = {}

    for criteria_key, snaps in key_snapshots.items():
        # snaps already ordered by snap_dt
        n = len(snaps)
        for i, (snap_date, snap_dt, disc_per) in enumerate(snaps):
            dt_obj = datetime.strptime(snap_date, "%Y-%m-%d")

            # disc_7d_ago: most recent disc_per from >= 7 days before
            disc_7d_ago = None
            disc_30d_ago = None
            days_since_last_chg = None

            for j in range(i - 1, -1, -1):
                prev_date_str, _, prev_disc = snaps[j]
                prev_dt = datetime.strptime(prev_date_str, "%Y-%m-%d")
                delta = (dt_obj - prev_dt).days

                if disc_7d_ago is None and delta >= 7:
                    disc_7d_ago = prev_disc
                if disc_30d_ago is None and delta >= 30:
                    disc_30d_ago = prev_disc
                    break

                # days since last change
                if days_since_last_chg is None and prev_disc != disc_per:
                    days_since_last_chg = delta

            # price_change_7d label: look forward
            price_change_7d = 0
            price_change_dir = 0
            price_change_mag = 0.0

            for j in range(i + 1, n):
                fut_date_str, _, fut_disc = snaps[j]
                fut_dt = datetime.strptime(fut_date_str, "%Y-%m-%d")
                delta = (fut_dt - dt_obj).days
                if delta > 7:
                    break
                if fut_disc != disc_per:
                    price_change_7d = 1
                    diff = fut_disc - disc_per
                    price_change_dir = 1 if diff > 0 else -1
                    price_change_mag = abs(diff)
                    break

            features[(criteria_key, snap_dt)] = {
                "disc_7d_ago":         disc_7d_ago,
                "disc_30d_ago":        disc_30d_ago,
                "days_since_last_chg": days_since_last_chg,
                "price_change_7d":     price_change_7d,
                "price_change_dir":    price_change_dir,
                "price_change_mag":    price_change_mag,
            }

    logger.info("Price history features built for %d (key, dt) pairs", len(features))
    return features


def _build_market_context(conn: sqlite3.Connection) -> dict:
    """
    Aggregate position_stones per (criteria_key, report_date).

    Join key: position_stones.psize == pricing_snapshots.from_size  (exact match)
    Psize in the position report IS the size-group code from the backup CSV.

    When multiple criteria_keys share the same from_size (e.g. 1.00-1.04 and
    1.00-1.20 both start at 1.00), each criteria_key gets the SAME stone count —
    all stones with Psize=1.00 belonging to that color/clarity/fluor combo.

    Returns a dict: (criteria_key, report_date) → aggregated feature dict
    """
    logger.info("Aggregating market context from position_stones…")

    # criteria_key lookup: (color, clarity, fluor, from_size) → [criteria_key, ...]
    from collections import defaultdict

    bucket_keys: dict[tuple, list] = defaultdict(list)
    for row in conn.execute("""
        SELECT DISTINCT criteria_key, color, clarity, fluor, from_size
        FROM pricing_snapshots
    """):
        criteria_key, color, clarity, fluor, from_size = row
        bucket_keys[(color, clarity, fluor, from_size)].append(criteria_key)

    if not bucket_keys:
        logger.warning("No pricing_snapshots data found — market context will be empty")
        return {}

    stones = conn.execute("""
        SELECT report_date, color, clarity, fluor, psize,
               aging_days, stone_status,
               rapnet_disc, base_pd_disc, limit_1,
               rapnet_pos_world, rapnet_pos_ind, rapnet_pos_usa,
               comp_world_01, comp_india_01, comp_usa_01
        FROM position_stones
        WHERE color IS NOT NULL AND clarity IS NOT NULL
          AND fluor IS NOT NULL AND psize IS NOT NULL
    """).fetchall()

    if not stones:
        logger.warning("No position_stones data found — market context will be empty")
        return {}

    def new_bucket():
        return {
            "stone_count": 0,
            "aging_sum": 0.0,
            "rapnet_disc_sum": 0.0,
            "base_pd_sum": 0.0,
            "limit_1_sum": 0.0,
            "rapnet_disc_n": 0,
            "base_pd_n": 0,
            "limit_1_n": 0,
            "rapnet_pos_world_min": None,
            "rapnet_pos_ind_min": None,
            "rapnet_pos_usa_min": None,
            "comp_world_1st_sum": 0.0,
            "comp_world_1st_n": 0,
            "comp_india_1st_sum": 0.0,
            "comp_india_1st_n": 0,
            "comp_usa_1st_sum": 0.0,
            "comp_usa_1st_n": 0,
            "stones_in_stock": 0,
            "stones_on_memo": 0,
        }

    context_raw: dict[tuple, dict] = defaultdict(new_bucket)

    for (report_date, color, clarity, fluor, psize,
         aging, status, rapnet_disc, base_pd, limit_1,
         pos_world, pos_ind, pos_usa,
         cw1, ci1, cu1) in stones:

        # Exact Psize = from_size join — one stone may map to multiple
        # criteria_keys when overlapping buckets share the same from_size
        matched_keys = bucket_keys.get((color, clarity, fluor, psize), [])
        if not matched_keys:
            continue

        for ck in matched_keys:
            key = (ck, report_date)
            d = context_raw[key]
            d["stone_count"] += 1
            if aging is not None:
                d["aging_sum"] += aging
            if rapnet_disc is not None:
                d["rapnet_disc_sum"] += rapnet_disc
                d["rapnet_disc_n"] += 1
            if base_pd is not None:
                d["base_pd_sum"] += base_pd
                d["base_pd_n"] += 1
            if limit_1 is not None:
                d["limit_1_sum"] += limit_1
                d["limit_1_n"] += 1
            if pos_world is not None:
                d["rapnet_pos_world_min"] = min(d["rapnet_pos_world_min"] or pos_world, pos_world)
            if pos_ind is not None:
                d["rapnet_pos_ind_min"] = min(d["rapnet_pos_ind_min"] or pos_ind, pos_ind)
            if pos_usa is not None:
                d["rapnet_pos_usa_min"] = min(d["rapnet_pos_usa_min"] or pos_usa, pos_usa)
            if cw1 is not None:
                d["comp_world_1st_sum"] += cw1
                d["comp_world_1st_n"] += 1
            if ci1 is not None:
                d["comp_india_1st_sum"] += ci1
                d["comp_india_1st_n"] += 1
            if cu1 is not None:
                d["comp_usa_1st_sum"] += cu1
                d["comp_usa_1st_n"] += 1
            status_lc = (status or "").lower()
            if "memo" in status_lc:
                d["stones_on_memo"] += 1
            else:
                d["stones_in_stock"] += 1

    def safe_avg(total, n):
        return total / n if n > 0 else None

    context: dict[tuple, dict] = {}
    for (ck, rdate), d in context_raw.items():
        n = d["stone_count"]
        context[(ck, rdate)] = {
            "stone_count":          n,
            "avg_aging_days":       d["aging_sum"] / n if n > 0 else None,
            "avg_rapnet_disc":      safe_avg(d["rapnet_disc_sum"], d["rapnet_disc_n"]),
            "avg_base_pd_disc":     safe_avg(d["base_pd_sum"],     d["base_pd_n"]),
            "min_rapnet_pos_world": d["rapnet_pos_world_min"],
            "min_rapnet_pos_ind":   d["rapnet_pos_ind_min"],
            "min_rapnet_pos_usa":   d["rapnet_pos_usa_min"],
            "avg_comp_world_1st":   safe_avg(d["comp_world_1st_sum"], d["comp_world_1st_n"]),
            "avg_comp_india_1st":   safe_avg(d["comp_india_1st_sum"], d["comp_india_1st_n"]),
            "avg_comp_usa_1st":     safe_avg(d["comp_usa_1st_sum"],   d["comp_usa_1st_n"]),
            "stones_in_stock":      d["stones_in_stock"],
            "stones_on_memo":       d["stones_on_memo"],
            "avg_limit_1":          safe_avg(d["limit_1_sum"],        d["limit_1_n"]),
        }

    logger.info("Market context built for %d (key, date) pairs", len(context))
    return context


def build_training_dataset(
    db_path: Path = DB_PATH,
    force_rebuild: bool = False,
) -> int:
    """
    Build training_dataset table from pricing_snapshots + position_stones.
    Returns number of rows inserted.
    """
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        _init_db(conn)

        if force_rebuild:
            conn.execute("DELETE FROM training_dataset")
            conn.commit()

        # Check what's already built
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT snapshot_dt FROM training_dataset"
            ).fetchall()
        }

        # Load all pricing snapshots not yet in training_dataset
        snapshots = conn.execute("""
            SELECT snapshot_date, snapshot_dt, criteria_key,
                   color, clarity, fluor, from_size, to_size, disc_per
            FROM pricing_snapshots
            ORDER BY snapshot_dt
        """).fetchall()

    new_snapshots = [s for s in snapshots if s[1] not in existing]
    if not new_snapshots:
        logger.info("Training dataset is already up to date.")
        return 0

    logger.info("%d new snapshot rows to process", len(new_snapshots))

    with sqlite3.connect(str(db_path)) as conn:
        price_features = _build_price_history_features(conn)
        market_ctx     = _build_market_context(conn)

    rows_to_insert = []
    for snap in new_snapshots:
        snap_date, snap_dt, ck, color, clarity, fluor, from_s, to_s, disc_per = snap

        pf = price_features.get((ck, snap_dt), {})
        mc = market_ctx.get((ck, snap_date), {})

        rows_to_insert.append((
            snap_date,
            snap_dt,
            ck,
            color,
            clarity,
            fluor,
            from_s,
            to_s,
            disc_per,
            pf.get("disc_7d_ago"),
            pf.get("disc_30d_ago"),
            pf.get("days_since_last_chg"),
            mc.get("stone_count"),
            mc.get("avg_aging_days"),
            mc.get("avg_rapnet_disc"),
            mc.get("avg_base_pd_disc"),
            mc.get("min_rapnet_pos_world"),
            mc.get("min_rapnet_pos_ind"),
            mc.get("min_rapnet_pos_usa"),
            mc.get("avg_comp_world_1st"),
            mc.get("avg_comp_india_1st"),
            mc.get("avg_comp_usa_1st"),
            mc.get("stones_in_stock"),
            mc.get("stones_on_memo"),
            mc.get("avg_limit_1"),
            pf.get("price_change_7d", 0),
            pf.get("price_change_dir", 0),
            pf.get("price_change_mag", 0.0),
            None,  # sold_30d — filled by sales_loader
        ))

    with sqlite3.connect(str(db_path)) as conn:
        conn.executemany(
            """INSERT OR IGNORE INTO training_dataset
               (snapshot_date, snapshot_dt, criteria_key,
                color, clarity, fluor, from_size, to_size, disc_per,
                disc_7d_ago, disc_30d_ago, days_since_last_chg,
                stone_count, avg_aging_days, avg_rapnet_disc, avg_base_pd_disc,
                min_rapnet_pos_world, min_rapnet_pos_ind, min_rapnet_pos_usa,
                avg_comp_world_1st, avg_comp_india_1st, avg_comp_usa_1st,
                stones_in_stock, stones_on_memo, avg_limit_1,
                price_change_7d, price_change_dir, price_change_mag,
                sold_30d)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows_to_insert,
        )
        conn.commit()

    logger.info("Training dataset built. %d rows inserted.", len(rows_to_insert))
    return len(rows_to_insert)
