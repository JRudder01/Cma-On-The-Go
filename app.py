"""Temporary isolated rendering test for Streamlit Community Cloud.

The complete app remains in mobile_app.py; the prior launcher is in launcher_backup.py.
This test imports only Streamlit, and neither accesses secrets nor connects to Supabase.
"""
import streamlit as st

st.set_page_config(page_title="CMA On The Go | Diagnostic", page_icon="🔎")
st.title("CMA On The Go")
st.success("Streamlit can render this application.")
st.write("Temporary connection diagnostic — no warehouse access or data changes.")
st.caption(f"Streamlit version: {st.__version__}")
if st.button("Test Streamlit interaction"):
    st.info("Button interaction received successfully.")
