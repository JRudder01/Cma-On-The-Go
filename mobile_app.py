"""CMA On The Go: mobile-first, warehouse-backed market viewer.

Production warehouse and desktop project data are strictly read-only in this release.
All mobile work-in-progress remains in this browser session.
"""
from __future__ import annotations
from datetime import date, timedelta
from math import asin, cos, radians, sin, sqrt
import hmac
import json

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.outlier_engine import analyze_tma_outliers
from core.trend_model import fit_resale_trend, trend_points
from data_access import configured, current_plans, list_projects, load_project, run_tma, search_communities, suggest_communities


def warehouse_ready() -> bool:
    """Use warehouse only on explicit Search/Open/Analyze actions."""
    return configured()

# Use native Streamlit layout rather than injected CSS; mobile app should not
# render raw style fragments on Streamlit Community Cloud.



def clean_error(exc: Exception) -> str:
    # Keep potentially sensitive SQL/host details out of public Streamlit UI.
    return f"Unable to retrieve this information ({type(exc).__name__}). Check the read-only connection and warehouse permissions."


def authorize() -> None:
    try:
        password = st.secrets.get("ACCESS_PASSWORD", "")
    except Exception:
        password = ""
    if not isinstance(password, str) or len(password) < 4:
        st.title("CMA On The Go")
        st.info("This app is not activated. An administrator must configure an access password in Streamlit Secrets before use.")
        st.caption("No shared warehouse connection is made and no market data is exposed.")
        st.stop()
    if st.session_state.get("authorized"):
        return
    st.title("CMA On The Go")
    st.caption("Private workspace access")
    with st.form("sign_in"):
        entry = st.text_input("Access passphrase", type="password")
        if st.form_submit_button("Unlock", use_container_width=True):
            if hmac.compare_digest(entry, password):
                st.session_state["authorized"] = True
                st.rerun()
            else:
                st.error("Incorrect passphrase.")
    st.stop()


def haversine(lat1, lon1, lat2, lon2):
    dl = radians(lat2-lat1)
    dn = radians(lon2-lon1)
    a = sin(dl/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dn/2)**2
    return 3958.7613*2*asin(min(1.0,sqrt(a)))


def subject_summary(draft):
    st.subheader(draft.get("name") or "Market snapshot")
    st.caption(f"{draft.get('mode','')} · {draft.get('start')} – {draft.get('end')} · MLS warehouse only")


def monthly_pi(price: float, annual_rate: float, down_pct: float = 20.0) -> float:
    principal = price * (1.0 - down_pct / 100.0)
    rate = annual_rate / 1200.0
    months = 360
    return principal / months if rate == 0 else principal * rate / (1 - (1 + rate) ** (-months))


def market_fig(frame: pd.DataFrame, models: dict, metric: str,
               subject: dict, plans: list[dict], rate: float = 6.5, down_pct: float = 20.) -> go.Figure:
    """Phone-first graphic: model lines + labeled communities; MLS dots as context."""
    fig=go.Figure()
    categories=subject.get("analysis_products") or ["SFD"]
    colors={"SFD":"#2563eb","Attached":"#7c3aed","Detached Condo":"#047857"}
    closed=frame[frame["Standard Status"].eq("Closed")].copy() if not frame.empty else pd.DataFrame()
    inventory=frame[frame["Standard Status"].isin(["Active","Pending"])].copy() if not frame.empty else pd.DataFrame()
    subject_plans=subject.get("subject_plans") or []
    if metric in ("Price","Price/SF"):
        pricefield="Close Price" if metric=="Price" else "Price per Sq Ft"
        if not closed.empty:
            fig.add_trace(go.Scattergl(
                x=closed["Living Area"],y=closed[pricefield],mode="markers",
                marker={"size":5,"color":"rgba(100,116,139,0.20)"},
                name="Closed MLS (background)",showlegend=False,
                hovertemplate="%{x:,.0f} SF · $%{y:,.0f}<extra>Closed MLS</extra>"))
        if not inventory.empty:
            inv_y=inventory["List Price"] if metric=="Price" else inventory["List Price"]/inventory["Living Area"].replace(0,np.nan)
            fig.add_trace(go.Scattergl(
                x=inventory["Living Area"],y=inv_y,mode="markers",
                marker={"size":6,"color":"rgba(217,119,6,0.19)"},
                name="Inventory (background)",showlegend=False,
                hovertemplate="%{x:,.0f} SF · $%{y:,.0f}<extra>Inventory asking</extra>"))
        for product in categories:
            model=models.get(product)
            if not model:
                continue
            line=trend_points(model,max(100.,float(model["sf_min"])*.9),float(model["sf_max"])*1.1)
            if line.empty:
                continue
            y=line["Price"] if metric=="Price" else line["Price"]/line["SF"]
            fig.add_trace(go.Scatter(
                x=line["SF"],y=y,mode="lines",name=f"{product} MLS trend",
                line={"color":colors.get(product,"#2563eb"),"width":4},
                hovertemplate="%{x:,.0f} SF · $%{y:,.0f}<extra>"+product+" trend</extra>"))
        for i,p in enumerate(subject_plans[:8]):
            x,y=float(p.get("sqft") or 0),float(p.get("base_price") or 0)
            if x<=0 or y<=0:
                continue
            label=str(p.get("name") or f"Subject {i+1}")
            fig.add_trace(go.Scatter(
                x=[x],y=[y if metric=="Price" else y/x],
                mode="markers+text",text=[label[:18]],textposition="top center",
                textfont={"size":11,"color":"#b91c1c"},
                marker={"symbol":"star","size":15,"color":"#dc2626","line":{"color":"white","width":1}},
                name=label,showlegend=False,
                hovertemplate="%{x:,.0f} SF · $%{y:,.0f}<extra>"+label+"</extra>"))
        grouped={}
        for p in plans:
            key=str(p.get("community_id") or "Other")
            grouped.setdefault(key,[]).append(p)
        for key, group in list(grouped.items())[:15]:
            points=[]
            for p in group:
                try:
                    x,y=float(p.get("sqft") or 0),float(p.get("base_price") or 0)
                    if x>0 and y>0:
                        points.append((x,y if metric=="Price" else y/x))
                except (ValueError,TypeError):
                    continue
            if not points:
                continue
            name=str(group[0].get("community_name") or group[0].get("name") or "New homes")
            xs=[p[0] for p in points]
            ys=[p[1] for p in points]
            fig.add_trace(go.Scatter(
                x=xs,y=ys,mode="markers",showlegend=False,
                marker={"size":11,"symbol":"square","color":"#d97706",
                        "line":{"color":"#ffffff","width":1.5}},
                hovertemplate="%{x:,.0f} SF · $%{y:,.0f}<extra>"+name+"</extra>"))
            anchor=len(points)//2
            fig.add_annotation(
                x=xs[anchor],y=ys[anchor],text=name[:22],showarrow=True,
                ax=0,ay=-24,arrowhead=0,arrowwidth=1,
                font={"size":11,"color":"#92400e"},bgcolor="rgba(255,255,255,0.92)",
                bordercolor="rgba(217,119,6,0.25)",borderpad=3)
        fig.update_xaxes(title="Home size (SF)",fixedrange=False,showgrid=True,gridcolor="#eef2f7")
        fig.update_yaxes(title="Price ($)" if metric=="Price" else "$ per SF",
                         tickprefix="$",fixedrange=False,showgrid=True,gridcolor="#eef2f7")
    elif metric=="Payment":
        candidates=[]
        for i,p in enumerate(subject_plans[:8]):
            value=float(p.get("base_price") or 0)
            if value>0:
                candidates.append((str(p.get("name") or f"Subject {i+1}"),monthly_pi(value,rate,down_pct),"#dc2626"))
        for p in plans[:20]:
            value=float(p.get("base_price") or 0)
            if value>0:
                label=f"{p.get('community_name') or 'New home'} · {p.get('name') or 'Plan'}"
                candidates.append((label[:28],monthly_pi(value,rate,down_pct),"#d97706"))
        if candidates:
            fig.add_trace(go.Bar(x=[x[0] for x in candidates],y=[x[1] for x in candidates],
                                 marker_color=[x[2] for x in candidates],
                                 text=[f"${x[1]:,.0f}" for x in candidates],
                                 textposition="outside",cliponaxis=False))
        fig.update_yaxes(title="Monthly principal + interest ($)",tickprefix="$",rangemode="tozero")
        fig.update_xaxes(tickangle=-35)
    else:
        if not frame.empty:
            counts=frame.groupby("Standard Status").size().reindex(["Active","Pending","Closed"],fill_value=0)
            fig.add_trace(go.Bar(x=counts.index,y=counts.values,marker_color=["#2563eb","#d97706","#94a3b8"],
                                 text=counts.values,textposition="outside"))
        fig.update_yaxes(title="MLS records",rangemode="tozero")
    fig.update_layout(
        height=460 if metric in ("Price","Price/SF") else 400,
        margin={"l":8,"r":8,"t":28,"b":28},template="plotly_white",
        legend={"orientation":"h","yanchor":"bottom","y":1.04,"x":0,"font":{"size":11}},
        font={"size":11},dragmode="zoom",hovermode="closest",
        paper_bgcolor="white",plot_bgcolor="white")
    return fig


@st.dialog("Expanded market view",width="large")
def expanded_chart(fig: go.Figure):
    st.plotly_chart(fig,use_container_width=True,config={"displaylogo":False,"scrollZoom":True})
    st.caption("Pinch or drag to zoom; double-tap / double-click to reset. Rotate your phone for a wider view.")


def market_lens(result, draft, plans):
    if not result.available:
        st.error("MLS assembly could not be completed. No market conclusion has been generated.")
        return
    df=result.mls
    st.caption("Read-only canonical MLS warehouse · no uploads or changes")
    if df.empty:
        st.warning("No eligible MLS records were found in this geography and period.")
        return
    products=draft.get("analysis_products") or [draft.get("analysis_product","SFD")]
    filtered=df[df["TMA Type"].isin(products)].copy()
    if filtered.empty:
        st.warning("There are no MLS records in the selected resale categories.")
        return
    sold=filtered[filtered["Standard Status"].eq("Closed")].copy()
    models={}
    excluded=0
    for product in products:
        group=sold[sold["TMA Type"].eq(product)].copy()
        if group.empty:
            continue
        try:
            tagged,diagnostics=analyze_tma_outliers(group)
            excluded+=int(diagnostics["auto_excluded"])
        except ValueError:
            tagged=group.assign(Keep=True)
        model=fit_resale_trend(tagged) if not tagged.empty else None
        if model:
            models[product]=model
    a,b,c=st.columns(3)
    a.metric("Closed sales",len(sold))
    b.metric("Inventory",int(filtered["Standard Status"].isin(["Active","Pending"]).sum()))
    c.metric("Trend models",len(models))
    st.caption("Trendlines are fitted separately for each resale category; never across incompatible products.")
    metric=st.radio("Market view",["Price/SF","Price","Payment","Inventory"],horizontal=True,key="market_chart_mode")
    rate=6.5
    down_pct=20.
    if metric=="Payment":
        rate=st.slider("Illustrative fixed mortgage rate (%)",3.0,12.0,6.5,0.125)
        down_pct=st.slider("Down payment (%)",0,50,20,5)
        st.caption("Current-price 30-year principal-and-interest illustrations only, not historical payment trends; excludes tax, insurance, HOA, CFD, MI and incentives.")
    fig=market_fig(filtered,models,metric,draft,plans,rate,down_pct)
    st.plotly_chart(fig,use_container_width=True,config={"displaylogo":False,"scrollZoom":True})
    if st.button("Expand chart",use_container_width=True):
        expanded_chart(fig)
    if metric in ("Price/SF","Price"):
        with st.expander("Chart key and community details",expanded=True):
            st.caption("Thick colored lines = resale trends · Red stars = subject plans · Orange squares = competitor plans · Faint dots = MLS records.")
            for p in products:
                m=models.get(p)
                if m:
                    quality="eligible" if m.get("automatic_pricing_eligible") else "descriptive only"
                    st.write(f"**{p} trend:** {int(m['row_count'])} retained sales · {quality}")
                else:
                    st.write(f"**{p} trend:** insufficient eligible closed sales")
            for community_id in dict.fromkeys(p.get("community_id") for p in plans):
                group=[p for p in plans if p.get("community_id")==community_id]
                if group:
                    name=str(group[0].get("community_name") or "Community")
                    st.write(f"**{name}:** "+", ".join(f"{p.get('name') or 'Plan'} · {p.get('sqft') or '?'} SF" for p in group[:8]))
            if draft.get("subject_plans"):
                st.write("**Subject:** "+", ".join(str(p.get("name") or "Plan") for p in draft["subject_plans"]))
    st.caption(f"Outlier screening flagged {excluded} closed records. No manual outlier editing in the mobile view.")
    for p in products:
        m=models.get(p)
        if m and not m.get("automatic_pricing_eligible"):
            st.warning(f"{p}: trend is descriptive only; automatic pricing not validated for this sample.")
    latest=result.stats.get("mls_latest_observed")
    if latest:
        age=(date.today()-latest).days
        st.caption(f"Last warehouse MLS observation: {latest} ({age} days ago).")
        if age>45:
            st.warning("Warehouse observations may be stale; verify current coverage before client use.")
    st.info("Preliminary field view; not yet certified to reproduce all desktop pricing adjustments.")

def saved_market_prefill(area: dict, project: dict) -> dict:
    """Extract a read-only desktop TMA definition for mobile MLS-only reanalysis."""
    mode = str(area.get("geography_mode") or "City + state")
    if mode not in ("City + state", "ZIP code", "Coordinates + radius"):
        raise ValueError("This desktop geography is not supported in mobile Quick Scan.")
    fields = {"mode": mode, "name": str(area.get("name") or "Market area"),
              "city": str(area.get("city") or project.get("subject_city") or ""),
              "state": str(area.get("state") or project.get("subject_state") or "CA"),
              "zip_code": str(area.get("zip_code") or ""),
              "center_coordinates": str(area.get("center_coordinates") or ""),
              "radius": float(area.get("radius") or 3),
              "start": str(area.get("tma_start") or ""),
              "end": str(area.get("tma_end") or "")}
    if mode == "ZIP code" and len(fields["zip_code"]) != 5:
        raise ValueError("The saved TMA does not contain a usable ZIP code.")
    if mode == "Coordinates + radius":
        parts = fields["center_coordinates"].split(",")
        if len(parts) != 2 or not all(part.strip() for part in parts):
            raise ValueError("The saved TMA does not contain usable center coordinates.")
    if mode == "City + state" and (not fields["city"] or not fields["state"]):
        raise ValueError("The saved TMA does not contain a usable city and state.")
    product_types = area.get("property_types") or []
    product_text = " ".join(str(v).lower() for v in product_types)
    if "single family" in product_text and not ("condo" in product_text or "townhouse" in product_text):
        fields["analysis_product"] = "SFD"
    elif "townhouse" in product_text and "single family" not in product_text:
        fields["analysis_product"] = "Attached"
    elif "condo" in product_text and "single family" not in product_text:
        fields["analysis_product"] = "Attached"
    else:
        fields["analysis_product"] = "SFD"
        fields["product_review_needed"] = True
    return fields


def project_view():
    st.subheader("Saved desktop projects")
    st.caption("Read-only: opening projects or copying TMA settings never changes the desktop workspace.")
    if not warehouse_ready():
        st.info("Configure the existing restricted read-only warehouse connection first.")
        return
    term = st.text_input("Search project or city", placeholder="Search saved analyses…")
    if st.button("Search saved projects", key="search_saved_projects", use_container_width=True):
        try:
            with st.spinner("Retrieving saved projects…"):
                st.session_state["saved_project_rows"] = list_projects(term)
                st.session_state["saved_project_last_query"] = term
                st.session_state.pop("saved_project_error", None)
        except Exception as exc:
            st.session_state["saved_project_error"] = type(exc).__name__
    if st.session_state.get("saved_project_error"):
        st.error("Project search could not retrieve data (" + st.session_state["saved_project_error"] + "). Other library searches may still work.")
    records = st.session_state.get("saved_project_rows", [])
    if not records:
        if "saved_project_last_query" in st.session_state:
            st.info("No saved projects matched that search. Clear the search box and search again to show all projects.")
        else:
            st.caption("Press Search saved projects to retrieve all available desktop projects.")
        return
    st.caption(f"{len(records)} matching saved project(s). Tap one to open its desktop snapshot.")
    with st.container(height=290, border=True):
        for saved in records:
            label = f"{saved['name']} · {saved.get('city') or 'Unknown city'}"
            if st.button(label, key=f"open_project_{saved['id']}", use_container_width=True):
                try:
                    with st.spinner("Opening saved analysis…"):
                        item = load_project(str(saved["id"]))
                        item["id"] = str(saved["id"])
                        st.session_state["desktop_project"] = item
                except Exception as exc:
                    st.error(clean_error(exc))
    item = st.session_state.get("desktop_project")
    if not item:
        return

    state = item.get("state_json") or {}
    project = state.get("project") or {}
    areas = state.get("market_areas") or []
    competitors = project.get("selected_community_ids") or []
    subject_plans = state.get("subject_plans") or []
    st.subheader(str(item.get("name") or "Saved analysis"))
    st.caption(f"Saved desktop snapshot · {item.get('updated_at') or 'Date unavailable'}")
    st.write(f"**Subject:** {project.get('subject_name') or 'Not specified'}")
    st.caption(f"Builder: {project.get('subject_builder') or 'Not specified'} · Product: {project.get('subject_product') or 'Not specified'}")
    a, b, c = st.columns(3)
    a.metric("TMAs", len(areas))
    b.metric("Competitors", len(competitors))
    c.metric("Subject plans", len(subject_plans))
    st.caption("Selected competitors are saved IDs from CMA O' Matic. Mobile searches current warehouse community/plan data.")
    if st.button("Copy subject and competitors into Quick Scan", key="copy_desktop_subject", use_container_width=True):
        st.session_state["draft_subject"] = {
            "name": str(project.get("subject_name") or ""),
            "sf": 0, "price": 0,
        }
        st.session_state["project_comm_ids"] = [int(v) for v in competitors if str(v).isdigit()]
        st.session_state["draft_subject_plans"] = [
            {"name": str(p.get("Plan/Offering") or f"Plan {i+1}"),
             "sqft": int(float(p.get("SF") or 0)),
             "base_price": int(float(p.get("Base Price") or 0))}
            for i, p in enumerate(subject_plans[:8]) if isinstance(p, dict) and p.get("Include", True)
        ]
        st.rerun()

    st.write("**Saved target market areas**")
    if not areas:
        st.info("This saved desktop project does not contain market areas.")
    for i, area in enumerate(areas):
        if not isinstance(area, dict):
            continue
        title = str(area.get("name") or f"Market area {i + 1}")
        mode = str(area.get("geography_mode") or "Unknown geography")
        with st.expander(f"{title} · {mode}", expanded=(len(areas) == 1)):
            st.caption(f"Saved analysis dates: {area.get('tma_start') or '?'} – {area.get('tma_end') or '?'}")
            st.caption(f"Saved source mode: {area.get('data_source_mode') or 'Unknown'}")
            if mode == "Coordinates + radius":
                st.write(f"Center: {area.get('center_coordinates') or 'Unspecified'} · Radius: {area.get('radius') or '?'} mi")
            elif mode == "ZIP code":
                st.write(f"ZIP: {area.get('zip_code') or 'Unspecified'}")
            else:
                st.write(f"{area.get('city') or 'Unspecified'}, {area.get('state') or ''}")
            if st.button("Use this TMA in Quick Scan", key=f"use_saved_area_{item.get('id','unknown')}_{i}", use_container_width=True):
                try:
                    st.session_state["scan_prefill"] = saved_market_prefill(area, project)
                    st.session_state["draft_subject"] = {
                        "name": str(project.get("subject_name") or ""),
                        "sf": 0, "price": 0,
                    }
                    st.session_state["project_comm_ids"] = [int(v) for v in competitors if str(v).isdigit()]
                    st.session_state["draft_subject_plans"] = [
                        {"name": str(p.get("Plan/Offering") or f"Plan {i+1}"),
                         "sqft": int(float(p.get("SF") or 0)),
                         "base_price": int(float(p.get("Base Price") or 0))}
                        for i, p in enumerate(subject_plans[:8]) if isinstance(p, dict) and p.get("Include", True)
                    ]
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
    st.info("Saved desktop analyses are reference snapshots. Quick Scan rebuilds a new result from currently available canonical MLS records; it does not reproduce custom MLS data, saved outlier edits, or historical pricing results.")

def library_view():
    st.subheader("Community library")
    st.caption("Search existing new-home communities. Search suggestions appear after two characters.")
    if not warehouse_ready():
        st.info("Configure the restricted read-only warehouse connection.")
        return
    search=st.text_input("Community, builder or city", placeholder="Try Fusion, Lennar, or Carson", key="community_library_query")
    if len(search.strip()) >= 2:
        try:
            proposals=suggest_communities(search,limit=5)
            if proposals:
                st.caption("Suggestions from the saved competitor database:")
                for proposal in proposals:
                    label=f"{proposal['name']} · {proposal['builder']} · {proposal.get('city') or ''}"
                    if st.button(label,key=f"library_suggest_{proposal['id']}",use_container_width=True):
                        st.session_state["community_search_rows"]=[proposal]
                        st.session_state["library_selected_ids"]=[int(proposal["id"])]
                        st.session_state[f"library_choose_{int(proposal['id'])}"]=True
                        st.rerun()
        except Exception as exc:
            st.caption("Suggestions temporarily unavailable; manual search remains available.")
    if st.button("Search community library",key="search_library",use_container_width=True):
        try:
            with st.spinner("Retrieving communities…"):
                st.session_state["community_search_rows"]=search_communities(search,limit=100)
                st.session_state.pop("community_search_error",None)
        except Exception as exc:
            st.session_state["community_search_error"]=type(exc).__name__
    if st.session_state.get("community_search_error"):
        st.error("Community search failed (" + st.session_state["community_search_error"] + ").")
    matches=st.session_state.get("community_search_rows",[])
    if not matches:
        st.caption("Search to see matching active communities.")
        return
    selected_ids=list(st.session_state.get("library_selected_ids",[]))
    st.caption(f"{len(matches)} matching communities. Tick up to 15 to inspect or use in Quick Scan.")
    with st.container(height=350,border=True):
        for record in matches:
            ident=int(record["id"])
            label=f"{record['name']} — {record['builder']} · {record.get('city') or ''}"
            tick=st.checkbox(label,value=ident in selected_ids,key=f"library_choose_{ident}")
            if tick and ident not in selected_ids and len(selected_ids)<15:
                selected_ids.append(ident)
            elif not tick and ident in selected_ids:
                selected_ids.remove(ident)
    st.session_state["library_selected_ids"]=selected_ids
    st.caption(f"Selected: {len(selected_ids)} / 15")
    if selected_ids:
        if st.button("Show selected community plans",key="load_library_plans",use_container_width=True):
            try:
                with st.spinner("Retrieving current plans…"):
                    st.session_state["community_plan_rows"]=current_plans(selected_ids)
            except Exception as exc:
                st.error(clean_error(exc))
        plans=st.session_state.get("community_plan_rows",[])
        if plans:
            display=pd.DataFrame(plans)
            cols=[c for c in ("community_name","name","sqft","base_price","last_verified_date") if c in display.columns]
            st.dataframe(display[cols],hide_index=True,use_container_width=True)
        if st.button("Use selected communities in Quick Scan",use_container_width=True):
            st.session_state["project_comm_ids"]=selected_ids[:15]
            st.success("Community IDs saved to this browser session; open Quick Scan to analyze them.")


def scan_view():
    st.subheader("Quick Market Scan")
    st.caption("Start with the MLS warehouse; adding a subject is optional.")
    if not warehouse_ready():
        st.info("Configure the read-only warehouse credential to enable live market scans.")
    subject=st.session_state.get("draft_subject") or {}
    prefill=st.session_state.get("scan_prefill") or {}
    if prefill:
        st.info(f"Saved TMA settings loaded: {prefill.get('name') or 'Market area'}. Review before running a fresh MLS-only scan.")
        if prefill.get("product_review_needed"):
            st.warning("Desktop TMA contains mixed product buckets. Choose the resale category to analyze; mobile does not combine them into one trend.")
    with st.expander("1 · Market area",expanded=True):
        geography_options=["City + state","ZIP code","Coordinates + radius"]
        mode=st.radio("Geography",geography_options,index=geography_options.index(prefill.get("mode","City + state")),horizontal=False)
        if mode=="City + state":
            city=st.text_input("City",value=prefill.get("city",st.session_state.get("scan_city","")))
            state=st.text_input("State",value=prefill.get("state","CA"),max_chars=2)
            location={"city":city,"state":state}
        elif mode=="ZIP code":
            zipcode=st.text_input("ZIP code",value=prefill.get("zip_code",""),max_chars=5)
            location={"zipCode":zipcode}
        else:
            center=st.text_input("Latitude, longitude",value=prefill.get("center_coordinates",""),placeholder="32.8384, -116.9739")
            radius=st.slider("Radius (miles)",0.5,15.0,min(15.,max(0.5,round(float(prefill.get("radius",3.0))*2)/2)),0.5)
            location={}
            try:
                lat,lon=[float(x.strip()) for x in center.split(",")]
                if -90<=lat<=90 and -180<=lon<=180:
                    location={"latitude":lat,"longitude":lon,"radius":radius}
            except (ValueError,TypeError):pass
        product_options=["SFD","Attached","Detached Condo"]
        st.caption("Resale categories · choose one or more")
        chosen_products=[
            category for category in product_options
            if st.checkbox(category, value=(category == prefill.get("analysis_product","SFD")),
                           key="scan_product_"+category.replace(" ","_").lower())
        ]
        saved_start = saved_end = None
        try:
            if prefill.get("start") and prefill.get("end"):
                saved_start=date.fromisoformat(prefill["start"])
                saved_end=date.fromisoformat(prefill["end"])
                if saved_start > saved_end:
                    saved_start = saved_end = None
        except (TypeError,ValueError):
            pass
        period_options=["Last 12 months","Last 6 months","Last 24 months","Custom dates"]
        if saved_start and saved_end:
            period_options.insert(0,"Saved TMA dates")
        period=st.radio("Closed-sale period",period_options,index=0 if saved_start else 0,horizontal=True)
        if period == "Saved TMA dates":
            start,end=saved_start,saved_end
        elif period == "Custom dates":
            first=date.today()-timedelta(days=365)
            start=st.date_input("From (closed sales)",value=first, key="custom_scan_start")
            end=st.date_input("Through (closed sales)",value=date.today(), key="custom_scan_end")
        else:
            days={"Last 6 months":183,"Last 12 months":365,"Last 24 months":730}[period]
            end=date.today()
            start=end-timedelta(days=days)
        st.caption(f"Historical sale window: {start} to {end}. Canonical MLS only.")
    with st.expander("2 · Subject (optional)",expanded=False):
        name=st.text_input("Subject community name",value=subject.get("name",""))
        st.caption("Enter individual subject plans. You can leave the subject blank for MLS-only scans.")
        previous_plans=st.session_state.get("draft_subject_plans") or []
        n_subject=st.number_input("Number of subject plans",min_value=0,max_value=8,value=min(8,len(previous_plans)),step=1)
        subject_plans=[]
        for i in range(int(n_subject)):
            prior=previous_plans[i] if i < len(previous_plans) else {}
            with st.expander(f"Subject plan {i+1}", expanded=(n_subject == 1)):
                plan_label=st.text_input(f"Plan {i+1} name", value=str(prior.get("name") or f"Plan {i+1}"), key=f"subject_plan_name_{i}")
                plan_sf=st.number_input(f"Plan {i+1} size (SF)",min_value=0,max_value=30000,step=50,value=int(prior.get("sqft") or 0), key=f"subject_plan_sf_{i}")
                plan_price=st.number_input(f"Plan {i+1} price ($)",min_value=0,max_value=100000000,step=10000,value=int(prior.get("base_price") or 0), key=f"subject_plan_price_{i}")
            if plan_sf > 0 and plan_price > 0:
                subject_plans.append({"name":plan_label,"sqft":plan_sf,"base_price":plan_price})
        sf=subject_plans[0]["sqft"] if subject_plans else 0
        price=subject_plans[0]["base_price"] if subject_plans else 0
    with st.expander("3 · Existing competitors",expanded=False):
        if warehouse_ready():
            q=st.text_input("Search communities",placeholder="Start typing a community or builder…",key="scan_comp_query")
            if len(q.strip()) >= 2:
                try:
                    proposals=suggest_communities(q,limit=5)
                    if proposals:
                        st.caption("Suggested communities:")
                        for p in proposals:
                            label=f"{p['name']} — {p['builder']} · {p.get('city') or ''}"
                            if st.button("Add " + label,key=f"comp_suggest_{p['id']}",use_container_width=True):
                                selected_ids=list(st.session_state.get("project_comm_ids") or [])
                                if int(p["id"]) not in selected_ids and len(selected_ids) < 15:
                                    selected_ids.append(int(p["id"]))
                                st.session_state["project_comm_ids"]=selected_ids
                                st.session_state[f"scan_comp_{int(p['id'])}"]=True
                                st.rerun()
                except Exception:
                    st.caption("Suggestions unavailable; manual search still works.")
            if st.button("Find competitors",key="find_competitors",use_container_width=True):
                try:
                    with st.spinner("Searching current communities…"):
                        st.session_state["competitor_search_rows"]=search_communities(q,limit=100)
                except Exception as exc:
                    st.error(clean_error(exc))
            found=list(st.session_state.get("competitor_search_rows",[]))
            if mode=="Coordinates + radius" and location and found:
                for p in found:
                    if p.get("latitude") is not None and p.get("longitude") is not None:
                        p["distance"]=haversine(location["latitude"],location["longitude"],float(p["latitude"]),float(p["longitude"]))
                found.sort(key=lambda x:x.get("distance",1e9))
            selected_ids=list(st.session_state.get("project_comm_ids") or [])
            with st.container(height=260,border=True):
                for p in found:
                    ident=int(p["id"])
                    label=f"{p['name']} — {p['builder']} · {p.get('city') or ''}"
                    if "distance" in p:
                        label+=f" · {p['distance']:.1f} mi"
                    checked=st.checkbox(label,value=ident in selected_ids,key=f"scan_comp_{ident}")
                    if checked and ident not in selected_ids and len(selected_ids)<15:
                        selected_ids.append(ident)
                    elif not checked and ident in selected_ids:
                        selected_ids.remove(ident)
            st.session_state["project_comm_ids"]=selected_ids[:15]
            st.caption(f"{len(st.session_state['project_comm_ids'])} existing communities selected · 15 maximum")
        else:
            st.caption("Configure the read-only warehouse to search competitors.")
    if st.button("Analyze market",type="primary",use_container_width=True):
        st.session_state["draft_subject"]={"name":name,"sf":sf,"price":price}
        st.session_state["draft_subject_plans"]=subject_plans
        if not warehouse_ready():
            st.warning("A read-only warehouse connection is required for live MLS analysis.")
        elif not chosen_products:
            st.warning("Choose at least one resale category.")
        elif start > end:
            st.warning("Analysis start date must be before the end date.")
        elif not location or (mode=="City + state" and (not city.strip() or not state.strip())) or (mode=="ZIP code" and len(location.get("zipCode",""))!=5):
            st.warning("Enter a valid market geography.")
        else:
            draft={"name":name,"sf":sf,"price":price,"mode":mode,"location":location,
                "analysis_product":chosen_products[0],"analysis_products":chosen_products,"start":start,"end":end,
                "subject_plans":subject_plans,
                "competitor_ids":list(st.session_state.get("project_comm_ids") or [])}
            try:
                with st.spinner("Reading canonical MLS warehouse…"):
                    assembled=run_tma(mode,location,chosen_products,start,end)
                    plans=current_plans(draft["competitor_ids"]) if draft["competitor_ids"] else []
                st.session_state["market_result"]=assembled
                st.session_state["market_draft"]=draft
                st.session_state["market_plans"]=plans
            except Exception as exc:
                st.error(clean_error(exc))
    if "market_result" in st.session_state:
        draft=st.session_state["market_draft"]
        subject_summary(draft)
        market_lens(st.session_state["market_result"],draft,st.session_state.get("market_plans",[]))
        st.download_button("Export scan settings (JSON)",
            data=json.dumps(draft,default=str,indent=2),
            file_name="cma_on_the_go_scan.json",mime="application/json",
            use_container_width=True)
        st.caption("JSON is a portable draft, not a synchronized desktop project. No project or warehouse data was written.")


authorize()
st.title("CMA On The Go")
st.caption("Mobile market intelligence · Companion to CMA O' Matic")
if configured():
    st.caption("Warehouse connection configured · available when you search, open a project, or analyze a market")
else:
    st.warning("Read-only warehouse not connected. Project and MLS views are unavailable.")
tabs=st.tabs(["📍 Quick Scan","📁 Projects","🏘️ Communities"])
with tabs[0]: scan_view()
with tabs[1]: project_view()
with tabs[2]: library_view()
st.divider()
st.caption("v0.1 field prototype · No MLS uploads · No database writes · No user-facing partner branding")
