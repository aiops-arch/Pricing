"""
model_store.py
--------------
Utilities for persisting and loading the trained ML model using joblib.
"""

import logging
from pathlib import Path
from typing import Any, Union

import joblib

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def save_model(model: Any, path: Union[str, Path]) -> None:
    """
    Serialise and save a trained model to disk using joblib.

    Creates parent directories if they do not exist.

    Parameters
    ----------
    model : Any
        Trained model object (e.g. XGBClassifier).
    path : str or Path
        Destination file path (e.g. ``models/sell_model.pkl``).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, str(path))
    logger.info("Model saved to %s", path)


def load_model(path: Union[str, Path]) -> Any:
    """
    Load a previously saved model from disk using joblib.

    Parameters
    ----------
    path : str or Path
        Path to the saved model file.

    Returns
    -------
    Any
        The deserialised model object.

    Raises
    ------
    FileNotFoundError
        If the model file does not exist at ``path``.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Model file not found: {path}")

    model = joblib.load(str(path))
    logger.info("Model loaded from %s", path)
    return model
