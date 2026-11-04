"""Quick Scan TMA labels preserve geography across all resale-category traces."""
from quick_workspace import quick_tma_label


def test_quick_tma_city():
    assert quick_tma_label("City + state",{"city":"Carson","state":"CA"})=="TMA Carson"


def test_quick_tma_zip():
    assert quick_tma_label("ZIP code",{"zipCode":"90805"})=="TMA 90805"


def test_quick_tma_radius_singular_plural_and_decimal():
    for radius, expected in [
        (1,"TMA 1-mile radius"),
        (2,"TMA 2-mile radius"),
        (2.5,"TMA 2.5-mile radius"),
    ]:
        assert quick_tma_label("Coordinates + radius",{"radius":radius})==expected


def test_quick_tma_labels_feed_both_categories():
    label=quick_tma_label("City + state",{"city":"El Cajon","state":"CA"})
    assert label+" · SFD"=="TMA El Cajon · SFD"
    assert label+" · Attached"=="TMA El Cajon · Attached"
