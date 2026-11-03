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
from data_access import configured, connection_check, current_plans, list_projects, load_project, run_tma, search_communities


def warehouse_ready() -> bool:
    """Never run database reads just because a tab or expander was rendered."""
    return configured() and bool(st.session_state.get("warehouse_connection_verified", False))

st.markdown("""<style>
.block-container{max-width:900px;padding-top:1.25rem;padding-left:1rem;padding-right:1rem;padding-bottom:5rem}
h1{font-size:1.85rem!important;letter-spacing:-.045em} h2{font-size:1.3rem!important}
div[data-testid="stMetric"]{background:#f4f7fb;border:1px solid #e0e7f0;border-radius:12px;padding:12px}
div[data-testid="stVerticalBlock"] > div{min-width:0}
@media(max-width:640px){div[data-testid="stHorizontalBlock"]{gap:.45rem} .stPlotlyChart{margin-left:-6px;margin-right:-6px}}
</style>""", unsafe_allow_html=True)


def clean_error(exc: Exception) -> str:
    # Keep potentially sensitive SQL/host details out of public Streamlit UI.
    return f"Unable to retrieve this information ({type(exc).__name__}). Check the read-only connection and warehouse permissions."


def authorize() -> None:
    try:
        password = st.secrets.get("ACCESS_PASSWORD", "")
    except Exception:
        password = ""
    if not isinstance(password, str) or len(password) < 14:
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


def market_fig(frame: pd.DataFrame, model: dict | None, metric: str,
               subject: dict, plans: list[dict], rate: float = 6.5, down_pct: float = 20.) -> go.Figure:
    fig = go.Figure()
    closed = frame[frame["Standard Status"].eq("Closed")].copy() if not frame.empty else pd.DataFrame()
    inventory = frame[frame["Standard Status"].isin(["Active","Pending"])].copy() if not frame.empty else pd.DataFrame()
    if metric in ("Price", "Price/SF"):
        pricefield = "Close Price" if metric == "Price" else "Price per Sq Ft"
        fig.add_trace(go.Scatter(
            x=closed.get("Living Area", []), y=closed.get(pricefield, []), mode="markers",
            marker={"size":8,"color":"#64748b","opacity":0.65},
            name="Closed MLS", text=closed.get("Formatted Address", None),
            hovertemplate="%{x:,.0f} SF · $%{y:,.0f}<extra>Closed MLS</extra>"
        ))
        if not inventory.empty:
            inventory_y = inventory["List Price"] if metric=="Price" else inventory["List Price"]/inventory["Living Area"].replace(0,np.nan)
            fig.add_trace(go.Scatter(
                x=inventory["Living Area"],y=inventory_y,mode="markers",
                marker={"symbol":"diamond-open","size":10,"color":"#f59e0b"},
                name="Active / pending", hovertemplate="%{x:,.0f} SF · $%{y:,.0f}<extra>Inventory asking</extra>"
            ))
        if model:
            line = trend_points(model,max(100.,float(model["sf_min"])*.90),
                                float(model["sf_max"])*1.10)
            if not line.empty:
                y=line["Price"] if metric=="Price" else line["Price"]/line["SF"]
                fig.add_trace(go.Scatter(x=line["SF"],y=y,mode="lines",
                    line={"color":"#2563eb","width":3},name="Robust MLS trend"))
        sf=float(subject.get("sf") or 0)
        price=float(subject.get("price") or 0)
        if sf>0 and price>0:
            fig.add_trace(go.Scatter(x=[sf],y=[price if metric=="Price" else price/sf],
                mode="markers",name="Subject",marker={"symbol":"star","size":15,"color":"#dc2626"}))
        for p in plans[:35]:
            try:
                x,y=float(p["sqft"]),float(p["base_price"])
                if x<=0 or y<=0: continue
                fig.add_trace(go.Scatter(x=[x],y=[y if metric=="Price" else y/x],
                    mode="markers",name=str(p.get("name") or "New home plan"),
                    marker={"symbol":"square","size":8,"color":"#0d9488"},
                    showlegend=False,hovertemplate="%{x:,.0f} SF · $%{y:,.0f}<extra>Current new-home plan</extra>"))
            except (ValueError, TypeError): continue
        fig.update_xaxes(title="Living area (SF)",fixedrange=False)
        fig.update_yaxes(title="Price ($)" if metric=="Price" else "Price per SF ($)",tickprefix="$",fixedrange=False)
    elif metric == "Payment":
        candidates = []
        subject_price = float(subject.get("price") or 0)
        if subject_price > 0:
            candidates.append(("Subject", monthly_pi(subject_price,rate,down_pct)))
        for p in plans[:15]:
            try:
                price = float(p["base_price"])
                if price > 0:
                    candidates.append((str(p.get("name") or "Plan")[:24], monthly_pi(price,rate,down_pct)))
            except (TypeError,ValueError): pass
        if candidates:
            fig.add_trace(go.Bar(x=[x[0] for x in candidates],y=[x[1] for x in candidates],
                                 marker_color=["#dc2626" if x[0]=="Subject" else "#0d9488" for x in candidates],
                                 name="P&I estimate"))
        fig.update_yaxes(title="Monthly principal + interest ($)",tickprefix="$",rangemode="tozero")
        fig.update_xaxes(tickangle=-25)
    else:
        if not frame.empty:
            counts = frame.groupby("Standard Status").size().reindex(["Active","Pending","Closed"],fill_value=0)
            fig.add_trace(go.Bar(x=counts.index,y=counts.values,marker_color=["#2563eb","#f59e0b","#64748b"],name="Listings"))
        fig.update_yaxes(title="Number of MLS records",rangemode="tozero")
    fig.update_layout(
        margin=dict(l=10,r=10,t=14,b=10),height=370,template="plotly_white",
        legend=dict(orientation="h",yanchor="bottom",y=1.02,x=0,font=dict(size=10)),
        font=dict(size=11),dragmode="zoom",hovermode="closest"
    )
    return fig


@st.dialog("Expanded market view",width="large")
def expanded_chart(fig: go.Figure):
    st.plotly_chart(fig,use_container_width=True,config={"displaylogo":False,"scrollZoom":True})
    st.caption("Pinch or drag to zoom; double-tap / double-click to reset. Rotate your phone for a wider view.")


def market_lens(result, draft, plans):
    if not result.available:
        st.error("MLS assembly could not be completed. No market conclusion has been generated.")
        return
    df = result.mls
    st.caption("Data source: existing CRMLS warehouse • No uploads or warehouse changes")
    if df.empty:
        st.warning("No eligible MLS records were found in this geography and period. Do not infer a market trend.")
        return
    sold = df[df["Standard Status"].eq("Closed")].copy()
    kept_frames=[]
    excluded=0
    if not sold.empty and "TMA Type" in sold.columns:
        for product, part in sold.groupby("TMA Type"):
            if product not in ("SFD","Attached","Detached Condo"): continue
            try:
                tagged,diagnostics=analyze_tma_outliers(part)
                kept_frames.append(tagged)
                excluded+=int(diagnostics["auto_excluded"])
            except ValueError:
                kept_frames.append(part.assign(Keep=True))
    retained=pd.concat(kept_frames,ignore_index=True) if kept_frames else pd.DataFrame()
    # Individual models must not mix incompatible product types.
    chosen=draft.get("analysis_product","SFD")
    fit_rows=retained[retained["TMA Type"].eq(chosen)].copy() if not retained.empty else pd.DataFrame()
    model=fit_resale_trend(fit_rows) if not fit_rows.empty else None
    a,b,c=st.columns(3)
    a.metric("Closed sales",int(len(sold)))
    b.metric("Inventory",int(df["Standard Status"].isin(["Active","Pending"]).sum()))
    c.metric("Model eligible",int(model["row_count"]) if model else 0)
    st.caption(f"Selected resale model: {chosen}. Outlier flags exclude {excluded} records across categories. No manual outlier review is available in mobile v0.1.")
    metric=st.segmented_control("Chart",["Price","Price/SF","Inventory","Payment"],default="Price/SF")
    metric=metric or "Price/SF"
    # Keep visualization restricted to chosen product so one trend is not paired with other product groups.
    filtered=df[df["TMA Type"].eq(chosen)].copy()
    rate=6.5
    down_pct=20.0
    if metric=="Payment":
        rate=st.slider("Illustrative fixed mortgage rate (%)",3.0,12.0,6.5,0.125)
        down_pct=st.slider("Down payment (%)",0,50,20,5)
        st.caption("Current-price, 30-year principal-and-interest estimates only; NOT an historical payment trend, and excludes taxes, HOA, CFD, insurance, mortgage insurance and incentives.")
    fig=market_fig(filtered,model,metric,draft,plans,rate,down_pct)
    st.plotly_chart(fig,use_container_width=True,config={"displaylogo":False,"scrollZoom":True})
    if st.button("⛶ Expand chart",use_container_width=True):
        expanded_chart(fig)
    if model:
        if not model.get("automatic_pricing_eligible"):
            st.warning("A trend was fitted, but automatic pricing is not validated for this sample. Treat it as descriptive only.")
        else:
            st.success(f"Trend eligible for review · {model.get('pricing_reliability','Caution')} model reliability")
        sf=float(draft.get("sf") or 0)
        if sf>0:
            predicted=float(model["slope"])*sf+float(model["intercept"])
            if model["sf_min"]<=sf<=model["sf_max"] and predicted>0:
                st.metric("Modeled resale price at subject size",f"${predicted:,.0f}")
                st.caption("Unadjusted resale relationship; not a validated new-home price recommendation.")
            else:
                st.info("Subject size is outside the retained trendline support or model output is invalid.")
    else:
        st.info("Insufficient retained closed sales or size variation for the desktop-compatible robust trendline (minimum 8 sales and 250 SF spread).")
    latest=result.stats.get("mls_latest_observed")
    if latest:
        age=(date.today()-latest).days
        st.caption(f"Last recorded MLS observation: {latest} ({age} days ago).")
        if age>45: st.warning("Warehouse observations may be stale. Confirm geographic and historical coverage before client use.")
    st.info("Preliminary field-view analysis. Desktop parity, MLS coverage completeness and pricing adjustments are not yet certified.")


def project_view():
    st.subheader("Saved desktop projects")
    st.caption("Read-only: loading a project will not normalize, update, or resave its desktop state.")
    if not warehouse_ready():
        st.info("Connect a restricted read-only warehouse credential to list existing projects.")
        return
    term=st.text_input("Search project or city",placeholder="Search saved analyses…")
    if st.button("Search saved projects", key="search_saved_projects"):
        try:
            with st.spinner("Retrieving saved projects…"):
                st.session_state["saved_project_rows"] = list_projects(term)
        except Exception as exc:
            st.error(clean_error(exc))
    records=st.session_state.get("saved_project_rows", [])
    if not records:
        st.caption("Use Search saved projects to load the matching list.")
        return
    labels={f"{p['name']} · {p.get('city') or 'Unknown city'} · {str(p['id'])[:8]}":p["id"] for p in records}
    pick=st.selectbox("Select project",list(labels))
    if st.button("Open snapshot",use_container_width=True):
        try:
            st.session_state["desktop_project"]=load_project(labels[pick])
        except Exception as exc:
            st.error(clean_error(exc))
    item=st.session_state.get("desktop_project")
    if item:
        state=item.get("state_json") or {}
        project=state.get("project",{})
        st.markdown(f"### {item.get('name','Saved analysis')}")
        c1,c2=st.columns(2)
        c1.metric("Subject",project.get("subject_name") or "Not specified")
        c2.metric("Selected communities",len(project.get("selected_community_ids") or []))
        st.write("Market areas")
        for area in state.get("market_areas",[]):
            st.write(f"• {area.get('name') or 'Market area'} — {area.get('geography_mode') or 'Unspecified geography'}")
        st.caption("View-only desktop project snapshot. Any embedded custom MLS records are not displayed here; a mobile warehouse analysis uses MLS-only canonical data.")
        if st.button("Use project's subject in Quick Scan"):
            st.session_state["draft_subject"]={"name":project.get("subject_name") or "",
                "sf":0,"price":0}
            st.session_state["project_comm_ids"]=project.get("selected_community_ids") or []
            st.success("Subject label and competitor selections copied into this session; open Quick Scan to configure the market.")


def library_view():
    st.subheader("Community library")
    st.caption("Search existing, active new-home communities. All plan data is read-only.")
    if not warehouse_ready():
        st.info("Connect the existing warehouse with a restricted read-only database credential.")
        return
    search=st.text_input("Community, builder or city",placeholder="Find a community…")
    if st.button("Search community library", key="search_library"):
        try:
            with st.spinner("Retrieving communities…"):
                st.session_state["community_search_rows"] = search_communities(search, limit=75)
        except Exception as exc:
            st.error(clean_error(exc))
    matches=st.session_state.get("community_search_rows", [])
    if not matches:
        st.caption("Use Search community library to load matching communities.")
        return
    opts={f"{x['name']} — {x['builder']} · {x.get('city') or ''}":x for x in matches}
    selected=st.multiselect("Select communities to inspect",list(opts),max_selections=15)
    if selected:
        chosen=[opts[key] for key in selected]
        for record in chosen:
            with st.expander(f"{record['name']} · {record['builder']}",expanded=len(chosen)==1):
                st.caption(f"{record.get('city') or ''}, {record.get('state') or ''} · {record.get('product_type') or 'Product unspecified'}")
                st.write(f"Recorded plans: {record.get('plan_count') or 0}")
        if st.button("Show selected community plans", key="load_library_plans"):
            try:
                with st.spinner("Retrieving current plans…"):
                    st.session_state["community_plan_rows"]=current_plans([p["id"] for p in chosen])
            except Exception as exc:
                st.error(clean_error(exc))
        plans=st.session_state.get("community_plan_rows", [])
        if plans:
            st.dataframe(pd.DataFrame(plans)[["community_id","name","sqft","base_price","last_verified_date"]],
                hide_index=True,use_container_width=True)
        if st.button("Use in Quick Scan",use_container_width=True):
            st.session_state["project_comm_ids"]=[int(x["id"]) for x in chosen]
            st.success("Competitor selections saved in this browser session.")


def scan_view():
    st.subheader("Quick Market Scan")
    st.caption("Start with the MLS warehouse; adding a subject is optional.")
    if not warehouse_ready():
        st.info("Warehouse connection is not yet configured. Build a scan definition below; live records will remain unavailable.")
    subject=st.session_state.get("draft_subject") or {}
    with st.expander("1 · Market area",expanded=True):
        mode=st.radio("Geography",["City + state","ZIP code","Coordinates + radius"],horizontal=False)
        if mode=="City + state":
            city=st.text_input("City",value=st.session_state.get("scan_city",""))
            state=st.text_input("State",value="CA",max_chars=2)
            location={"city":city,"state":state}
        elif mode=="ZIP code":
            zipcode=st.text_input("ZIP code",max_chars=5)
            location={"zipCode":zipcode}
        else:
            center=st.text_input("Latitude, longitude",placeholder="32.8384, -116.9739")
            radius=st.slider("Radius (miles)",0.5,15.0,3.0,0.5)
            location={}
            try:
                lat,lon=[float(x.strip()) for x in center.split(",")]
                if -90<=lat<=90 and -180<=lon<=180:
                    location={"latitude":lat,"longitude":lon,"radius":radius}
            except (ValueError,TypeError):pass
        chosen_product=st.radio("Resale category",["SFD","Attached","Detached Condo"],horizontal=True)
        period=st.radio("Closed-sale period",["Last 12 months","Last 6 months","Last 24 months"],index=0,horizontal=True)
        days={"Last 6 months":183,"Last 12 months":365,"Last 24 months":730}[period]
        end=date.today()
        start=end-timedelta(days=days)
        st.caption(f"Historical sale window: {start} to {end}. Canonical MLS only.")
    with st.expander("2 · Subject (optional)",expanded=False):
        name=st.text_input("Subject community name",value=subject.get("name",""))
        sf=st.number_input("Representative home size (SF)",min_value=0,max_value=30000,step=50,value=int(subject.get("sf") or 0))
        price=st.number_input("Representative asking price ($)",min_value=0,max_value=100000000,step=10000,value=int(subject.get("price") or 0))
    with st.expander("3 · Existing competitors",expanded=False):
        if warehouse_ready():
            q=st.text_input("Search communities",placeholder="Builder, community or city…")
            if st.button("Find competitors", key="find_competitors"):
                try:
                    with st.spinner("Searching current communities…"):
                        st.session_state["competitor_search_rows"]=search_communities(q,limit=100)
                except Exception as exc:
                    st.error(clean_error(exc))
            found=list(st.session_state.get("competitor_search_rows", []))
            try:
                if mode=="Coordinates + radius" and location and found:
                    for p in found:
                        if p.get("latitude") is not None and p.get("longitude") is not None:
                            p["distance"]=haversine(location["latitude"],location["longitude"],float(p["latitude"]),float(p["longitude"]))
                    found.sort(key=lambda x:x.get("distance",1e9))
                options={f"{p['name']} — {p['builder']} · {p.get('city') or ''}"+(f" · {p['distance']:.1f} mi" if "distance" in p else ""):int(p["id"]) for p in found}
                previous=set(int(x) for x in st.session_state.get("project_comm_ids",[]))
                defaults=[label for label,cid in options.items() if cid in previous]
                selected=st.multiselect("Choose existing communities",list(options),default=defaults,max_selections=15)
                st.session_state["project_comm_ids"]=[options[x] for x in selected]
            except Exception as exc: st.error(clean_error(exc))
        else: st.caption("Connect the read-only warehouse to search competitors.")
    if st.button("Analyze market",type="primary",use_container_width=True):
        st.session_state["draft_subject"]={"name":name,"sf":sf,"price":price}
        if not warehouse_ready():
            st.warning("A read-only warehouse connection is required for live MLS analysis.")
        elif not location or (mode=="City + state" and (not city.strip() or not state.strip())) or (mode=="ZIP code" and len(location.get("zipCode",""))!=5):
            st.warning("Enter a valid market geography.")
        else:
            draft={"name":name,"sf":sf,"price":price,"mode":mode,"location":location,
                "analysis_product":chosen_product,"start":start,"end":end,
                "competitor_ids":list(st.session_state.get("project_comm_ids") or [])}
            try:
                with st.spinner("Reading canonical MLS warehouse…"):
                    assembled=run_tma(mode,location,[chosen_product],start,end)
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
    st.info("Read-only warehouse settings found. Verify the connection before running market analyses.")
    if st.button("Check secure warehouse connection", key="warehouse_health", use_container_width=True):
        try:
            status = connection_check()
            st.session_state["warehouse_connection_verified"] = True
            st.success(f"Verified dedicated read-only account and SELECT access to {status['required_objects_checked']} required objects.")
        except Exception as exc:
            st.session_state["warehouse_connection_verified"] = False
            st.error(clean_error(exc))
            st.caption("Confirm the reader role, TLS connection string, and SELECT grants in the setup guide.")
else:
    st.warning("Read-only warehouse not connected. Project and MLS views are unavailable.")
tabs=st.tabs(["📍 Quick Scan","📁 Projects","🏘️ Communities"])
with tabs[0]: scan_view()
with tabs[1]: project_view()
with tabs[2]: library_view()
st.divider()
st.caption("v0.1 field prototype · No MLS uploads · No database writes · No user-facing partner branding")
