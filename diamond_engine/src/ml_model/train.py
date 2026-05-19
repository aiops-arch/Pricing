"""
train.py
--------
Train the XGBoost sell-probability classifier on historical Base Report data
stored in the SQLite database.

A synthetic target label ``sell_within_7d`` is created using a heuristic:
  positive (1) if sold_1w > 0 OR (inv_days < 14 AND rapnet_pos_india <= 3).

The trained model is saved via joblib and feature importance is exported as a
PNG chart.
"""

import logging
import os
import sqlite3
from pathlib import Path
from typing import Union

import joblib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, classification_report
from sklearn.model_selection import train_test_split
from xgboost import XGBClassifier

from src.ml_model.features import FEATURE_COLS, build_features

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def load_training_data(db_path: Union[str, Path]) -> pd.DataFrame:
    """
    Load all rows from the ``base_report_rows`` table for training.

    Parameters
    ----------
    db_path : str or Path
        Path to the SQLite database.

    Returns
    -------
    pd.DataFrame
        All rows from base_report_rows.

    Raises
    ------
    ValueError
        If the table is empty or does not exist.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    with sqlite3.connect(str(db_path)) as conn:
        df = pd.read_sql("SELECT * FROM base_report_rows", conn)

    if df.empty:
        raise ValueError("base_report_rows table is empty — load some Base Reports first.")

    logger.info("Loaded %d rows for training from %s", len(df), db_path)
    return df


def create_synthetic_target(df: pd.DataFrame) -> pd.DataFrame:
    """
    Create a binary target column ``sell_within_7d`` using a heuristic.

    The label is 1 (positive / likely sold) if:
      - sold_1w > 0, OR
      - inv_days < 14 AND rapnet_pos_india <= 3

    This heuristic approximates "the stone sold or is highly likely to sell
    soon" based on observed data patterns.

    Parameters
    ----------
    df : pd.DataFrame
        Training data with at least ``sold_1w``, ``inv_days``, and
        ``rapnet_pos_india`` columns.

    Returns
    -------
    pd.DataFrame
        Input DataFrame with an additional ``sell_within_7d`` column (0 or 1).
    """
    df = df.copy()

    sold_1w = pd.to_numeric(df.get("sold_1w", 0), errors="coerce").fillna(0)
    inv_days = pd.to_numeric(df.get("inv_days", 999), errors="coerce").fillna(999)
    rapnet_pos = pd.to_numeric(df.get("rapnet_pos_india", 999), errors="coerce").fillna(999)

    df["sell_within_7d"] = (
        (sold_1w > 0) | ((inv_days < 14) & (rapnet_pos <= 3))
    ).astype(int)

    pos_rate = df["sell_within_7d"].mean()
    logger.info(
        "Synthetic target created: %d positive / %d negative (%.1f%% positive rate)",
        df["sell_within_7d"].sum(),
        (df["sell_within_7d"] == 0).sum(),
        pos_rate * 100,
    )
    return df


def _save_feature_importance_chart(model: XGBClassifier, feature_names: list[str], output_path: Union[str, Path]) -> None:
    """
    Save a feature importance bar chart as a PNG file.

    Parameters
    ----------
    model : XGBClassifier
    feature_names : list of str
    output_path : str or Path
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    importances = model.feature_importances_
    indices = np.argsort(importances)[::-1]
    sorted_names = [feature_names[i] for i in indices]
    sorted_importance = importances[indices]

    fig, ax = plt.subplots(figsize=(10, 6))
    bars = ax.barh(range(len(sorted_names)), sorted_importance[::-1], align="center", color="steelblue")
    ax.set_yticks(range(len(sorted_names)))
    ax.set_yticklabels(sorted_names[::-1])
    ax.set_xlabel("Feature Importance (Gain)")
    ax.set_title("XGBoost Feature Importance — Diamond Sell Probability")
    ax.invert_yaxis()
    plt.tight_layout()
    plt.savefig(str(output_path), dpi=150)
    plt.close(fig)
    logger.info("Feature importance chart saved to %s", output_path)


def train_model(
    db_path: Union[str, Path],
    model_path: Union[str, Path],
    feature_importance_chart_path: Union[str, Path] = "data/processed/feature_importance.png",
) -> XGBClassifier:
    """
    Train an XGBoost sell-probability classifier and save it to disk.

    Steps:
    1. Load training data from the database.
    2. Create the synthetic target label.
    3. Build the feature matrix.
    4. 80/20 train/test split.
    5. Train XGBClassifier.
    6. Print accuracy and classification report.
    7. Save feature importance chart as PNG.
    8. Save the trained model via joblib.

    Parameters
    ----------
    db_path : str or Path
        Path to the SQLite database.
    model_path : str or Path
        Path where the trained model will be saved (e.g. ``models/sell_model.pkl``).
    feature_importance_chart_path : str or Path
        Path for the feature importance PNG chart.

    Returns
    -------
    XGBClassifier
        The trained model.
    """
    db_path = Path(db_path)
    model_path = Path(model_path)
    model_path.parent.mkdir(parents=True, exist_ok=True)

    # Load and prepare data
    df = load_training_data(db_path)
    df = create_synthetic_target(df)

    X = build_features(df)
    y = df["sell_within_7d"].values

    logger.info("Feature matrix shape: %s, target shape: %s", X.shape, y.shape)

    # Train/test split
    X_train, X_test, y_train, y_test = train_test_split(
        X.values, y, test_size=0.2, random_state=42, stratify=y
    )
    logger.info("Train: %d rows, Test: %d rows", len(X_train), len(X_test))

    # XGBoost classifier
    model = XGBClassifier(
        n_estimators=200,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        use_label_encoder=False,
        eval_metric="logloss",
        random_state=42,
        n_jobs=-1,
    )
    model.fit(
        X_train,
        y_train,
        eval_set=[(X_test, y_test)],
        verbose=False,
    )

    # Evaluation
    y_pred = model.predict(X_test)
    accuracy = accuracy_score(y_test, y_pred)
    report = classification_report(y_test, y_pred, target_names=["No Sale", "Sold"])

    print(f"\n{'='*50}")
    print(f"XGBoost Model Accuracy: {accuracy:.4f} ({accuracy*100:.2f}%)")
    print(f"{'='*50}")
    print(report)

    logger.info("Model accuracy: %.4f", accuracy)

    # Save feature importance chart
    _save_feature_importance_chart(model, FEATURE_COLS, feature_importance_chart_path)

    # Save model
    joblib.dump(model, str(model_path))
    logger.info("Model saved to %s", model_path)

    return model
