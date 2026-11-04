"""Regression tests for in-memory subject price adjustments and reliability gates."""
from types import SimpleNamespace
import pandas as pd
import pytest
from price_player import eligible_market_models, price_proposals


def eligible_model():
    return {
        "automatic_pricing_eligible":True,
        "pricing_reliability":"Moderate",
        "sf_min":1400.0,"sf_max":2300.0,
        "intercept":150000.0,"slope":350.0,
    }


def test_prices_each_existing_plan_by_its_own_sf():
    plans=[{"name":"Small","sqft":1500,"base_price":0},
           {"name":"Large","sqft":2000,"base_price":1000000}]
    prices=price_proposals(plans,eligible_model(),5)
    assert prices[0]["trend_price"]==675000
    assert prices[0]["target_price"]==708750
    assert prices[1]["trend_price"]==850000
    assert prices[1]["target_price"]==892500
    assert plans[0]["base_price"]==0  # preview must not mutate original inputs


def test_price_player_blocks_unreliable_or_outside_support():
    model=eligible_model()
    model["automatic_pricing_eligible"]=False
    with pytest.raises(ValueError,match="not eligible"):
        price_proposals([{"sqft":1700}],model,0)
    model["automatic_pricing_eligible"]=True
    rows=price_proposals([{"sqft":2500,"name":"Oversized","base_price":0}],model,0)
    assert rows[0]["extrapolated"] is True
    assert rows[0]["target_price"]==1025000
    assert rows[0]["support_boundary_sf"]==2300
    assert rows[0]["distance_sf"]==200
    assert rows[0]["distance_pct"]==8.7


def test_negative_adjustment_and_invalid_input_bounds():
    prices=price_proposals([{"sqft":1500,"base_price":0}],eligible_model(),-10)
    assert prices[0]["target_price"]==607500
    with pytest.raises(ValueError,match="between"):
        price_proposals([{"sqft":1500}],eligible_model(),-30)


def test_model_collection_uses_same_closed_sale_category_filter(monkeypatch):
    import price_player
    data=pd.DataFrame({
        "Standard Status":["Closed","Active","Closed"],
        "TMA Type":["SFD","SFD","Attached"],
        "Living Area":[1500,1800,1600],
        "Close Price":[700000,0,690000],
    })
    def fake_outliers(data):
        return data.assign(Keep=True),{}
    def fake_fit(rows):
        return eligible_model() if rows["TMA Type"].iloc[0]=="SFD" else {
            **eligible_model(),"automatic_pricing_eligible":False
        }
    monkeypatch.setattr(price_player,"analyze_tma_outliers",fake_outliers)
    monkeypatch.setattr(price_player,"fit_resale_trend",fake_fit)
    models,warnings=eligible_market_models(data,["SFD","Attached"])
    assert list(models)==["SFD"]
    assert "Attached" in warnings


def test_extrapolation_below_range_and_no_extra_model_fit():
    model=eligible_model()
    results=price_proposals([
        {"name":"Small","sqft":1200,"base_price":0},
        {"name":"Normal","sqft":1500,"base_price":0},
        {"name":"Far larger","sqft":2800,"base_price":0},
    ],model,5)
    assert results[0]["extrapolated"] is True
    assert results[0]["support_boundary_sf"]==1400
    assert results[0]["distance_sf"]==200
    assert results[1]["extrapolated"] is False
    assert results[2]["extrapolated"] is True
    assert results[2]["distance_sf"]==500
    assert results[2]["target_price"]==round((150000+350*2800)*1.05)


def test_nonpositive_extrapolated_predictions_stay_blocked():
    model=eligible_model()
    model["intercept"]=-500000
    result=price_proposals([{"name":"Tiny","sqft":100,"base_price":0}],model,0)
    assert "target_price" not in result[0]
    assert result[0]["reason"]=="Invalid trend prediction"
