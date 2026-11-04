"""Pre-colleague POC walkthrough through main workflows with a mocked read-only warehouse.

No network/production credentials are used by these tests.
"""
from datetime import date
from pathlib import Path
from types import SimpleNamespace
import pandas as pd
from streamlit.testing.v1 import AppTest
import quick_workspace
import saved_workspace

ENTRY=Path(__file__).resolve().parents[1]/"app.py"


def market_data():
    rows=[]
    for category in ("SFD","Attached"):
        for n in range(22):
            sf=(1400 if category=="SFD" else 900)+50*n
            price=300000+sf*(390 if category=="SFD" else 415)
            rows.append({
                "TMA Type":category,
                "Standard Status":"Closed" if n<18 else ("Active" if n%2 else "Pending"),
                "Living Area":sf,"Close Price":price,"List Price":price*1.02,
                "Price per Sq Ft":price/sf,
            })
    return pd.DataFrame(rows)


def new_app():
    a=AppTest.from_file(ENTRY,default_timeout=35)
    a.secrets["ACCESS_PASSWORD"]="CI-access-only-not-production"
    a.session_state["authorized"]=True
    a.run()
    assert not a.exception,[str(v.message) for v in a.exception]
    return a


def _error_free(app):
    assert not app.exception,[str(v.message) for v in app.exception]


def test_quick_scan_all_categories_with_competitor_and_price_player_no_repeat_db_read(monkeypatch):
    observed=[]
    frame=market_data()
    def fake_assembly(mode,location,products,start,end):
        observed.append((mode,dict(location),list(products),start,end))
        return SimpleNamespace(available=True,mls=frame,stats={"mock":True})
    monkeypatch.setattr(quick_workspace,"configured",lambda:True)
    monkeypatch.setattr(quick_workspace,"run_tma",fake_assembly)
    monkeypatch.setattr(quick_workspace,"current_plans",lambda ids:[
        {"community_id":9,"community_name":"Fusion",
         "name":"Plan A","sqft":1700,"base_price":790000},
        {"community_id":9,"community_name":"Fusion",
         "name":"Plan B","sqft":1950,"base_price":850000},
    ])
    monkeypatch.setattr(quick_workspace,"suggest_communities",lambda q,limit=5:[
        {"id":9,"name":"Fusion","builder":"Lennar Homes","city":"El Cajon"}
    ])
    # The real model is independently covered in test_core/test_price_player.
    model={"automatic_pricing_eligible":True,"sf_min":1000,"sf_max":2400,
           "slope":400,"intercept":100000,"pricing_reliability":"Moderate"}
    monkeypatch.setattr(quick_workspace,"eligible_market_models",
                        lambda frame,products:({p:model for p in products},{}))
    a=new_app()
    next(v for v in a.text_input if v.label=="City").set_value("El Cajon").run()
    next(v for v in a.checkbox if v.label=="Attached").set_value(True).run()
    next(v for v in a.radio if v.label=="Closed-sale period").set_value("Custom dates").run()
    assert {v.label for v in a.date_input}>={"Start date","End date"}

    next(v for v in a.number_input if v.label=="Number of subject plans").set_value(2).run()
    sizes=[v for v in a.number_input if v.label=="Home size (SF)"]
    sizes[0].set_value(1600).run()
    sizes=[v for v in a.number_input if v.label=="Home size (SF)"]
    sizes[1].set_value(2000).run()
    next(v for v in a.text_input if v.label=="Find a community or builder").set_value("Fus").run()
    add=next(v for v in a.button if "Add Fusion" in v.label)
    add.click().run()
    assert a.session_state["unified_selected_competitors"]==[9]
    next(v for v in a.button if v.label=="Analyze market").click().run()
    _error_free(a)
    assert len(observed)==1
    assert observed[0][0]=="City + state"
    assert observed[0][1]["city"]=="El Cajon"
    assert observed[0][2]==["SFD","Attached"]
    assert a.session_state["unified_quick_result"]["name"]=="TMA El Cajon"
    assert len(a.session_state["unified_quick_plans"])==2
    assert "unified_quick_price_models" in a.session_state
    # Price Player should reuse the same TMA records when changing assumptions.
    choice=next(v for v in a.selectbox if v.label=="Reference trendline")
    choice.set_value("Attached").run()
    next(v for v in a.slider if v.label=="Price relative to trend (%)").set_value(5).run()
    assert len(observed)==1
    btn=next(v for v in a.button if v.label.startswith("Apply suggested prices"))
    assert not btn.disabled
    btn.click().run()
    _error_free(a)
    draft=a.session_state["unified_quick_draft"]
    assert [v["base_price"] for v in draft["subject_plans"]]==[777000,945000]
    assert draft["applied_pricing_reference"]["product"]=="Attached"
    assert draft["applied_pricing_reference"]["percent"]==5
    assert len(observed)==1
    for metric in ("Price","Payment","Inventory","Price/SF"):
        next(v for v in a.radio if v.label=="Chart").set_value(metric).run()
        _error_free(a)
        assert len(observed)==1


def test_saved_project_search_load_multiple_tmas_and_competitors(monkeypatch):
    frame=market_data()
    queries=[]
    monkeypatch.setattr(saved_workspace,"list_projects",lambda search:[
        {"id":"00000000-0000-4000-8000-000000000001",
         "name":"Internal Demo","city":"Carson"}])
    state={"project":{"subject_city":"Carson","subject_state":"CA",
                      "subject_name":"Test Development",
                      "selected_community_ids":[9]},
           "subject_plans":[{"Plan/Offering":"Plan A","SF":1600,"Base Price":850000}],
           "market_areas":[
               {"name":"Carson homes","geography_mode":"City + state",
                "city":"Carson","state":"CA",
                "property_types":["Single Family Residence"],
                "tma_start":"2025-11-04","tma_end":"2026-11-04"},
               {"name":"Carson townhomes","geography_mode":"ZIP code",
                "zip_code":"90745","property_types":["Townhouse"],
                "tma_start":"2025-11-04","tma_end":"2026-11-04"},
           ]}
    monkeypatch.setattr(saved_workspace,"load_project",
                        lambda identifier:{"name":"Internal Demo","city":"Carson",
                                           "state_json":state})
    def fake_market(mode,loc,products,start,end):
        queries.append((mode,loc,products))
        return SimpleNamespace(available=True,mls=frame,stats={"test":True})
    monkeypatch.setattr(saved_workspace,"run_tma",fake_market)
    monkeypatch.setattr(saved_workspace,"current_plans",lambda ids:[
        {"community_id":9,"community_name":"Fusion","name":"Plan A",
         "sqft":1700,"base_price":780000},
    ])
    a=new_app()
    next(v for v in a.radio if v.label=="Analysis source").set_value("Saved Project").run()
    next(v for v in a.button if v.label=="Search").click().run()
    _error_free(a)
    btn=next(v for v in a.button if "Internal Demo" in v.label)
    btn.click().run()
    _error_free(a)
    assert len(queries)==2,queries
    assert queries[0][0]=="City + state"
    assert queries[1][0]=="ZIP code"
    assert len(a.session_state["saved_project_market_cache"])==2
    assert len(a.session_state["saved_project_competitor_plans"])==1
    assert any("2 of 2" in v.value for v in a.get("caption"))
    # An ordinary widget rerun should not reissue the two MLS reads.
    next(v for v in a.radio if v.label=="Chart").set_value("Payment").run()
    _error_free(a)
    assert len(queries)==2
    next(v for v in a.button if v.label=="Choose another project").click().run()
    _error_free(a)
    assert "unified_open_project" not in a.session_state


def test_saved_project_rejects_unsupported_market_configuration():
    from saved_workspace import saved_market_definition
    import pytest
    with pytest.raises(ValueError,match="Unsupported geography"):
        saved_market_definition({"geography_mode":"Custom polygon"},
                                {},{})
    with pytest.raises(ValueError,match="Saved ZIP"):
        saved_market_definition({"geography_mode":"ZIP code","zip_code":"Bad"},
                                {},{})


def test_saved_multi_zip_tma_reconstructs_all_zips_without_database_writes(monkeypatch):
    from saved_workspace import saved_market_definition,_run_saved_market
    area={"geography_mode":"ZIP code","zip_code":"90806,90807",
          "meta":{"zips":["90806","90807"]},
          "property_types":["Condo"],
          "tma_start":"2025-11-04","tma_end":"2026-11-04"}
    mode,loc,products,start,end=saved_market_definition(area,{}, {})
    assert mode=="ZIP code"
    assert loc=={"zipCodes":["90806","90807"]}
    assert products==["Attached"]

    calls=[]
    def mock_run(mode,loc,products,start,end):
        calls.append((mode,loc["zipCode"],list(products)))
        code=loc["zipCode"]
        frame=pd.DataFrame({
            "Canonical Transaction ID":["tx-"+code],
            "Canonical Property ID":["property-"+code],
            "Canonical Listing ID":["listing-"+code],
            "Standard Status":["Closed"],
            "TMA Type":["Attached"],
            "Living Area":[1300],
            "Close Price":[700000],
            "Price per Sq Ft":[538.46],
            "List Price":[710000],
        })
        return SimpleNamespace(available=True,mls=frame)
    monkeypatch.setattr(saved_workspace,"run_tma",mock_run)
    result=_run_saved_market(mode,loc,products,start,end)
    assert result.available
    assert len(result.mls)==2
    assert result.stats["assembled_zip_count"]==2
    assert calls==[
        ("ZIP code","90806",["Attached"]),
        ("ZIP code","90807",["Attached"]),
    ]


def test_saved_zip_delimiters_reject_invalid_and_accept_valid_lists():
    from saved_workspace import saved_market_definition
    import pytest
    base={"geography_mode":"ZIP code","property_types":["Single Family"],
          "tma_start":"2025-11-04","tma_end":"2026-11-04"}
    mode,loc,_,_,_=saved_market_definition(
        {**base,"zip_code":"90806; 90807"}, {},{})
    assert loc=={"zipCodes":["90806","90807"]}
    with pytest.raises(ValueError,match="ZIP"):
        saved_market_definition({**base,"zip_code":"90806, ABC"}, {},{})
