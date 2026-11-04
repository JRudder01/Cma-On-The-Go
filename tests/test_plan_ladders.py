"""Community plan ladders in the mobile chart, independent of the desktop CMA."""
from types import SimpleNamespace
import pandas as pd
import plotly.graph_objects as go
from mobile_chart import make_chart, _sorted_plan_points


def _empty_market():
    return [{"name":"Test TMA",
             "result":SimpleNamespace(available=True,mls=pd.DataFrame()),
             "products":["SFD"]}]


def _plan(name,sf,price,community=None):
    value={"name":name,"sqft":sf,"base_price":price}
    if community is not None:
        value["community_id"]=community
        value["community_name"]="Community "+str(community)
    return value


def test_plan_ladders_are_sorted_and_not_connected_across_communities():
    draft={"name":"Example Subject","subject_plans":[
        _plan("Large",2100,900000),_plan("Small",1500,700000),
        _plan("Mid",1800,800000)]}
    comps=[
        _plan("B",1900,850000,1),_plan("A",1400,630000,1),
        _plan("D",2000,920000,2),_plan("C",1700,750000,2)]
    fig,_,_=make_chart(_empty_market(),draft,comps,"Price/SF",6.5,20.)
    traces=[t for t in fig.data if " · plans" in t.name]
    assert len(traces)==3
    assert [t.name for t in traces]==[
        "Example Subject · plans","Community 1 · plans","Community 2 · plans"]
    assert [list(t.x) for t in traces]==[
        [1500,1800,2100],[1400,1900],[1700,2000]]
    assert all(t.mode=="lines+markers" for t in traces)
    assert traces[0].marker.symbol=="star"
    assert all(t.marker.symbol=="square" for t in traces[1:])
    annotations=[a.text for a in fig.layout.annotations]
    assert sum("Example Subject" in name for name in annotations)<=1
    assert all("Small" not in name and "Mid" not in name for name in annotations)


def test_single_plan_has_marker_without_connector_and_fallback_subject():
    draft={"name":"","subject_plans":[_plan("P1",1700,800000)]}
    fig,_,_=make_chart(_empty_market(),draft,[_plan("Only",1900,900000,4)],
                       "Price",6.5,20.)
    traces=[t for t in fig.data if " · plans" in t.name]
    assert [t.name for t in traces]==["Subject · plans","Community 4 · plans"]
    assert all(t.mode=="markers" for t in traces)


def test_invalid_plans_are_excluded_from_ladders():
    plans=[_plan("Zero",0,700000),_plan("Valid",1600,750000),
           _plan("Negative",1900,-1)]
    assert [p[2]["name"] for p in _sorted_plan_points(plans,"Price")]==["Valid"]


def test_mobile_chart_visual_hierarchy_emphasizes_communities(monkeypatch):
    """The background MLS trend must remain less prominent than new-home ladders."""
    import mobile_chart

    data=pd.DataFrame({
        "Standard Status":["Closed"]*3,
        "TMA Type":["SFD"]*3,
        "Living Area":[1400,1700,2000],
        "Close Price":[700000,780000,870000],
        "Price per Sq Ft":[500.,458.82,435.],
        "List Price":[710000,790000,880000],
    })
    result=SimpleNamespace(available=True,mls=data)
    monkeypatch.setattr(mobile_chart,"analyze_tma_outliers",
                        lambda group:(group,{"auto_excluded":0}))
    monkeypatch.setattr(mobile_chart,"fit_resale_trend",
                        lambda tagged:{"sf_min":1400,"sf_max":2000,
                                       "automatic_pricing_eligible":True})
    monkeypatch.setattr(mobile_chart,"trend_points",
                        lambda model,start,end:pd.DataFrame({
                            "SF":[1400,2000],"Price":[700000,870000]}))

    subject={"name":"Sample Subject","subject_plans":[
        _plan("Small",1500,730000),_plan("Large",1900,890000)]}
    competitor=[_plan("One",1500,720000,4),_plan("Two",2000,900000,4)]
    fig,_,_=make_chart([{"name":"Local Market","result":result,"products":["SFD"]}],
                       subject,competitor,"Price",6.5,20.)
    tma=next(trace for trace in fig.data if trace.name=="Local Market · SFD")
    subject_line=next(trace for trace in fig.data if trace.name=="Sample Subject · plans")
    comp_line=next(trace for trace in fig.data if trace.name=="Community 4 · plans")
    assert tma.opacity < comp_line.opacity
    assert tma.line.width < comp_line.line.width < subject_line.line.width
    assert subject_line.marker.size >= 15
    assert comp_line.marker.size >= 11
    # Market legend stays part of Plotly (never a separate Streamlit block)
    # and is positioned below the axes to avoid covering the mobile modebar.
    assert fig.layout.legend.orientation=="h"
    assert fig.layout.legend.y<0
    assert fig.layout.legend.yanchor=="top"
    assert fig.layout.margin.b>=90
    assert len([t for t in fig.data if t.showlegend])>=1


def test_payment_chart_connects_independent_plan_ladders():
    subject={"name":"Field Subject","subject_plans":[
        _plan("Big",2150,900000),_plan("Small",1600,720000)]}
    comps=[_plan("B",2050,850000,1),_plan("A",1400,650000,1),
           _plan("C",1750,760000,2)]
    fig,_,_=make_chart(_empty_market(),subject,comps,"Payment",6.5,20.)
    assert not any(t.type=="bar" for t in fig.data)
    traces=[t for t in fig.data if " · plans" in t.name]
    assert [t.name for t in traces]==[
        "Field Subject · plans","Community 1 · plans","Community 2 · plans"]
    assert [list(t.x) for t in traces]==[[1600,2150],[1400,2050],[1750]]
    assert traces[0].mode=="lines+markers"
    assert traces[1].mode=="lines+markers"
    assert traces[2].mode=="markers"
    assert [round(float(y)) for y in traces[0].y]==[
        round(float(__import__("mobile_chart").monthly_payment(720000,6.5,20))),
        round(float(__import__("mobile_chart").monthly_payment(900000,6.5,20)))]
    assert fig.layout.yaxis.title.text=="Monthly P&I ($)"


def test_payment_uses_current_rate_and_down_payment():
    subject={"name":"Subject","subject_plans":[_plan("Small",1600,700000)]}
    first,_,_=make_chart(_empty_market(),subject,[],"Payment",6.5,20.)
    second,_,_=make_chart(_empty_market(),subject,[],"Payment",7.5,20.)
    a=next(t for t in first.data if t.name=="Subject · plans")
    b=next(t for t in second.data if t.name=="Subject · plans")
    assert b.y[0]>a.y[0]


def test_plotly_toolbar_zoom_keeps_pan_and_removes_selection_modes():
    from mobile_chart import _plotly_config
    cfg=_plotly_config()
    removed=cfg["modeBarButtonsToRemove"]
    assert set(["select2d","lasso2d","zoom2d","pan2d"]).issubset(removed)
    assert "zoomIn2d" not in removed
    assert "zoomOut2d" not in removed
    assert "resetScale2d" not in removed
    assert cfg["scrollZoom"] is False
    assert cfg["displayModeBar"] is True


def test_selected_price_player_trend_extends_dashed_beyond_support(monkeypatch):
    import mobile_chart
    data=pd.DataFrame({
        "Standard Status":["Closed"]*3,
        "TMA Type":["Attached"]*3,
        "Living Area":[1000,1500,2000],
        "Close Price":[500000,700000,900000],
        "Price per Sq Ft":[500.,466.67,450.],
        "List Price":[510000,710000,910000],
    })
    market={"name":"TMA Escondido","result":SimpleNamespace(available=True,mls=data),
            "products":["Attached"]}
    model={"sf_min":1000.,"sf_max":2000.,
           "intercept":100000.,"slope":400.,
           "automatic_pricing_eligible":True}
    monkeypatch.setattr(mobile_chart,"analyze_tma_outliers",
                        lambda frame:(frame.assign(Keep=True),{}))
    monkeypatch.setattr(mobile_chart,"fit_resale_trend",lambda frame:model)
    monkeypatch.setattr(mobile_chart,"trend_points",
                        lambda model,lo,hi:pd.DataFrame({"SF":[1000.,2000.],
                                                        "Price":[500000.,900000.]}))
    draft={"name":"Subject",
           "subject_plans":[_plan("Small",1500,0),_plan("Large",2500,0)],
           "pricing_reference_product":"Attached"}
    fig,_,notes=make_chart([market],draft,[],"Price",6.5,20.)
    extension=next(trace for trace in fig.data if
                   trace.name=="TMA Escondido · Attached · extrapolated")
    assert extension.line.dash=="dash"
    assert list(extension.x)==[2000.,2500.]
    assert list(extension.y)==[900000.,1100000.]
    assert any("extrapolated" in note.lower() for _,note in notes)

    draft["pricing_reference_product"]="SFD"
    fig,_,_=make_chart([market],draft,[],"Price",6.5,20.)
    assert not any("extrapolated" in (trace.name or "") for trace in fig.data)



def test_initial_chart_focus_ignores_extreme_mls_sizes():
    from mobile_chart import _set_default_focus
    import numpy as np
    sales=pd.DataFrame({
        "Standard Status":["Closed"]*103,
        "Living Area":list(np.linspace(900,3000,100))+[6500,8200,8800],
        "Close Price":list(np.linspace(450000,1000000,100))+[1500000,1850000,2200000],
    })
    fig=go.Figure()
    fig.add_trace(go.Scatter(x=sales["Living Area"],y=sales["Close Price"],mode="markers"))
    _set_default_focus(fig,[{"result":SimpleNamespace(available=True,mls=sales)}],
                       {"subject_plans":[_plan("Subject",1800,800000)]},[],
                       "Price",6.5,20.)
    lo,hi=fig.layout.xaxis.range
    assert 1000 < hi < 4500, (lo,hi)
    assert lo < 1800 < hi
    assert len(fig.data[0].x)==103  # data is never thrown away


def test_initial_focus_does_not_stretch_for_far_outlier_subject():
    from mobile_chart import _set_default_focus
    sales=pd.DataFrame({
        "Standard Status":["Closed"]*30,
        "Living Area":list(range(1100,2600,50)),
        "Close Price":list(range(450000,900000,15000)),
    })
    fig=go.Figure()
    _set_default_focus(fig,[{"result":SimpleNamespace(available=True,mls=sales)}],
                       {"subject_plans":[_plan("Extreme",9000,3000000)]},[],
                       "Price",6.5,20.)
    assert fig.layout.xaxis.range[1]<5000


def test_payment_chart_focus_on_plan_ladders_without_mls():
    subject={"subject_plans":[_plan("A",1400,650000),_plan("B",2000,900000)]}
    fig,_,_=make_chart(_empty_market(),subject,[],"Payment",6.5,20.)
    assert fig.layout.xaxis.range[0]<1400
    assert fig.layout.xaxis.range[1]>2000
    assert fig.layout.yaxis.range[1]>fig.layout.yaxis.range[0]
