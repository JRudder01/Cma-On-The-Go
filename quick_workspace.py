"""One-TMA Quick Scan controls for the unified mobile market workbench."""
from __future__ import annotations
from datetime import date,timedelta
import json
import streamlit as st
from data_access import configured,run_tma,current_plans,search_communities,suggest_communities
from mobile_chart import render_analysis
from price_player import eligible_market_models, price_proposals

CATEGORIES=("SFD","Attached","Detached Condo")



def market_signature(mode, location, products, start, end):
    """Bind suggestions to the analysis, not whichever area is currently typed."""
    return json.dumps([mode,location,products,str(start),str(end)],sort_keys=True)


def subject_price_player(plans, signature):
    with st.expander("Price Player · % above/below TMA trendline"):
        if not plans:
            st.caption("Enter one or more subject plan sizes first.")
            return
        result=st.session_state.get("unified_quick_result") or {}
        if result.get("signature")!=signature:
            st.info("Run Analyze market to enable pricing from the current TMA.")
            return
        models=st.session_state.get("unified_quick_price_models") or {}
        if not models:
            st.warning("No selected TMA trends passed the automatic-pricing quality checks.")
            for product,reason in (st.session_state.get("unified_quick_price_warnings") or {}).items():
                st.caption(f"{product}: {reason}")
            return
        products=[p for p in result.get("products",[]) if p in models]
        choice=st.selectbox("Reference trendline",products,
            format_func=lambda p:f"{result['name']} · {p}",key="unified_player_category")
        offset=st.slider("Price relative to trend (%)",-25,25,0,1,key="unified_player_percent")
        model=models[choice]
        st.caption(f"Supported range: {model['sf_min']:,.0f}–{model['sf_max']:,.0f} SF "
                   f"· Reliability: {model.get('pricing_reliability','Eligible')}")
        proposals=price_proposals(plans,model,offset)
        rows=[]
        for p in proposals:
            rows.append({
                "Plan":p["name"],
                "SF":f"{p['sqft']:,.0f}",
                "Existing":f"${p['existing_price']:,.0f}" if p["existing_price"] else "—",
                "Trend":f"${p['trend_price']:,.0f}" if "trend_price" in p else "—",
                "Suggested":f"${p['target_price']:,.0f}" if "target_price" in p else p.get("reason","Unavailable"),
            })
        st.dataframe(rows,hide_index=True,use_container_width=True)
        blocked=any("target_price" not in p for p in proposals)
        if blocked:
            st.warning("At least one plan is outside the model's supported SF range. "
                       "No prices will be applied until every plan is eligible.")
        if st.button("Apply prices to subject plans",disabled=blocked,
                     use_container_width=True,type="primary",key="unified_player_apply"):
            st.session_state["unified_pending_plan_prices"]={
                plans[p["index"]]["input_index"]:int(p["target_price"]) for p in proposals
            }
            st.session_state["unified_player_applied"]=True
            st.rerun()
        st.caption("Uses the existing plan sizes and the validated market model. "
                   "No new MLS query or desktop-project changes.")


def subject_inputs(signature: str):
    name=st.text_input("Subject community name",key="unified_subject_name")
    count=st.number_input("Number of subject plans",0,8,value=0,step=1,key="unified_plan_count")
    pending=st.session_state.pop("unified_pending_plan_prices",{})
    for i,price in pending.items():
        st.session_state[f"unified_plan_price_{i}"]=int(price)
    plans=[]
    for i in range(int(count)):
        with st.expander(f"Plan {i+1}",expanded=count==1):
            label=st.text_input("Plan name",value=f"Plan {i+1}",key=f"unified_plan_name_{i}")
            sf=st.number_input("Home size (SF)",0,30000,step=50,key=f"unified_plan_sf_{i}")
            price=st.number_input("Asking price ($)",0,100000000,step=10000,key=f"unified_plan_price_{i}")
        if sf>0:
            plans.append({"name":label,"sqft":sf,"base_price":price,"input_index":i})
    subject_price_player(plans,signature)
    return name,plans


def competitor_inputs():
    query=st.text_input("Find a community or builder",placeholder="Try Fus or Lennar",key="unified_comp_query")
    current=list(st.session_state.get("unified_selected_competitors") or [])
    if len(query.strip())>=2:
        try:
            for p in suggest_communities(query,limit=5):
                if st.button(f"Add {p['name']} — {p['builder']}",
                             key=f"unified_suggestion_{p['id']}",use_container_width=True):
                    if int(p["id"]) not in current and len(current)<15:
                        current.append(int(p["id"]))
                    st.session_state["unified_selected_competitors"]=current
                    st.rerun()
        except Exception:
            st.caption("Suggestions are unavailable; manual search still works.")
    if st.button("Search competitors",key="unified_comp_search",use_container_width=True):
        try:
            st.session_state["unified_comp_search_rows"]=search_communities(query,limit=100)
        except Exception as exc:
            st.error(f"Competitor lookup unavailable ({type(exc).__name__}).")
    found=st.session_state.get("unified_comp_search_rows") or []
    if found:
        with st.container(height=250,border=True):
            for p in found:
                ident=int(p["id"])
                checked=st.checkbox(f"{p['name']} — {p['builder']} · {p.get('city') or ''}",
                                    value=ident in current,key=f"unified_comp_{ident}")
                if checked and ident not in current and len(current)<15:
                    current.append(ident)
                elif not checked and ident in current:
                    current.remove(ident)
    st.session_state["unified_selected_competitors"]=current[:15]
    st.caption(f"{len(current)} existing competitor communities selected")


def quick_tma_label(mode: str, location: dict) -> str:
    """Describe the actual geographic filter without claiming a city for a radius."""
    if mode == "City + state":
        return f"TMA {str(location.get('city') or '').strip()}"
    if mode == "ZIP code":
        return f"TMA {str(location.get('zipCode') or '').strip()}"
    if mode == "Coordinates + radius":
        radius = float(location["radius"])
        formatted = f"{radius:g}"
        return f"TMA {formatted}-mile radius"
    raise ValueError("Unsupported quick-scan geography")


def quick_scan_page():
    st.subheader("Quick Market Scan")
    st.caption("One TMA, any combination of resale categories, optional subject plans.")
    with st.expander("1 · Market area",expanded=True):
        mode=st.radio("Geography",["City + state","ZIP code","Coordinates + radius"],key="unified_geo_mode")
        if mode=="City + state":
            city=st.text_input("City",key="unified_city")
            state=st.text_input("State",value="CA",key="unified_state")
            location={"city":city,"state":state}
            valid=bool(city.strip() and state.strip())
        elif mode=="ZIP code":
            zipcode=st.text_input("ZIP code",max_chars=5,key="unified_zip")
            location={"zipCode":zipcode}
            valid=(len(zipcode)==5 and zipcode.isdigit())
        else:
            center=st.text_input("Latitude, longitude",key="unified_center")
            radius=st.slider("Radius (miles)",.5,15.,3.,.5,key="unified_radius")
            location={}
            valid=False
            try:
                lat,lon=[float(x.strip()) for x in center.split(",")]
                if -90<=lat<=90 and -180<=lon<=180:
                    location={"latitude":lat,"longitude":lon,"radius":radius}
                    valid=True
            except (TypeError,ValueError):
                pass
        st.caption("Resale categories · select multiple when needed")
        products=[p for p in CATEGORIES if st.checkbox(p,value=p=="SFD",
                                                        key="unified_product_"+p)]
        period=st.radio("Closed-sale period",["Last 12 months","Last 6 months","Last 24 months","Custom dates"],key="unified_period")
        if period=="Custom dates":
            start=st.date_input("Start date",value=date.today()-timedelta(days=365),key="unified_start")
            end=st.date_input("End date",value=date.today(),key="unified_end")
        else:
            days={"Last 12 months":365,"Last 6 months":183,"Last 24 months":730}[period]
            end=date.today()
            start=end-timedelta(days=days)
        st.caption(f"Canonical MLS period: {start} to {end}")
    signature=market_signature(mode,location,products,start,end)
    with st.expander("2 · Subject plans (optional)"):
        name,subject_plans=subject_inputs(signature)
    with st.expander("3 · Existing competitor communities"):
        competitor_inputs()
    if st.button("Analyze market",type="primary",key="unified_analyze",use_container_width=True):
        if not configured():
            st.warning("Configure the restricted warehouse connection.")
        elif not valid:
            st.warning("Enter a valid market geography.")
        elif not products:
            st.warning("Select at least one resale category.")
        elif start>end:
            st.warning("Start date must not be later than end date.")
        elif (end-start).days>3650:
            st.warning("This time period is too large for an on-the-go scan.")
        else:
            draft={"name":name,"subject_plans":subject_plans,
                   "analysis_products":products,
                   "competitor_ids":list(st.session_state.get("unified_selected_competitors") or [])}
            try:
                with st.spinner("Retrieving MLS transactions…"):
                    result=run_tma(mode,location,products,start,end)
                    plans=current_plans(draft["competitor_ids"]) if draft["competitor_ids"] else []
                models,warnings=eligible_market_models(result.mls,products) if result.available else ({},{p:"Warehouse unavailable" for p in products})
                st.session_state["unified_quick_result"]={"name":quick_tma_label(mode,location),"result":result,"products":products,"signature":signature}
                st.session_state["unified_quick_price_models"]=models
                st.session_state["unified_quick_price_warnings"]=warnings
                st.session_state["unified_quick_draft"]=draft
                st.session_state["unified_quick_plans"]=plans
                st.rerun()
            except Exception as exc:
                st.error("Market assembly unavailable ("+type(exc).__name__+").")
    if st.session_state.get("unified_quick_result") and st.session_state["unified_quick_result"].get("signature")!=signature:
        st.info("Market settings changed. Run Analyze market again to refresh the trendlines.")
    elif st.session_state.get("unified_quick_result"):
        render_analysis([st.session_state["unified_quick_result"]],
                        st.session_state.get("unified_quick_draft") or {},
                        st.session_state.get("unified_quick_plans") or [],
                        scope="Quick Scan")
        st.download_button("Export scan inputs (JSON)",
            data=json.dumps(st.session_state.get("unified_quick_draft") or {},default=str,indent=2),
            file_name="cma_on_the_go_scan.json",mime="application/json")


def community_library_panel():
    st.caption("Read-only lookup of existing new-home communities.")
    query=st.text_input("Community, builder or city",key="unified_library_query")
    if len(query.strip())>=2:
        try:
            matches=suggest_communities(query,limit=5)
            for p in matches:
                st.caption(f"Suggested: {p['name']} — {p['builder']} · {p.get('city') or ''}")
        except Exception:
            pass
    if st.button("Search community library",key="unified_library_search"):
        try:
            st.session_state["unified_library_rows"]=search_communities(query,limit=100)
        except Exception as exc:
            st.error("Library search unavailable: "+type(exc).__name__)
    for p in (st.session_state.get("unified_library_rows") or [])[:50]:
        st.write(f"**{p['name']}** — {p['builder']} · {p.get('city') or ''}")
