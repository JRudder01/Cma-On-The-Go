"""Lightweight CMA On The Go launcher.

Keep the setup and access gate free of analytics dependencies and database calls.
The full app is imported only after authentication succeeds.
"""
from __future__ import annotations

import hmac

import streamlit as st

st.set_page_config(
    page_title="CMA On The Go",
    page_icon="📍",
    layout="centered",
    initial_sidebar_state="collapsed",
)


def get_access_password() -> str:
    try:
        value = st.secrets.get("ACCESS_PASSWORD", "")
    except Exception:
        return ""
    return value if isinstance(value, str) else ""


password = get_access_password()

if len(password) < 14:
    st.title("CMA On The Go")
    st.info("The application is installed, but access has not yet been configured.")
    st.write("Add a unique `ACCESS_PASSWORD` of at least 14 characters in this app's **Settings → Secrets**.")
    st.caption("No market data or production warehouse connection has been opened.")
    st.stop()

if not st.session_state.get("authorized", False):
    st.title("CMA On The Go")
    st.caption("Private workspace access")
    with st.form("access_gate"):
        submitted_password = st.text_input("Access passphrase", type="password")
        submitted = st.form_submit_button("Unlock", use_container_width=True)
    if submitted:
        if hmac.compare_digest(submitted_password, password):
            st.session_state["authorized"] = True
            st.rerun()
        else:
            st.error("Incorrect passphrase.")
    st.stop()

# Analytics imports and any optional database access occur only behind the gate.
from mobile_app import *  # noqa: E402,F401,F403
