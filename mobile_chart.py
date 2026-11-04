"""One responsive chart for a quick scan or a multi-TMA saved project.

All data is supplied by read-only canonical MLS queries. This does not claim
to reproduce historical desktop chart snapshots or custom-MLS exclusions.
"""
from __future__ import annotations
from collections import OrderedDict
from math import isfinite

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from core.outlier_engine import analyze_tma_outliers
from core.trend_model import fit_resale_trend, trend_points

TMA_COLORS = ["#2563eb", "#7c3aed", "#0891b2", "#059669", "#db2777",
              "#ea580c", "#65a30d", "#475569", "#a21caf", "#0f766e"]
COMP_COLORS = ["#b45309", "#0f766e", "#9333ea", "#0369a1", "#be185d",
               "#4d7c0f", "#9a3412", "#475569"]
PRODUCT_DASH = {"SFD": "solid", "Attached": "dash", "Detached Condo": "dot"}


def monthly_payment(price: float, rate: float, down: float) -> float:
    principal=price*(1-down/100)
    r=rate/1200
    return principal/360 if r == 0 else principal*r/(1-(1+r)**-360)


def community_groups(plans: list[dict]) -> list[tuple[str, list[dict]]]:
    groups=OrderedDict()
    for p in plans:
        groups.setdefault(str(p.get("community_id") or "none"),[]).append(p)
    return list(groups.items())[:15]


def community_key(draft: dict, plans: list[dict], line_labels: list[tuple[str,str]]) -> None:
    """Names and series colors stay readable even in the expanded chart."""
    with st.container(border=True):
        st.markdown("**Chart key**")
        for name,color in line_labels:
            st.markdown(f":blue[━] {name}" if color=="#2563eb" else f"**━** {name}")
        subject=draft.get("subject_plans") or []
        for i,p in enumerate(subject):
            st.write(f"🔴 **S{i+1} · {p.get('name') or 'Subject plan'}** — "
                     f"{p.get('sqft') or '?'} SF · ${float(p.get('base_price') or 0):,.0f}")
        for i,(_,group) in enumerate(community_groups(plans)):
            st.write(f"🟧 **C{i+1} · {group[0].get('community_name') or 'New-home community'}** — "
                     + ", ".join(f"{p.get('name') or 'Plan'} · {p.get('sqft') or '?'} SF"
                                 for p in group[:6]))


def _positive_point(plan: dict, metric: str):
    try:
        x=float(plan.get("sqft") or 0)
        price=float(plan.get("base_price") or 0)
        if x>0 and price>0 and isfinite(x) and isfinite(price):
            return x, price if metric=="Price" else price/x
    except (TypeError,ValueError,ZeroDivisionError):
        pass
    return None


def _sorted_plan_points(rows: list[dict], metric: str) -> list[tuple[float,float,dict]]:
    """Valid plans in ascending home-size order; never interpolate absent plans."""
    points=[]
    for plan in rows:
        pt=_positive_point(plan,metric)
        if pt is not None:
            points.append((pt[0],pt[1],plan))
    return sorted(points,key=lambda point: point[0])


def _annotate_sparse(fig: go.Figure, points: list[tuple[float,float,str,bool]]) -> None:
    """Mobile-safe labels: compact names, bounded count, separated in chart pixels.

    Full community and plan names stay in the chart key below the graph.
    """
    if not points:
        return
    xs=[p[0] for p in points]
    ys=[p[1] for p in points]
    xmin,xmax=min(xs),max(xs)
    ymin,ymax=min(ys),max(ys)
    xspan=max(1600.0,xmax-xmin)
    yspan=max(550.0,ymax-ymin)
    placed=[]
    # Competitor names have priority; a few subject labels may fit afterward.
    for idx,(x,y,name,is_subject) in sorted(enumerate(points),key=lambda p:(p[1][3],p[0])):
        if len(placed)>=5:
            break
        label=str(name).strip()
        if is_subject and label.replace(",","").replace(" ","").isdigit():
            label=label.replace(",","") + " SF"
        if len(label)>18:
            label=label[:17]+"…"
        # Estimate text width in chart pixels, rather than measuring in data units.
        width=max(55,min(125,len(label)*6+12))
        anchor_x=(x-xmin)/xspan*580
        anchor_y=(y-ymin)/yspan*320
        choices=[(0,-42),(0,42),(85,-30),(-85,-30),(90,38),(-90,38)]
        selected=None
        for dx,dy in choices:
            cx=anchor_x+dx
            cy=anchor_y+dy
            if cx-width/2 < -8 or cx+width/2 > 588 or cy < -10 or cy > 330:
                continue
            if any(abs(cx-px)<(width+pw)/2+12 and abs(cy-py)<29
                   for px,py,pw in placed):
                continue
            selected=(dx,dy,cx,cy)
            break
        if selected is None:
            continue
        dx,dy,cx,cy=selected
        placed.append((cx,cy,width))
        fig.add_annotation(
            x=x,y=y,text=label,showarrow=True,
            ax=dx,ay=dy,arrowhead=0,arrowwidth=1,
            arrowcolor="#b91c1c" if is_subject else "#64748b",
            font=dict(size=10,color="#9f1239" if is_subject else "#1e293b"),
            bgcolor="rgba(255,255,255,0.94)",
            bordercolor="rgba(148,163,184,0.35)",borderpad=3
        )


def make_chart(series: list[dict], draft: dict, plans: list[dict],
               metric: str, rate: float, down: float):
    fig=go.Figure()
    trend_labels=[]
    diagnostics=[]
    point_labels=[]
    if metric in ("Price/SF","Price"):
        for i,entry in enumerate(series):
            label=str(entry["name"])
            result=entry["result"]
            if not result.available:
                diagnostics.append((label,"MLS assembly unavailable"))
                continue
            frame=result.mls
            if frame.empty:
                diagnostics.append((label,"No MLS rows"))
                continue
            color=TMA_COLORS[i % len(TMA_COLORS)]
            products=entry.get("products") or ["SFD"]
            sold=frame[frame["Standard Status"].eq("Closed")]
            inventory=frame[frame["Standard Status"].isin(["Active","Pending"])]
            ycol="Close Price" if metric=="Price" else "Price per Sq Ft"
            if not sold.empty:
                fig.add_trace(go.Scattergl(x=sold["Living Area"],y=sold[ycol],
                    mode="markers",marker=dict(size=4,color="rgba(100,116,139,0.12)"),
                    showlegend=False,hovertemplate=label+" · Closed MLS: %{x:,.0f} SF · $%{y:,.0f}<extra></extra>"))
            if not inventory.empty:
                yy=inventory["List Price"] if metric=="Price" else inventory["List Price"]/inventory["Living Area"].replace(0,pd.NA)
                fig.add_trace(go.Scattergl(x=inventory["Living Area"],y=yy,
                    mode="markers",marker=dict(size=5,color="rgba(217,119,6,0.12)"),
                    showlegend=False,hovertemplate=label+" · Active/pending: %{x:,.0f} SF · $%{y:,.0f}<extra></extra>"))
            for product in products:
                group=sold[sold["TMA Type"].eq(product)].copy()
                if group.empty:
                    diagnostics.append((f"{label} · {product}","No closed sales"))
                    continue
                try:
                    tagged,diag=analyze_tma_outliers(group)
                except ValueError:
                    tagged=group.assign(Keep=True)
                model=fit_resale_trend(tagged) if not tagged.empty else None
                if not model:
                    diagnostics.append((f"{label} · {product}","Insufficient valid sales for trend"))
                    continue
                trend=trend_points(model,max(100.,float(model["sf_min"])*.9),float(model["sf_max"])*1.1)
                if trend.empty:
                    continue
                yy=trend["Price"] if metric=="Price" else trend["Price"]/trend["SF"]
                trace_name=f"{label} · {product}"
                trend_labels.append((trace_name,color))
                fig.add_trace(go.Scatter(x=trend["SF"],y=yy,mode="lines",
                    name=trace_name,showlegend=True,
                    line=dict(color=color,width=3.5,dash=PRODUCT_DASH.get(product,"solid")),
                    hovertemplate=trace_name+": %{x:,.0f} SF · $%{y:,.0f}<extra></extra>"))
                if not model.get("automatic_pricing_eligible"):
                    diagnostics.append((trace_name,"Descriptive trend only; not certified for pricing"))
        # Community product ladders are straight connectors between listed
        # plans, not statistical trend models. Each community stays separate.
        subject_points=_sorted_plan_points((draft.get("subject_plans") or [])[:8],metric)
        subject_name=str(draft.get("name") or "").strip() or "Subject"
        if subject_points:
            fig.add_trace(go.Scatter(
                x=[p[0] for p in subject_points],
                y=[p[1] for p in subject_points],
                mode="lines+markers" if len(subject_points)>=2 else "markers",
                line=dict(color="#dc2626",width=2.5),
                marker=dict(symbol="star",size=15,color="#dc2626",
                            line=dict(color="white",width=1)),
                name=subject_name+" · plans",showlegend=False,
                text=[str(p[2].get("name") or f"Plan {j+1}")
                      for j,p in enumerate(subject_points)],
                customdata=[[float(p[2].get("base_price") or 0)] for p in subject_points],
                hovertemplate=subject_name+" · %{text}: %{x:,.0f} SF · $%{customdata[0]:,.0f}<extra></extra>"
            ))
            mid=subject_points[len(subject_points)//2]
            point_labels.append((mid[0],mid[1],subject_name,True))

        for i,(_,group) in enumerate(community_groups(plans)):
            pts=_sorted_plan_points(group,metric)
            if not pts:
                continue
            community_name=str(group[0].get("community_name") or f"Comp {i+1}")
            color=COMP_COLORS[i % len(COMP_COLORS)]
            fig.add_trace(go.Scatter(
                x=[p[0] for p in pts],
                y=[p[1] for p in pts],
                mode="lines+markers" if len(pts)>=2 else "markers",
                line=dict(color=color,width=1.8),
                opacity=.78,
                marker=dict(symbol="square",size=11,color=color,
                            line=dict(color="white",width=1)),
                name=community_name+" · plans",showlegend=False,
                text=[str(p[2].get("name") or f"Plan {j+1}")
                      for j,p in enumerate(pts)],
                customdata=[[float(p[2].get("base_price") or 0)] for p in pts],
                hovertemplate=community_name+" · %{text}: %{x:,.0f} SF · $%{customdata[0]:,.0f}<extra></extra>"
            ))
            mid=pts[len(pts)//2]
            point_labels.append((mid[0],mid[1],community_name,False))
        _annotate_sparse(fig,point_labels)
        fig.update_xaxes(title="Home size (SF)",gridcolor="#edf2f7")
        fig.update_yaxes(title="Price ($)" if metric=="Price" else "$ per SF",tickprefix="$",gridcolor="#edf2f7")
    elif metric=="Payment":
        labels=[];vals=[];colors=[]
        for i,p in enumerate((draft.get("subject_plans") or [])[:8]):
            try:
                val=float(p.get("base_price") or 0)
                if val>0:
                    labels.append(f"S{i+1} {p.get('name') or 'Subject'}");vals.append(monthly_payment(val,rate,down));colors.append("#dc2626")
            except (TypeError,ValueError):
                continue
        for i,(_,group) in enumerate(community_groups(plans)):
            for p in group[:4]:
                try:
                    val=float(p.get("base_price") or 0)
                    if val>0:
                        labels.append(f"C{i+1} {p.get('name') or 'Plan'}");vals.append(monthly_payment(val,rate,down));colors.append(COMP_COLORS[i % len(COMP_COLORS)])
                except (TypeError,ValueError):
                    continue
        fig.add_trace(go.Bar(x=labels,y=vals,marker_color=colors,text=[f"${v:,.0f}" for v in vals],textposition="outside"))
        fig.update_yaxes(title="Monthly P&I ($)",tickprefix="$",rangemode="tozero")
        fig.update_xaxes(tickangle=-30)
    else:
        for i,entry in enumerate(series):
            frame=entry["result"].mls
            if frame.empty:
                continue
            counts=frame["Standard Status"].value_counts()
            fig.add_trace(go.Bar(name=entry["name"],x=["Active","Pending","Closed"],
                y=[int(counts.get(k,0)) for k in ["Active","Pending","Closed"]],
                marker_color=TMA_COLORS[i % len(TMA_COLORS)]))
        fig.update_layout(barmode="group")
        fig.update_yaxes(title="MLS records")
    fig.update_layout(template="plotly_white",height=460 if metric in ("Price/SF","Price") else 390,
                      margin=dict(l=6,r=6,t=24,b=25),font=dict(size=11),
                      legend=dict(orientation="h",y=1.05,x=0,font=dict(size=10)),
                      hovermode="closest",dragmode="pan")
    return fig,trend_labels,diagnostics


@st.dialog("Expanded market view",width="large")
def expanded_chart(fig: go.Figure,draft: dict,plans: list[dict],lines):
    st.plotly_chart(fig,use_container_width=True,config={"displaylogo":False,"scrollZoom":False,"responsive":True,"modeBarButtonsToRemove":["zoom2d","pan2d"]})
    st.caption("One finger: pan the chart · Two fingers: pinch to zoom · Double-tap: reset.")
    community_key(draft,plans,lines)


def render_analysis(series: list[dict], draft: dict, plans: list[dict], *, scope: str):
    """Single shared chart surface regardless of analysis source."""
    st.subheader("Market Analysis")
    if not series:
        st.warning("No eligible market series were loaded. See TMA coverage notes above.")
        return
    st.caption(f"{scope} · {len(series)} TMA market series · warehouse read-only")
    metric=st.radio("Chart",["Price/SF","Price","Payment","Inventory"],horizontal=True,
                    key="shared_market_metric")
    rate=6.5;down=20.
    if metric=="Payment":
        rate=st.slider("Illustrative interest rate (%)",3.,12.,6.5,.125)
        down=st.slider("Down payment (%)",0,50,20,5)
        st.caption("30-year principal and interest only. Excludes HOA, taxes, insurance and incentives.")
    fig,lines,diagnostics=make_chart(series,draft,plans,metric,rate,down)
    st.plotly_chart(fig,use_container_width=True,config={"displaylogo":False,"scrollZoom":False,"responsive":True,"modeBarButtonsToRemove":["zoom2d","pan2d"]})
    st.caption("Drag to pan; pinch with two fingers to zoom. Double-tap or double-click to reset. Scroll outside the plot to move down the page.")
    if st.button("Expand chart",use_container_width=True,key="shared_expand_chart"):
        expanded_chart(fig,draft,plans,lines)
    community_key(draft,plans,lines)
    if diagnostics:
        with st.expander(f"Model and coverage notes ({len(diagnostics)})"):
            for name,note in diagnostics:
                st.caption(f"{name}: {note}")
    st.caption("Saved project charts are current MLS reconstructions, not byte-identical desktop snapshots.")
