"""Streamlit rerun regression tests.

The Streamlit frontend should be rebuilt whenever a widget changes. A simple
Python import of mobile_app caches the rendered UI and loses it on reruns.
"""
from streamlit.testing.v1 import AppTest
from pathlib import Path

APP_ENTRY = Path(__file__).resolve().parents[1] / "app.py"


def test_locked_page_is_rendered():
    app = AppTest.from_file(APP_ENTRY, default_timeout=30)
    app.run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert any("CMA On The Go" in header.value for header in app.title)


def test_full_app_rebuilds_on_reruns_without_database():
    app = AppTest.from_file(APP_ENTRY, default_timeout=30)
    app.secrets["ACCESS_PASSWORD"] = "local-test-only-password"
    app.session_state["authorized"] = True
    app.run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert any("Quick Market Scan" in header.value for header in app.subheader)

    # An ordinary UI rerun must not make the interface disappear.
    app.run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert any("Quick Market Scan" in header.value for header in app.subheader)

    # Changing a radio control must also rebuild the app, without Supabase.
    geography = next(r for r in app.radio if r.label == "Geography")
    geography.set_value("ZIP code").run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert any("Quick Market Scan" in header.value for header in app.subheader)
    assert any(t.label == "ZIP code" for t in app.text_input)


def test_quick_scan_custom_dates_multicategory_and_subject_plans():
    app = AppTest.from_file(APP_ENTRY, default_timeout=30)
    app.secrets["ACCESS_PASSWORD"] = "local-test-only-password"
    app.session_state["authorized"] = True
    app.run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert any(c.label == "SFD" for c in app.checkbox)
    assert any(c.label == "Attached" for c in app.checkbox)
    attach = next(c for c in app.checkbox if c.label == "Attached")
    attach.set_value(True).run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert next(c for c in app.checkbox if c.label == "Attached").value

    period = next(r for r in app.radio if r.label == "Closed-sale period")
    period.set_value("Custom dates").run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert any(d.label == "From (closed sales)" for d in app.date_input)
    assert any(d.label == "Through (closed sales)" for d in app.date_input)

    count = next(n for n in app.number_input if n.label == "Number of subject plans")
    count.set_value(2).run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert any(t.label == "Plan 1 name" for t in app.text_input)
    assert any(t.label == "Plan 2 name" for t in app.text_input)


def test_load_saved_workbook_automatically_opens_quick_scan_without_database():
    app=AppTest.from_file(APP_ENTRY,default_timeout=30)
    app.secrets["ACCESS_PASSWORD"]="local-test-only-password"
    # This URL is only a marker; no database connections are opened by this test.
    app.secrets["READONLY_DATABASE_URL"]="test-only-nonproduction-marker"
    app.session_state["authorized"]=True
    project_id="12345678-1234-1234-1234-123456789abc"
    saved={
        "id":project_id,"name":"Demo Project","city":"Carson",
        "state_json":{
            "project":{"subject_name":"Demo Subject","subject_city":"Carson",
                       "subject_state":"CA","selected_community_ids":[8,9]},
            "market_areas":[
                {"name":"Carson TMA","geography_mode":"City + state",
                 "city":"Carson","state":"CA","property_types":["Single Family"],
                 "tma_start":"2025-09-01","tma_end":"2026-08-31"},
                {"name":"Long Beach","geography_mode":"ZIP code","zip_code":"90805",
                 "property_types":["Single Family"]}],
            "subject_plans":[{"Include":True,"Plan/Offering":"Plan A",
                              "SF":1800,"Base Price":750000}]
        }
    }
    app.session_state["desktop_project"]=saved
    app.session_state["saved_project_rows"]=[{"id":project_id,"name":"Demo Project","city":"Carson"}]
    app.run()
    assert not app.exception, [str(e.message) for e in app.exception]
    nav=next(r for r in app.radio if r.label == "Workspace")
    nav.set_value("📁 Projects").run()
    assert not app.exception, [str(e.message) for e in app.exception]
    load=next(b for b in app.button if b.label == "Load in Quick Market Scan")
    load.click().run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert app.session_state["active_workspace"] == "📍 Quick Scan"
    assert app.session_state["scan_prefill"]["city"] == "Carson"
    assert app.session_state["project_comm_ids"] == [8,9]
    assert app.session_state["draft_subject_plans"][0]["name"] == "Plan A"
    assert any("Quick Market Scan" in title.value for title in app.subheader)
    assert next(n for n in app.number_input if n.label == "Number of subject plans").value == 1


def test_saved_project_multi_tma_view_is_lazy_and_switches(monkeypatch):
    """View all saved TMAs with only one on-demand warehouse assembly."""
    from types import SimpleNamespace
    import pandas as pd
    import data_access
    calls=[]
    def fake_tma(mode,location,products,start,end):
        calls.append((mode,dict(location)))
        return SimpleNamespace(available=True, mls=pd.DataFrame(), stats={})
    monkeypatch.setattr(data_access,"run_tma",fake_tma)
    monkeypatch.setattr(data_access,"current_plans",lambda ids: [])
    app=AppTest.from_file(APP_ENTRY,default_timeout=30)
    app.secrets["ACCESS_PASSWORD"]="local-test-only-password"
    app.secrets["READONLY_DATABASE_URL"]="test-only-nonproduction-marker"
    app.session_state["authorized"]=True
    app.session_state["desktop_project"]={
        "id":"12345678-1234-1234-1234-123456789abc",
        "name":"Demo Project","city":"Carson",
        "state_json":{
            "project":{"subject_name":"Demo Subject","subject_city":"Carson","selected_community_ids":[8]},
            "market_areas":[
                {"name":"Carson TMA","geography_mode":"City + state","city":"Carson","state":"CA",
                 "property_types":["Single Family"],"tma_start":"2025-09-01","tma_end":"2026-08-31"},
                {"name":"Long Beach TMA","geography_mode":"ZIP code","zip_code":"90805",
                 "property_types":["Single Family"],"tma_start":"2025-09-01","tma_end":"2026-08-31"}],
            "subject_plans":[{"Include":True,"Plan/Offering":"Plan A","SF":1800,"Base Price":750000}]
        }
    }
    app.session_state["saved_project_view_id"]="12345678-1234-1234-1234-123456789abc"
    app.run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert calls == []
    next(r for r in app.radio if r.label=="Workspace").set_value("📊 Project View").run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert len(calls)==1 and calls[0][0]=="City + state"
    app.run()
    assert len(calls)==1, "Re-render must not silently query MLS a second time"
    next(b for b in app.button if "Long Beach TMA" in b.label).click().run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert len(calls)==2 and calls[-1][0]=="ZIP code"
    assert app.session_state["active_workspace"] == "📊 Project View"
