"""Single-page CMA On The Go workbench, with one shared market chart."""
import streamlit as st
from data_access import configured
from saved_workspace import saved_projects_page
from quick_workspace import quick_scan_page, community_library_panel

st.title("CMA On The Go")
st.caption("Mobile market intelligence · Companion to CMA O' Matic")
if not configured():
    st.warning("Restricted warehouse connection is not yet configured.")

source=st.radio("Analysis source",["Quick Scan","Saved Project"],
                horizontal=True,key="analysis_source")
if source=="Quick Scan":
    quick_scan_page()
else:
    saved_projects_page()
st.divider()
st.caption("Read-only mobile analysis · No warehouse changes · Desktop results reproduced only when historical chart data is available")
