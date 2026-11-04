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
    assert any(d.label == "Start date" for d in app.date_input)
    assert any(d.label == "End date" for d in app.date_input)

    count = next(n for n in app.number_input if n.label == "Number of subject plans")
    count.set_value(2).run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert any(t.label == "Plan name" for t in app.text_input)
    assert any(t.label == "Plan name" for t in app.text_input)



def test_unified_source_switch():
    app=AppTest.from_file(APP_ENTRY,default_timeout=30)
    app.secrets["ACCESS_PASSWORD"]="test-not-production"
    app.secrets["READONLY_DATABASE_URL"]="local-no-connection"
    app.session_state["authorized"]=True
    app.run()
    assert not app.exception
    source=next(r for r in app.radio if r.label=="Analysis source")
    source.set_value("Saved Project").run()
    assert not app.exception
    assert any(t.label=="Search saved project or city" for t in app.text_input)
    source=next(r for r in app.radio if r.label=="Analysis source")
    source.set_value("Quick Scan").run()
    assert not app.exception
    assert any(h.value=="Quick Market Scan" for h in app.subheader)
