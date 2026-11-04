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
