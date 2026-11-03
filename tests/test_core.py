from core.product_classifier import classify_tma_product
from core.trend_model import fit_resale_trend
import pandas as pd


def test_taxonomy():
    assert classify_tma_product("Single Family Residence") == "SFD"
    assert classify_tma_product("Condominium") == "Attached"


def test_trend_minimum_rows():
    assert fit_resale_trend(pd.DataFrame({"Living Area": [1800, 1900], "Close Price": [500000, 530000]})) is None


def test_trend_unmodified_columns():
    data = pd.DataFrame({"Living Area": [1200 + 150 * i for i in range(12)],
                         "Close Price": [340000 + 51000 * i for i in range(12)],
                         "Keep": [True] * 12})
    model = fit_resale_trend(data)
    assert model is not None and model["row_count"] == 12
    assert model["version"] == "standardized_huber_linear_v4"
