"""End-to-end geography and validation checks for Quick Scan (mock warehouse)."""
from datetime import date,timedelta
from pathlib import Path
from types import SimpleNamespace
import pandas as pd
from streamlit.testing.v1 import AppTest
import quick_workspace

ENTRY=Path(__file__).resolve().parents[1]/"app.py"


def test_zip_radius_and_invalid_inputs_do_not_leak_stale_market(monkeypatch):
    calls=[]
    frame=pd.DataFrame({
        "Standard Status":["Closed","Closed","Active"],
        "TMA Type":["SFD","SFD","SFD"],
        "Living Area":[1300,1700,1600],
        "Close Price":[625000,760000,0],
        "List Price":[640000,780000,800000],
        "Price per Sq Ft":[480.77,447.06,0],
    })
    def fake_query(mode,loc,products,start,end):
        calls.append((mode,dict(loc),list(products),start,end))
        return SimpleNamespace(available=True,mls=frame,stats={})
    monkeypatch.setattr(quick_workspace,"configured",lambda:True)
    monkeypatch.setattr(quick_workspace,"run_tma",fake_query)

    app=AppTest.from_file(ENTRY,default_timeout=30)
    app.secrets["ACCESS_PASSWORD"]="mock-secret-not-production"
    app.session_state["authorized"]=True
    app.run()

    # Invalid city must never invoke warehouse query.
    next(w for w in app.button if w.label=="Analyze market").click().run()
    assert not calls
    assert not app.exception

    next(w for w in app.radio if w.label=="Geography").set_value("ZIP code").run()
    next(w for w in app.text_input if w.label=="ZIP code").set_value("90").run()
    next(w for w in app.button if w.label=="Analyze market").click().run()
    assert not calls

    next(w for w in app.text_input if w.label=="ZIP code").set_value("90640").run()
    next(w for w in app.button if w.label=="Analyze market").click().run()
    assert not app.exception
    assert calls[0][0]=="ZIP code"
    assert calls[0][1]=={"zipCode":"90640"}
    assert app.session_state["unified_quick_result"]["name"]=="TMA 90640"

    next(w for w in app.radio if w.label=="Geography").set_value("Coordinates + radius").run()
    assert not app.exception
    next(w for w in app.text_input if w.label=="Latitude, longitude").set_value("34.03, -118.11").run()
    next(w for w in app.slider if w.label=="Radius (miles)").set_value(2.5).run()
    # Previous ZIP TMA should remain cached but not be rendered for changed geography.
    assert app.session_state["unified_quick_result"]["name"]=="TMA 90640"
    next(w for w in app.button if w.label=="Analyze market").click().run()
    assert not app.exception
    assert len(calls)==2
    assert calls[1][0]=="Coordinates + radius"
    assert calls[1][1]=={"latitude":34.03,"longitude":-118.11,"radius":2.5}
    assert app.session_state["unified_quick_result"]["name"]=="TMA 2.5-mile radius"


def test_custom_date_reverse_range_prevents_warehouse_request(monkeypatch):
    calls=[]
    monkeypatch.setattr(quick_workspace,"configured",lambda:True)
    monkeypatch.setattr(quick_workspace,"run_tma",lambda *args:calls.append(args))
    app=AppTest.from_file(ENTRY,default_timeout=30)
    app.secrets["ACCESS_PASSWORD"]="mock-secret-not-production"
    app.session_state["authorized"]=True
    app.run()
    next(w for w in app.text_input if w.label=="City").set_value("Carson").run()
    next(w for w in app.radio if w.label=="Closed-sale period").set_value("Custom dates").run()
    d1=next(w for w in app.date_input if w.label=="Start date")
    d1.set_value(date(2026,10,1)).run()
    d2=next(w for w in app.date_input if w.label=="End date")
    d2.set_value(date(2026,9,1)).run()
    next(w for w in app.button if w.label=="Analyze market").click().run()
    assert not app.exception
    assert not calls
