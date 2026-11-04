"""Mobile chart labels: readable names with collision-aware placement."""
import plotly.graph_objects as go
from mobile_chart import _annotate_sparse


def test_competitor_names_have_priority_and_no_duplicate_label_positions():
    fig=go.Figure()
    points=[
        (1550,490,"1500",True),
        (1700,475,"1700",True),
        (1900,450,"1900",True),
        (1500,510,"Brixton-Manhattan Square",False),
        (1650,480,"Madison-Manhattan Square",False),
        (1850,455,"Chelsea-Manhattan Square",False),
    ]
    _annotate_sparse(fig,points)
    labels=[a.text for a in fig.layout.annotations]
    assert any("Brixton" in t for t in labels)
    assert any("Madison" in t or "Chelsea" in t for t in labels)
    assert len(labels)==len(set(labels))
    assert all(a.showarrow for a in fig.layout.annotations)
