"""
predict.py
----------
Load a trained XGBoost sell-probability model and score a DataFrame.

The ``score_dataframe`` function adds a ``sell_score`` column (0-100 integer)
to the input DataFrame, where higher values indicate a higher predicted
probability of selling within 7 days.
"""

import logging
from pathlib import Path
from typing import Union

import numpy as np
import pandas as pd

from src.ml_model.features import build_features
from src.ml_model.model_store import load_model

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def score_dataframe(df: pd.DataFrame, model) -> pd.DataFrame:
    """
    Score a normalised Base Report DataFrame using a trained sell-probability
    model.

    Adds a ``sell_score`` column to the DataFrame, representing the predicted
    probability of selling within 7 days, scaled to an integer in [0, 100].
    Higher scores indicate a more likely sale.

    Parameters
    ----------
    df : pd.DataFrame
        Normalised DataFrame (output of ``normalizer.run_full_normalisation``).
        Must contain the columns needed by ``build_features``.
    model : XGBClassifier
        Trained XGBoost classifier as loaded by ``model_store.load_model``.

    Returns
    -------
    pd.DataFrame
        Input DataFrame with an additional ``sell_score`` (int, 0-100) column.
    """
    if df.empty:
        df["sell_score"] = pd.Series(dtype=int)
        return df

    # Build feature matrix
    X = build_features(df)

    # Predict probability of the positive class (index 1)
    try:
        prob_positive = model.predict_proba(X.values)[:, 1]
    except IndexError:
        # Binary output with only one class in training
        prob_positive = model.predict_proba(X.values)[:, 0]

    # Scale to 0-100 integer
    sell_scores = (prob_positive * 100).round().astype(int)
    sell_scores = np.clip(sell_scores, 0, 100)

    df = df.copy()
    df["sell_score"] = sell_scores

    logger.info(
        "Scored %d rows. Score range: %d - %d, mean: %.1f",
        len(df),
        sell_scores.min(),
        sell_scores.max(),
        sell_scores.mean(),
    )
    return df
