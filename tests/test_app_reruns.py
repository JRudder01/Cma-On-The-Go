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
