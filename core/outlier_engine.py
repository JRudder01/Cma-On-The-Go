"""TMA resale outlier engine adapted from the user's prior Streamlit tool.

For TMA use, robust price-vs-living-area regression is the primary exclusion flag.
SFD and Attached must be passed separately. Default robust Z threshold = 3.0.
"""
from __future__ import annotations

from typing import Dict, Tuple

import numpy as np
import pandas as pd
from sklearn.linear_model import HuberRegressor


def robust_z(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    median = np.median(values)
    mad = np.median(np.abs(values - median))
    if mad > 1e-12:
        return 0.67448975 * (values - median) / mad
    std = np.std(values, ddof=1) if len(values) > 1 else 0
    if std > 1e-12:
        return (values - np.mean(values)) / std
    return np.zeros_like(values)


def analyze_tma_outliers(
    df: pd.DataFrame,
    price_col: str = "Close Price",
    area_col: str = "Living Area",
    robust_z_threshold: float = 3.0,
) -> Tuple[pd.DataFrame, Dict[str, float]]:
    """Flag price-vs-size outliers. Do not delete rows; analyst review decides final inclusion."""
    work = df.copy()
    work[price_col] = pd.to_numeric(work[price_col], errors="coerce")
    work[area_col] = pd.to_numeric(work[area_col], errors="coerce")
    valid = work[price_col].gt(0) & work[area_col].gt(0)
    work = work.loc[valid].copy()
    if len(work) < 3:
        raise ValueError("At least 3 valid listings are required for outlier analysis.")

    work["Price per Sq Ft"] = work[price_col] / work[area_col]
    x = np.log(work[area_col].to_numpy(float)).reshape(-1, 1)
    y = np.log(work[price_col].to_numpy(float))

    model = HuberRegressor(epsilon=1.35, max_iter=1000)
    try:
        model.fit(x, y)
        predicted_log = model.predict(x)
    except Exception:
        slope, intercept = np.polyfit(x.ravel(), y, 1)
        predicted_log = intercept + slope * x.ravel()

    work["Expected Close Price"] = np.exp(predicted_log)
    work["Difference from Expected"] = work[price_col] / work["Expected Close Price"] - 1
    work["Regression Robust Z"] = robust_z(y - predicted_log)
    work["Auto Exclude"] = work["Regression Robust Z"].abs() > float(robust_z_threshold)
    work["Keep"] = ~work["Auto Exclude"]

    diagnostics = {
        "listings": int(len(work)),
        "auto_excluded": int(work["Auto Exclude"].sum()),
        "threshold": float(robust_z_threshold),
    }
    return work, diagnostics
