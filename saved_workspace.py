"""Saved desktop projects in one mobile chart; only read-only warehouse reads."""
from __future__ import annotations
from datetime import date,timedelta
import streamlit as st
from data_access import list_projects,load_project,run_tma,current_plans
from mobile_chart import render_analysis


def saved_subject_plans(state):
    plans=[]
    for i,p in enumerate((state.get("subject_plans") or [])[:8]):
        if not isinstance(p,dict) or p.get("Include") is False:
            continue
        try:
            area=int(float(p.get("SF") or 0))
            price=int(float(p.get("Base Price") or 0))
        except (ValueError,TypeError):
            area=price=0
        plans.append({"name":str(p.get("Plan/Offering") or f"Plan {i+1}"),
                      "sqft":max(0,area),"base_price":max(0,price)})
    return plans


def saved_market_definition(area,project,item):
    """Never silently replace unsupported desktop source settings."""
    mode=str(area.get("geography_mode") or "")
    meta=area.get("meta") or {}
    if mode=="City + state":
        city=str(area.get("city") or project.get("subject_city") or item.get("city") or "")
        state=str(area.get("state") or project.get("subject_state") or item.get("state") or "CA")
        if not city:
            raise ValueError("Saved city missing")
        loc={"city":city,"state":state}
    elif mode=="ZIP code":
        zips=meta.get("zips") or []
        code=str(area.get("zip_code") or area.get("zip_filter") or (zips[0] if zips else ""))
        if not (len(code)==5 and code.isdigit()):
            raise ValueError("Saved ZIP missing or invalid")
        loc={"zipCode":code}
    elif mode=="Coordinates + radius":
        try:
            lat,lon=[float(v.strip()) for v in str(area.get("center_coordinates") or "").split(",")]
            radius=float(area.get("radius") or 3)
        except (ValueError,TypeError):
            raise ValueError("Saved coordinates missing") from None
        if not(-90<=lat<=90 and -180<=lon<=180 and 0<radius<=15):
            raise ValueError("Invalid saved center or radius")
        loc={"latitude":lat,"longitude":lon,"radius":radius}
    else:
        raise ValueError("Unsupported geography: "+(mode or "unknown"))
    try:
        start=date.fromisoformat(str(area.get("tma_start") or date.today()-timedelta(days=365)))
        end=date.fromisoformat(str(area.get("tma_end") or date.today()))
    except ValueError:
        raise ValueError("Invalid saved date") from None
    if start>end or (end-start).days>3650:
        raise ValueError("Unsupported saved date window")
    raw=area.get("property_types") or meta.get("property_types") or []
    products=[]
    for value in raw:
        token=str(value).casefold()
        if ("single family" in token or "sfd" in token or ("detached" in token and "condo" not in token)) and "SFD" not in products:
            products.append("SFD")
        if ("townhouse" in token or "attached" in token) and "Attached" not in products:
            products.append("Attached")
        if "detached condo" in token and "Detached Condo" not in products:
            products.append("Detached Condo")
    if not products:
        raise ValueError("Saved property categories not recognized")
    return mode,loc,products,start,end


def project_series(item):
    state=item.get("state_json") or {}
    project=state.get("project") or {}
    cache=st.session_state.setdefault("saved_project_market_cache",{})
    series=[];notes=[]
    for i,area in enumerate(state.get("market_areas") or []):
        label=str(area.get("name") or f"TMA {i+1}") if isinstance(area,dict) else f"TMA {i+1}"
        key=str(item.get("id"))+":"+str(i)
        if key not in cache:
            try:
                mode,loc,products,start,end=saved_market_definition(area,project,item)
                result=run_tma(mode,loc,products,start,end)
                if not result.available:
                    cache[key]={"error":"Warehouse assembly unavailable"}
                else:
                    cache[key]={"name":label,"result":result,"products":products}
            except Exception as exc:
                cache[key]={"error":str(exc) if isinstance(exc,ValueError) else type(exc).__name__}
        entry=cache[key]
        if "error" in entry:
            notes.append((label,entry["error"]))
        else:
            series.append(entry)
    return series,notes


def saved_projects_page():
    st.subheader("Saved Project")
    search=st.text_input("Search saved project or city",placeholder="Enter a project name",key="unified_project_search")
    a,b=st.columns(2)
    clicked=a.button("Search",key="unified_project_find",use_container_width=True)
    show_all=b.button("Show all",key="unified_project_all",use_container_width=True)
    if clicked or show_all:
        try:
            st.session_state["unified_project_rows"]=list_projects("" if show_all else search)
            st.session_state.pop("unified_project_error",None)
        except Exception as exc:
            st.session_state["unified_project_error"]=type(exc).__name__
    if st.session_state.get("unified_project_error"):
        st.error("Saved project search unavailable: "+st.session_state["unified_project_error"])
    rows=st.session_state.get("unified_project_rows") or []
    if rows:
        with st.expander(f"Projects ({len(rows)})",expanded=not st.session_state.get("unified_open_project")):
            with st.container(height=300,border=True):
                for p in rows:
                    if st.button(f"{p['name']} · {p.get('city') or 'Unknown city'}",
                                 key=f"unified_pick_{p['id']}",use_container_width=True):
                        try:
                            with st.spinner("Opening saved project…"):
                                item=load_project(str(p["id"]))
                            item["id"]=str(p["id"])
                            st.session_state["unified_open_project"]=item
                            st.session_state["saved_project_market_cache"]={}
                            st.session_state.pop("saved_project_competitor_plans",None)
                            st.rerun()
                        except Exception as exc:
                            st.error("Unable to open project: "+type(exc).__name__)
    item=st.session_state.get("unified_open_project")
    if not item:
        st.caption("Choose a desktop project to display all its supported TMAs together.")
        return
    state=item.get("state_json") or {}
    project=state.get("project") or {}
    areas=state.get("market_areas") or []
    competitors=[int(v) for v in project.get("selected_community_ids") or [] if str(v).isdigit()][:15]
    st.write(f"**{item.get('name') or 'Saved project'}**")
    st.caption(f"{len(areas)} desktop TMAs · {len(competitors)} selected communities")
    if st.button("Choose another project",key="unified_clear_project"):
        st.session_state.pop("unified_open_project",None)
        st.rerun()
    with st.spinner("Assembling saved market definitions…"):
        series,notes=project_series(item)
        if "saved_project_competitor_plans" not in st.session_state:
            try:
                st.session_state["saved_project_competitor_plans"]=current_plans(competitors) if competitors else []
            except Exception as exc:
                st.session_state["saved_project_competitor_plans"]=[]
                notes.append(("Competitors",type(exc).__name__))
    st.caption(f"{len(series)} of {len(areas)} saved TMAs successfully reconstructed.")
    if notes:
        with st.expander(f"TMA coverage notes ({len(notes)})",expanded=True):
            for label,note in notes:
                st.write(f"{label}: {note}")
    st.info("Combined chart uses current canonical MLS records. It is not an exact historical desktop snapshot: custom MLS files, manual exclusions and pricing adjustments are not stored in the same chart data.")
    render_analysis(series,{"subject_plans":saved_subject_plans(state)},st.session_state.get("saved_project_competitor_plans") or [],scope="Saved Project")
