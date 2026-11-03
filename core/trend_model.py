from __future__ import annotations

"""Shared resale trend model used by charts, Price Player and exports.

A trend is fitted once from the complete retained/non-outlier market population.
Viewport/axis changes only alter the visible segment of that equation; they never alter
which observations are fitted or the slope/intercept.

Build dev.10.14.0 standardizes both SF and price before the Huber fit, then transforms
that robust equation back into ordinary dollar/SF units.  This removes optimizer-scale
artifacts while keeping every downstream surface on the exact same market equation.
"""

from typing import Any, Optional

import numpy as np
import pandas as pd
from sklearn.linear_model import HuberRegressor
from sklearn.model_selection import KFold

TREND_MODEL_VERSION = "standardized_huber_linear_v4"
MIN_TREND_ROWS = 8
MIN_TREND_SF_SPREAD = 250.0
MIN_AUTOPRICE_R2 = 0.20
MAX_AUTOPRICE_MEDIAN_APE = 20.0
MIN_BASELINE_IMPROVEMENT_PCT = 5.0
TREND_DISPLAY_PADDING_RATIO = 0.08


def _finite_float(value: Any) -> float | None:
    try:
        out = float(value)
    except Exception:
        return None
    return out if np.isfinite(out) else None




def _fit_standardized_huber(x: np.ndarray, y: np.ndarray) -> tuple[float, float, str]:
    """Fit robust linear price~SF after unit-standardization and back-transform."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    x_mean = float(np.mean(x)); y_mean = float(np.mean(y))
    x_scale = float(np.std(x, ddof=0)); y_scale = float(np.std(y, ddof=0))
    if not np.isfinite(x_scale) or x_scale <= 0 or not np.isfinite(y_scale) or y_scale <= 0:
        raise ValueError("trend standardization has zero/invalid scale")
    x_std = ((x - x_mean) / x_scale).reshape(-1, 1)
    y_std = (y - y_mean) / y_scale
    model = HuberRegressor(epsilon=1.35, alpha=0.0, max_iter=2000, tol=1e-7).fit(x_std, y_std)
    slope_std = float(model.coef_[0]); intercept_std = float(model.intercept_)
    slope = float((y_scale / x_scale) * slope_std)
    intercept = float(y_mean + y_scale * intercept_std - slope * x_mean)
    return slope, intercept, "HuberRegressor_standardized"


def _validation_metrics(x: np.ndarray, y: np.ndarray) -> dict[str, float | int | None]:
    """Deterministic out-of-sample diagnostics used to gate Price Player autofill.

    Five-fold validation is deliberately small: it materially improves the honesty of
    the pricing diagnostics without turning every Streamlit rerun into a heavy model
    training job.  Each fold compares the robust trend to a training-set median-price
    baseline, so the gate does not reward in-sample overfit.
    """
    n = int(len(x))
    folds = min(5, max(2, n // 4))
    if n < 8 or folds < 2:
        return {"folds": 0}
    kf = KFold(n_splits=folds, shuffle=True, random_state=314159)
    predicted = np.full(n, np.nan, dtype=float)
    baseline_pred = np.full(n, np.nan, dtype=float)
    for train_idx, test_idx in kf.split(x):
        xt=x[train_idx]; yt=y[train_idx]
        try:
            slope, intercept, _ = _fit_standardized_huber(xt, yt)
        except Exception:
            slope, intercept = np.polyfit(xt, yt, 1)
            slope=float(slope); intercept=float(intercept)
        predicted[test_idx] = slope*x[test_idx] + intercept
        baseline_pred[test_idx] = float(np.median(yt))
    if not np.isfinite(predicted).all() or not np.isfinite(baseline_pred).all():
        return {"folds": folds}
    err=np.abs(y-predicted)
    pct=np.divide(err,y,out=np.full_like(err,np.nan,dtype=float),where=y>0)*100.0
    base_err=np.abs(y-baseline_pred)
    base_pct=np.divide(base_err,y,out=np.full_like(base_err,np.nan,dtype=float),where=y>0)*100.0
    centered=float(np.square(y-float(np.mean(y))).sum())
    sse=float(np.square(y-predicted).sum())
    vr2=(1.0-sse/centered) if centered>0 else None
    med=float(np.median(err)); base_med=float(np.median(base_err))
    improvement=((base_med-med)/base_med*100.0) if base_med>0 else 0.0
    return {
        "folds": folds,
        "r2": float(vr2) if vr2 is not None else None,
        "median_absolute_error": med,
        "mean_absolute_error": float(np.mean(err)),
        "median_absolute_percentage_error": float(np.nanmedian(pct)),
        "mean_absolute_percentage_error": float(np.nanmean(pct)),
        "baseline_median_absolute_error": base_med,
        "baseline_median_absolute_percentage_error": float(np.nanmedian(base_pct)),
        "baseline_improvement_pct": float(improvement),
    }

def _pricing_reliability(*, r2: float | None, median_ape: float | None, baseline_improvement_pct: float | None) -> str:
    if r2 is None or median_ape is None or baseline_improvement_pct is None:
        return "Blocked"
    if r2 >= 0.60 and median_ape <= 10.0 and baseline_improvement_pct >= 15.0:
        return "High"
    if r2 >= 0.35 and median_ape <= 15.0 and baseline_improvement_pct >= 8.0:
        return "Moderate"
    if r2 >= MIN_AUTOPRICE_R2 and median_ape <= MAX_AUTOPRICE_MEDIAN_APE and baseline_improvement_pct >= MIN_BASELINE_IMPROVEMENT_PCT:
        return "Caution"
    return "Blocked"


def fit_resale_trend(
    frame: pd.DataFrame | None,
    *,
    x_col: str = "Living Area",
    y_col: str = "Close Price",
    keep_col: str = "Keep",
    min_rows: int = MIN_TREND_ROWS,
    min_sf_spread: float = MIN_TREND_SF_SPREAD,
) -> Optional[dict[str, Any]]:
    """Fit the canonical robust resale trend to all retained observations.

    The model is intentionally one-variable: total sale price as a function of living
    area.  Outlier decisions are upstream: if ``Keep`` exists, only rows where Keep is
    true are fitted.  No chart bounds, zoom window or new-home SF band is accepted by
    this API.

    Huber is solved on standardized X and Y to avoid unit-scale conditioning problems.
    The returned slope/intercept are back-transformed to normal ``dollars`` and
    ``dollars per SF`` so Price Player and exports can use them directly.
    """
    if not isinstance(frame, pd.DataFrame) or frame.empty or x_col not in frame.columns or y_col not in frame.columns:
        return None

    raw = frame.copy()
    raw_x = pd.to_numeric(raw[x_col], errors="coerce")
    raw_y = pd.to_numeric(raw[y_col], errors="coerce")
    valid = raw_x.gt(0) & raw_y.gt(0) & raw_x.notna() & raw_y.notna()
    valid_count = int(valid.sum())
    work = raw.loc[valid].copy()
    work[x_col] = raw_x.loc[valid]
    work[y_col] = raw_y.loc[valid]
    if keep_col in work.columns:
        work = work[work[keep_col].fillna(False).astype(bool)].copy()

    retained_count = int(len(work))
    if retained_count < int(min_rows):
        return None

    x = work[x_col].to_numpy(float)
    y = work[y_col].to_numpy(float)
    sf_min = float(np.min(x))
    sf_max = float(np.max(x))
    sf_spread = sf_max - sf_min
    if sf_spread < float(min_sf_spread):
        return None

    x_mean = float(np.mean(x))
    y_mean = float(np.mean(y))
    x_scale = float(np.std(x, ddof=0))
    y_scale = float(np.std(y, ddof=0))
    if not np.isfinite(x_scale) or x_scale <= 0 or not np.isfinite(y_scale) or y_scale <= 0:
        return None

    try:
        slope, intercept, method = _fit_standardized_huber(x, y)
    except Exception:
        # Deterministic numerical fallback.  This is intentionally explicit in the
        # model metadata so an analyst can see if robust fitting was unavailable.
        slope, intercept = np.polyfit(x, y, 1)
        slope, intercept = float(slope), float(intercept)
        method = "numpy_polyfit_fallback"

    predicted = slope * x + intercept
    residual = y - predicted
    abs_error = np.abs(residual)
    pct_error = np.divide(abs_error, y, out=np.full_like(abs_error, np.nan, dtype=float), where=y > 0) * 100.0

    sse = float(np.square(residual).sum())
    centered = float(np.square(y - y_mean).sum())
    r2 = (1.0 - sse / centered) if centered > 0 else None

    median_absolute_error = float(np.median(abs_error))
    mean_absolute_error = float(np.mean(abs_error))
    median_ape = float(np.nanmedian(pct_error)) if np.isfinite(pct_error).any() else None
    mean_ape = float(np.nanmean(pct_error)) if np.isfinite(pct_error).any() else None

    baseline_price = float(np.median(y))
    baseline_abs_error = np.abs(y - baseline_price)
    baseline_pct_error = np.divide(
        baseline_abs_error, y, out=np.full_like(baseline_abs_error, np.nan, dtype=float), where=y > 0
    ) * 100.0
    baseline_median_absolute_error = float(np.median(baseline_abs_error))
    baseline_median_ape = float(np.nanmedian(baseline_pct_error)) if np.isfinite(baseline_pct_error).any() else None

    if baseline_median_absolute_error > 0:
        baseline_improvement_pct = float(
            (baseline_median_absolute_error - median_absolute_error) / baseline_median_absolute_error * 100.0
        )
    else:
        baseline_improvement_pct = 0.0

    validation = _validation_metrics(x, y)
    validation_r2 = _finite_float(validation.get("r2"))
    validation_mape = _finite_float(validation.get("median_absolute_percentage_error"))
    validation_improvement = _finite_float(validation.get("baseline_improvement_pct"))

    blockers: list[str] = []
    if not np.isfinite(slope) or slope <= 0:
        blockers.append("non_positive_slope")
    if int(validation.get("folds", 0) or 0) < 2:
        blockers.append("validation_unavailable")
    if validation_r2 is None or validation_r2 < MIN_AUTOPRICE_R2:
        blockers.append("weak_fit_r2")
    if validation_mape is None or validation_mape > MAX_AUTOPRICE_MEDIAN_APE:
        blockers.append("high_median_percentage_error")
    if validation_improvement is None or validation_improvement < MIN_BASELINE_IMPROVEMENT_PCT:
        blockers.append("does_not_materially_beat_median_baseline")
    if np.any(predicted <= 0):
        blockers.append("non_positive_prediction_within_support")

    automatic_pricing_eligible = len(blockers) == 0
    reliability = _pricing_reliability(
        r2=validation_r2,
        median_ape=validation_mape,
        baseline_improvement_pct=validation_improvement,
    )
    if not automatic_pricing_eligible:
        reliability = "Blocked"

    return {
        "version": TREND_MODEL_VERSION,
        "method": method,
        "slope": slope,
        "intercept": intercept,
        "raw_valid_count": valid_count,
        "row_count": retained_count,
        "total_row_count": valid_count,
        "outlier_count": max(0, valid_count - retained_count),
        "sf_min": sf_min,
        "sf_max": sf_max,
        "sf_spread": sf_spread,
        "r2": float(r2) if r2 is not None else None,
        "median_absolute_error": median_absolute_error,
        "mean_absolute_error": mean_absolute_error,
        "median_absolute_percentage_error": median_ape,
        "mean_absolute_percentage_error": mean_ape,
        "baseline_median_price": baseline_price,
        "baseline_median_absolute_error": baseline_median_absolute_error,
        "baseline_median_absolute_percentage_error": baseline_median_ape,
        "baseline_improvement_pct": baseline_improvement_pct,
        "validation_folds": int(validation.get("folds", 0) or 0),
        "validation_r2": validation.get("r2"),
        "validation_median_absolute_error": validation.get("median_absolute_error"),
        "validation_mean_absolute_error": validation.get("mean_absolute_error"),
        "validation_median_absolute_percentage_error": validation.get("median_absolute_percentage_error"),
        "validation_mean_absolute_percentage_error": validation.get("mean_absolute_percentage_error"),
        "validation_baseline_median_absolute_error": validation.get("baseline_median_absolute_error"),
        "validation_baseline_median_absolute_percentage_error": validation.get("baseline_median_absolute_percentage_error"),
        "validation_baseline_improvement_pct": validation.get("baseline_improvement_pct"),
        "automatic_pricing_eligible": automatic_pricing_eligible,
        "pricing_reliability": reliability,
        "pricing_blockers": blockers,
        "standardization": {
            "x_mean": x_mean,
            "x_scale": x_scale,
            "y_mean": y_mean,
            "y_scale": y_scale,
        },
        # Compatibility key retained for old UI consumers. It is now always false.
        "using_local_band": False,
        "fit_band": None,
    }


def is_within_trend_support(model: dict[str, Any] | None, sf: float | int | None) -> bool:
    if not model:
        return False
    try:
        x = float(sf)
        lo = float(model["sf_min"])
        hi = float(model["sf_max"])
    except Exception:
        return False
    return np.isfinite(x) and lo <= x <= hi


def trend_points(
    model: dict[str, Any] | None,
    x_min: float,
    x_max: float,
    *,
    points: int = 180,
    clip_to_support: bool = True,
    support_padding_ratio: float = TREND_DISPLAY_PADDING_RATIO,
) -> pd.DataFrame:
    """Return points from one stored equation without refitting it.

    By default the visible line stops near the retained-sale SF support instead of
    implying that far-away extrapolation is equally supported.  The equation itself is
    unchanged; only the rendered segment is clipped.
    """
    if not model or x_max <= x_min:
        return pd.DataFrame(columns=["SF", "Price"])

    lo = float(x_min)
    hi = float(x_max)
    if clip_to_support:
        sf_min = float(model.get("sf_min", lo))
        sf_max = float(model.get("sf_max", hi))
        spread = max(0.0, sf_max - sf_min)
        pad = spread * max(0.0, float(support_padding_ratio or 0.0))
        lo = max(lo, sf_min - pad)
        hi = min(hi, sf_max + pad)
    if hi <= lo:
        return pd.DataFrame(columns=["SF", "Price"])

    xs = np.linspace(lo, hi, max(2, int(points)))
    ys = float(model["slope"]) * xs + float(model["intercept"])
    return pd.DataFrame({"SF": xs, "Price": ys})
