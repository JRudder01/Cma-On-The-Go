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
        choices=[(0,-42),(0,42),(85,-30),(-85,-30),(90,38),(-90,38),
                 (140,-52),(-140,-52),(145,52),(-145,52),
                 (0,-92),(0,92),(180,-15),(-180,-15)]
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
            if len(points)>5:
                continue
            # With only a subject and a few competitors, every community
            # deserves a chart label. Stagger remaining annotations instead
            # of silently discarding a tightly clustered community.
            dx=(-130 if idx % 2 else 130)
            dy=(-65 if idx % 3 else 65) + 23 * (idx // 3)
            selected=(dx,dy,anchor_x+dx,anchor_y+dy)
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



def _focus_axis(values, *, low_q: float = .10, high_q: float = .90):
    """Robust visible range, not full MLS extremes; never filters chart data."""
    import numpy as np
    values=np.asarray(list(values),dtype=float)
    values=values[np.isfinite(values) & (values>0)]
    if not len(values):
        return None
    if len(values)>=10:
        lo,hi=np.quantile(values,[low_q,high_q])
    else:
        lo,hi=float(values.min()),float(values.max())
    center=(float(lo)+float(hi))/2
    span=max(float(hi)-float(lo),max(1.,abs(center)*.12))
    padding=span*.13
    return (max(0.,center-span/2-padding),center+span/2+padding)


def _set_default_focus(fig: go.Figure, series: list[dict],
                       draft: dict, plans: list[dict],metric: str,
                       rate: float, down: float) -> None:
    """Start in the MLS concentration, not at extremes of a distant TMA.

    Subject/competitor points close to the main cluster are included; extreme
    remote plans remain accessible through the toolbar zoom-out/pan controls.
    No underlying sales, trend regressions, or Plotly traces are removed.
    """
    import numpy as np
    if metric=="Inventory":
        return
    x_sales=[]; y_sales=[]
    if metric in ("Price","Price/SF"):
        for entry in series:
            result=entry.get("result")
            if not getattr(result,"available",False):
                continue
            frame=getattr(result,"mls",None)
            if frame is None or frame.empty:
                continue
            if not {"Standard Status","Living Area","Close Price"}.issubset(frame.columns):
                continue
            closed=frame[frame["Standard Status"].eq("Closed")]
            sizes=pd.to_numeric(closed["Living Area"],errors="coerce")
            prices=pd.to_numeric(closed["Close Price"],errors="coerce")
            valid=sizes.gt(0)&prices.gt(0)&sizes.notna()&prices.notna()
            if valid.any():
                xs=sizes[valid].astype(float).to_numpy()
                ys=prices[valid].astype(float).to_numpy()
                x_sales.extend(xs.tolist())
                y_sales.extend((ys if metric=="Price" else ys/xs).tolist())
    # Keep the viewport based on the main body of MLS sales.
    x_range=_focus_axis(x_sales) if x_sales else None
    plan_points=[]
    if metric in ("Price","Price/SF"):
        for p in (draft.get("subject_plans") or [])+plans:
            pt=_positive_point(p,metric)
            if pt is not None:
                plan_points.append(pt)
    elif metric=="Payment":
        for p in (draft.get("subject_plans") or [])+plans:
            try:
                sf=float(p.get("sqft") or 0)
                price=float(p.get("base_price") or 0)
                if sf>0 and price>0 and isfinite(sf) and isfinite(price):
                    plan_points.append((sf,monthly_payment(price,rate,down)))
            except (TypeError,ValueError,OverflowError,ZeroDivisionError):
                continue
    if x_range is None:
        x_range=_focus_axis((p[0] for p in plan_points),low_q=0.,high_q=1.)
    if x_range is None:
        return
    xlo,xhi=x_range
    span=xhi-xlo
    # Include nearby subject/competitor plans while avoiding an extreme single
    # plan forcing the full viewport to become unreadably wide.
    close_plans=[p for p in plan_points if xlo-.25*span<=p[0]<=xhi+.25*span]
    if close_plans:
        xlo=max(0.,min(xlo,min(p[0] for p in close_plans)-.07*span))
        xhi=max(xhi,max(p[0] for p in close_plans)+.07*span)
    if metric=="Payment":
        visible_y=[p[1] for p in plan_points if xlo<=p[0]<=xhi]
    else:
        visible_y=[y for x,y in zip(x_sales,y_sales) if xlo<=x<=xhi]
        visible_y+= [p[1] for p in close_plans]
    yrange=_focus_axis(visible_y,low_q=.05,high_q=.95)
    if yrange is None:
        yrange=_focus_axis((p[1] for p in plan_points))
    if yrange is None:
        fig.update_xaxes(range=[xlo,xhi])
        return
    ylo,yhi=yrange
    # Preserve the market-centered view, but show subject/comp prices that are
    # within the chosen home-size region even when they exceed MLS quantiles.
    for _,y in close_plans:
        if ylo<=y<=yhi:
            continue
        height=yhi-ylo
        if ylo-.4*height<=y<=yhi+.4*height:
            ylo=max(0.,min(ylo,y-.06*height))
            yhi=max(yhi,y+.06*height)
    fig.update_xaxes(range=[xlo,xhi])
    fig.update_yaxes(range=[ylo,yhi])



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
                    line=dict(color=color,width=1.8,dash=PRODUCT_DASH.get(product,"solid")),
                    opacity=0.60,
                    hovertemplate=trace_name+": %{x:,.0f} SF · $%{y:,.0f}<extra></extra>"))
                # Only extend the chosen Quick Scan pricing reference and only
                # when subject plans exceed the retained MLS support interval.
                # The extension is a prediction of the SAME fitted equation,
                # never a newly fitted trend or evidence of observed sales.
                if (len(series)==1 and
                    draft.get("pricing_reference_product")==product and
                    model.get("automatic_pricing_eligible")):
                    subject_sizes=[]
                    for p in draft.get("subject_plans") or []:
                        try:
                            sf=float(p.get("sqft") or 0)
                            if isfinite(sf) and sf>0:
                                subject_sizes.append(sf)
                        except (TypeError,ValueError):
                            continue
                    lo=float(model["sf_min"]); hi=float(model["sf_max"])
                    for boundary, outer in (
                        (lo, min((sf for sf in subject_sizes if sf<lo),default=lo)),
                        (hi, max((sf for sf in subject_sizes if sf>hi),default=hi)),
                    ):
                        if outer==boundary:
                            continue
                        coords=[boundary,outer]
                        prices=[float(model["intercept"])+float(model["slope"])*sf
                                for sf in coords]
                        if min(prices)<=0 or not all(isfinite(y) for y in prices):
                            diagnostics.append((trace_name,"Extrapolated segment has invalid predicted values"))
                            continue
                        y_values=(prices if metric=="Price" else
                                  [price/sf for price,sf in zip(prices,coords)])
                        fig.add_trace(go.Scatter(
                            x=coords,y=y_values,mode="lines",
                            name=trace_name+" · extrapolated",
                            showlegend=False,
                            line=dict(color=color,width=2,dash="dash"),
                            opacity=.68,
                            hovertemplate=trace_name+
                                "<br>Extrapolated beyond closed MLS support: "
                                "%{x:,.0f} SF · $%{y:,.0f}<extra></extra>"
                        ))
                    if any(sf<lo or sf>hi for sf in subject_sizes):
                        diagnostics.append((trace_name,
                            f"Dashed extensions beyond {lo:,.0f}–{hi:,.0f} SF are "
                            "extrapolated from the existing fitted equation, not supported by "
                            "closed MLS sales of those sizes."))
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
                line=dict(color="#dc2626",width=3.6),
                marker=dict(symbol="star",size=16,color="#dc2626",
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
                line=dict(color=color,width=3.0),
                opacity=.96,
                marker=dict(symbol="square",size=12,color=color,
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
        # Mobile payment view mirrors the community-ladder geometry used for
        # price charts. It is P&I only, not desktop all-in payment.
        subject_name=str(draft.get("name") or "").strip() or "Subject"
        subject_rows=(draft.get("subject_plans") or [])[:8]
        subject_pts=[]
        for p in subject_rows:
            try:
                sf=float(p.get("sqft") or 0)
                base=float(p.get("base_price") or 0)
                if sf>0 and base>0 and isfinite(sf) and isfinite(base):
                    subject_pts.append((sf,monthly_payment(base,rate,down),p))
            except (TypeError,ValueError,ZeroDivisionError,OverflowError):
                continue
        subject_pts.sort(key=lambda x:x[0])
        if subject_pts:
            fig.add_trace(go.Scatter(
                x=[p[0] for p in subject_pts],y=[p[1] for p in subject_pts],
                mode="lines+markers" if len(subject_pts)>1 else "markers",
                line=dict(color="#dc2626",width=3.6),
                marker=dict(symbol="star",size=16,color="#dc2626",
                            line=dict(color="white",width=1)),
                showlegend=False,name=subject_name+" · plans",
                text=[str(p[2].get("name") or "Plan") for p in subject_pts],
                hovertemplate=subject_name+" · %{text}<br>%{x:,.0f} SF · $%{y:,.0f}/mo P&I<extra></extra>"
            ))
            mid=subject_pts[len(subject_pts)//2]
            point_labels.append((mid[0],mid[1],subject_name,True))

        for i,(_,group) in enumerate(community_groups(plans)):
            pts=[]
            for p in group:
                try:
                    sf=float(p.get("sqft") or 0)
                    base=float(p.get("base_price") or 0)
                    if sf>0 and base>0 and isfinite(sf) and isfinite(base):
                        pts.append((sf,monthly_payment(base,rate,down),p))
                except (TypeError,ValueError,ZeroDivisionError,OverflowError):
                    continue
            pts.sort(key=lambda x:x[0])
            if not pts:
                continue
            label=str(group[0].get("community_name") or f"Comp {i+1}")
            color=COMP_COLORS[i % len(COMP_COLORS)]
            fig.add_trace(go.Scatter(
                x=[p[0] for p in pts],y=[p[1] for p in pts],
                mode="lines+markers" if len(pts)>1 else "markers",
                line=dict(color=color,width=3.0),opacity=.96,
                marker=dict(symbol="square",size=12,color=color,
                            line=dict(color="white",width=1)),
                showlegend=False,name=label+" · plans",
                text=[str(p[2].get("name") or "Plan") for p in pts],
                hovertemplate=label+" · %{text}<br>%{x:,.0f} SF · $%{y:,.0f}/mo P&I<extra></extra>"
            ))
            mid=pts[len(pts)//2]
            point_labels.append((mid[0],mid[1],label,False))
        _annotate_sparse(fig,point_labels)
        fig.update_xaxes(title="Home size (SF)",gridcolor="#edf2f7")
        fig.update_yaxes(title="Monthly P&I ($)",tickprefix="$",gridcolor="#edf2f7")
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
    # Keep the legend INSIDE the Plotly figure. Anchoring below the axes
    # avoids overlap with the mobile modebar while retaining the figure's
    # interactive/responsive legend (including when expanded).
    show_market_legend=metric in ("Price/SF","Price")
    n_market_lines=len(trend_labels) if show_market_legend else 0
    legend_bottom=100+max(0,n_market_lines-3)*10 if n_market_lines else 40
    fig.update_layout(
        template="plotly_white",
        height=(460 if metric in ("Price/SF","Price","Payment") else 390)
               + (legend_bottom-40 if n_market_lines else 0),
        margin=dict(l=6,r=6,t=24,b=legend_bottom),font=dict(size=11),
        legend=dict(orientation="h",y=-0.19,yanchor="top",x=0,xanchor="left",
                    font=dict(size=10),tracegroupgap=4),
        hovermode="closest",dragmode="pan"
    )
    _set_default_focus(fig,series,draft,plans,metric,rate,down)
    return fig,trend_labels,diagnostics



def _plotly_config() -> dict:
    """Keep pan as the default gesture and use toolbar buttons for zoom.

    Removing drag-mode and selection-mode buttons prevents accidental switching
    into a mode that feels stuck on a touchscreen.
    """
    return {
        "displaylogo":False,
        "scrollZoom":False,
        "responsive":True,
        "modeBarButtonsToRemove":["zoom2d","pan2d","select2d","lasso2d"],
        "displayModeBar":True,
    }


@st.dialog("Expanded market view",width="large")
def expanded_chart(fig: go.Figure,draft: dict,plans: list[dict],lines):
    st.plotly_chart(fig,use_container_width=True,config=_plotly_config())
    st.caption("Drag to pan · Use + / − to zoom · Home to reset.")
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
    if scope=="Quick Scan":
        applied=draft.get("applied_pricing_reference") or {}
        if applied and applied.get("tma_name")==series[0].get("name"):
            percent=applied.get("percent",0)
            label=f"{percent:+g}%" if isinstance(percent,(int,float)) else str(percent)
            st.caption(
                f"Subject pricing reference: {applied['tma_name']} · "
                f"{applied['product']} ({label} versus trendline)"
            )
    fig,lines,diagnostics=make_chart(series,draft,plans,metric,rate,down)
    st.plotly_chart(fig,use_container_width=True,config=_plotly_config())
    st.caption("Drag to pan · Use the + / − chart buttons to zoom · Home to reset. Scroll outside the chart to move down the page.")
    if st.button("Expand chart",use_container_width=True,key="shared_expand_chart"):
        expanded_chart(fig,draft,plans,lines)
    community_key(draft,plans,lines)
    if diagnostics:
        with st.expander(f"Model and coverage notes ({len(diagnostics)})"):
            for name,note in diagnostics:
                st.caption(f"{name}: {note}")
    st.caption("Saved project charts are current MLS reconstructions, not byte-identical desktop snapshots.")
