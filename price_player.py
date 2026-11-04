"""Read-only Quick Scan Price Player using the identical MLS trend equation.

Models are built from the already loaded Quick Scan result; no warehouse reads,
new subject-plan inputs, SQL writes, or desktop application dependencies.
"""
from __future__ import annotations

from math import isfinite
import pandas as pd
from core.outlier_engine import analyze_tma_outliers
from core.trend_model import fit_resale_trend, is_within_trend_support


def eligible_market_models(frame: pd.DataFrame, products: list[str]) -> tuple[dict, dict]:
    """Fit each selected category separately and preserve explanatory blockers."""
    models={}
    warnings={}
    if frame is None or frame.empty:
        return models,{p:"No eligible warehouse records" for p in products}
    closed=frame[frame["Standard Status"].eq("Closed")]
    for product in products:
        records=closed[closed["TMA Type"].eq(product)].copy()
        if records.empty:
            warnings[product]="No closed MLS sales in this category"
            continue
        try:
            tagged,_=analyze_tma_outliers(records)
        except ValueError:
            tagged=records.assign(Keep=True)
        try:
            model=fit_resale_trend(tagged)
        except (ValueError,TypeError):
            model=None
        if model is None:
            warnings[product]="Insufficient retained sales or size variation to fit the trend"
        elif not model.get("automatic_pricing_eligible",False):
            warnings[product]="Trend is descriptive only; automatic pricing is not validated"
        else:
            models[product]=model
    return models,warnings


def price_proposals(plans: list[dict], model: dict, percent: float) -> list[dict]:
    """Return supported and clearly identified extrapolated predictions from one fitted equation."""
    if not model or not model.get("automatic_pricing_eligible",False):
        raise ValueError("The selected trend is not eligible for automatic pricing")
    offset=float(percent)
    if not isfinite(offset) or not -25<=offset<=25:
        raise ValueError("The adjustment must be between -25% and +25%")
    output=[]
    for index,plan in enumerate(plans):
        sqft=float(plan.get("sqft") or 0)
        row={"index":index,"name":str(plan.get("name") or f"Plan {index+1}"),
             "sqft":sqft,"existing_price":float(plan.get("base_price") or 0)}
        if not isfinite(sqft) or sqft<=0:
            row["reason"]="No valid square footage"
        else:
            lo=float(model["sf_min"]); hi=float(model["sf_max"])
            extrapolated=not is_within_trend_support(model,sqft)
            row["extrapolated"]=extrapolated
            if extrapolated:
                boundary=lo if sqft<lo else hi
                row["support_boundary_sf"]=boundary
                row["distance_sf"]=abs(sqft-boundary)
                row["distance_pct"]=round(abs(sqft-boundary)/boundary*100,1)
                row["support_min_sf"]=lo
                row["support_max_sf"]=hi
            baseline=float(model["intercept"])+float(model["slope"])*sqft
            if not isfinite(baseline) or baseline<=0:
                row["reason"]="Invalid trend prediction"
            else:
                target=baseline*(1+offset/100)
                if not isfinite(target) or target<=0 or target>100000000:
                    row["reason"]="Adjusted price outside supported range"
                else:
                    row["trend_price"]=baseline
                    row["target_price"]=round(target)
        output.append(row)
    return output
