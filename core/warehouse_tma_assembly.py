from __future__ import annotations

"""Warehouse-wide row-geography assembly for Target Market Areas.

A TMA is a geographic query over the canonical warehouse, not a pointer to one
historical pull. This MLS-only build assembles every usable CRMLS observation whose
canonical property geography belongs to the requested market, then collapses repeated
observations to canonical analytical rows. Raw MLS geography remains audit lineage but
never overrides canonical TMA membership. The empty ``rentcast`` frame is retained
only as a compatibility field for older callers; it is never populated.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping

import numpy as np
import pandas as pd
from sqlalchemy import text

from product_classifier import broad_property_bucket, classify_tma_product


_SNAPSHOT_SCHEMA_CACHE: dict[str, bool] = {}


def _inventory_snapshot_schema_ready(engine: Any) -> bool:
    """Return whether the optional authoritative-presence overlay is installed.

    Cached per database URL for the process lifetime. Deployments restart Streamlit,
    so a newly applied migration is discovered on the next app boot without adding a
    schema-probe query to every TMA assembly.
    """
    key = str(getattr(engine, "url", ""))
    if key in _SNAPSHOT_SCHEMA_CACHE:
        return _SNAPSHOT_SCHEMA_CACHE[key]
    try:
        with engine.connect() as conn:
            ready = bool(conn.execute(text("""
                select to_regclass('warehouse.inventory_current_state') is not null
            """)).scalar_one())
    except Exception:
        ready = False
    _SNAPSHOT_SCHEMA_CACHE[key] = ready
    return ready


@dataclass(frozen=True)
class WarehouseTMAAssembly:
    rentcast: pd.DataFrame
    mls: pd.DataFrame
    stats: Mapping[str, Any]
    available: bool = True
    warning: str | None = None

    @property
    def has_rows(self) -> bool:
        return bool((isinstance(self.rentcast, pd.DataFrame) and not self.rentcast.empty) or (isinstance(self.mls, pd.DataFrame) and not self.mls.empty))


def _clean_zip(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:5] if len(digits) >= 5 else ""


def _property_bucket(property_type: Any, property_subtype: Any) -> str:
    """Return the selector-level category from product taxonomy only."""
    return broad_property_bucket(property_subtype, source_property_type=property_type)


def _mls_subtype(bucket: str, source_subtype: Any) -> str:
    value = str(source_subtype or "").strip()
    if value:
        return value
    if bucket == "Single Family":
        return "Single Family Residence"
    if bucket == "Condo":
        return "Condominium"
    if bucket == "Townhouse":
        return "Townhouse"
    if bucket == "Manufactured":
        return "Manufactured On Land"
    return str(source_subtype or bucket or "Other")


def _tma_type(bucket: str, property_type: Any = None, property_subtype: Any = None) -> str:
    """Return the product-taxonomy TMA class; Attached Y/N is never consulted."""
    subtype = property_subtype
    if subtype in (None, ""):
        subtype = {
            "Single Family": "Single Family Residence",
            "Condo": "Condominium",
            "Townhouse": "Townhouse",
            "Multi-Family": "Multi-Family",
        }.get(bucket, bucket)
    return classify_tma_product(subtype, source_property_type=property_type)


def _canonical_city_sql() -> str:
    # Runtime compatibility for warehouses loaded before dev.10.10.2. New ingestion
    # persists this alias canonically; this CASE keeps older loaded rows analytically
    # correct until the schema/data migration is applied.
    return """case
        when upper(coalesce(p.state,''))='CA'
         and left(coalesce(p.zip_code,''),5)='92119'
         and lower(trim(coalesce(p.city,'')))='san carlos'
        then 'San Diego'
        else p.city
    end"""


def _geography_clause(mode: str, location: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Build geography SQL exclusively from canonical MLS property geography.

    Source-row geography remains available in ``warehouse.source_records`` for audit,
    but TMA membership is authoritative from ``warehouse.properties``. This prevents
    source-label aliases and malformed ZIP/city combinations from leaking into market
    selection after canonical normalization/QA.
    """
    loc = dict(location or {})
    if mode == "City + state":
        city = str(loc.get("city") or "").strip().casefold()
        state = str(loc.get("state") or "").strip().upper()
        if not city or not state:
            raise ValueError("City and state are required before warehouse data can be assembled.")
        canonical_city = _canonical_city_sql()
        return (
            f"lower(trim(coalesce(({canonical_city}),'')))=:geo_city and upper(trim(coalesce(p.state,'')))=:geo_state",
            {"geo_city": city, "geo_state": state},
        )
    if mode == "ZIP code":
        zip_code = _clean_zip(loc.get("zipCode"))
        if not zip_code:
            raise ValueError("A valid ZIP code is required before warehouse data can be assembled.")
        return "left(coalesce(p.zip_code,''),5)=:geo_zip", {"geo_zip": zip_code}
    if mode == "Coordinates + radius":
        try:
            lat = float(loc.get("latitude"))
            lon = float(loc.get("longitude"))
            radius = float(loc.get("radius"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Valid center coordinates and radius are required before warehouse data can be assembled.") from exc
        if radius <= 0:
            raise ValueError("Radius must be greater than zero.")
        # Cheap indexed bounding-box prefilter first, exact Haversine second. This
        # keeps radius TMAs efficient as the shared warehouse grows.
        lat_delta = radius / 69.0
        lon_scale = max(0.01, abs(float(np.cos(np.radians(lat)))))
        lon_delta = radius / (69.0 * lon_scale)
        clause = """
            p.latitude is not null and p.longitude is not null and
            p.latitude between :geo_lat_min and :geo_lat_max and
            p.longitude between :geo_lon_min and :geo_lon_max and
            3958.7613 * 2 * asin(sqrt(
                power(sin(radians(p.latitude-:geo_lat)/2),2)
                + cos(radians(:geo_lat))*cos(radians(p.latitude))*power(sin(radians(p.longitude-:geo_lon)/2),2)
            )) <= :geo_radius
        """
        return clause, {
            "geo_lat": lat, "geo_lon": lon, "geo_radius": radius,
            "geo_lat_min": lat - lat_delta, "geo_lat_max": lat + lat_delta,
            "geo_lon_min": lon - lon_delta, "geo_lon_max": lon + lon_delta,
        }
    raise ValueError(f"Unsupported TMA geography mode: {mode}")

def _standardize(rows: list[Mapping[str, Any]], *, selected_property_types: list[str], inventory: bool) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    selected = {str(v).strip().casefold() for v in selected_property_types if str(v).strip()}
    out: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        bucket = _property_bucket(row.get("property_type"), row.get("property_subtype"))
        if selected and bucket.casefold() not in selected:
            continue
        living = pd.to_numeric(row.get("living_area"), errors="coerce")
        list_price = pd.to_numeric(row.get("list_price"), errors="coerce")
        close_price = pd.to_numeric(row.get("close_price"), errors="coerce")
        price = list_price if inventory else close_price
        ppsf = np.nan
        if pd.notna(price) and pd.notna(living) and float(living) > 0:
            ppsf = float(price) / float(living)
        source = str(row.get("source_system") or "").strip()
        address = str(row.get("canonical_address") or "").strip()
        status = str(row.get("status_normalized") or ("Active" if inventory else "Closed")).strip() or ("Active" if inventory else "Closed")
        tma_type = _tma_type(bucket, row.get("property_type"), row.get("property_subtype"))
        # The source Attached Y/N flag is intentionally not synthesized from the
        # analytical category. Raw source lineage retains the original field for QA.
        attached_flag = pd.NA
        record = {
            "Canonical Property ID": row.get("property_id"),
            "Canonical Listing ID": row.get("listing_id"),
            "Canonical Transaction ID": row.get("transaction_id"),
            "Formatted Address": address,
            "Address": address,
            "City": row.get("city"),
            "State": row.get("state"),
            "Zip Code": row.get("zip_code"),
            "Latitude": row.get("latitude"),
            "Longitude": row.get("longitude"),
            "Parcel Number": row.get("apn"),
            "RentCast Property Type": bucket,
            "Property Type": row.get("property_type"),
            "Property Sub Type": _mls_subtype(bucket, row.get("property_subtype")),
            "TMA Type": tma_type,
            "Property Attached YN": attached_flag,
            "Standard Status": status,
            "List Price": list_price,
            "Original List Price": pd.to_numeric(row.get("original_list_price"), errors="coerce"),
            "Close Price": close_price,
            "Living Area": living,
            "Lot Size Square Feet": pd.to_numeric(row.get("lot_size_sqft"), errors="coerce"),
            "Year Built": pd.to_numeric(row.get("year_built"), errors="coerce"),
            "Bedrooms": pd.to_numeric(row.get("bedrooms"), errors="coerce"),
            "Bedrooms Total": pd.to_numeric(row.get("bedrooms"), errors="coerce"),
            "Bathrooms": pd.to_numeric(row.get("bathrooms"), errors="coerce"),
            "Bathrooms Total Integer": pd.to_numeric(row.get("bathrooms"), errors="coerce"),
            "Garage Spaces": pd.to_numeric(row.get("garage_spaces"), errors="coerce"),
            "Days On Market": pd.to_numeric(row.get("days_on_market"), errors="coerce"),
            "Cumulative Days Active In MLS": pd.to_numeric(row.get("cumulative_days_on_market"), errors="coerce"),
            "List Date": pd.to_datetime(row.get("list_date"), errors="coerce"),
            "Listing Contract Date": pd.to_datetime(row.get("list_date"), errors="coerce"),
            "Purchase Contract Date": pd.to_datetime(row.get("pending_date"), errors="coerce"),
            "Close Date": pd.to_datetime(row.get("close_date"), errors="coerce"),
            "Last Seen Date": pd.to_datetime(row.get("last_seen_at"), errors="coerce"),
            "Listing Type": row.get("listing_type"),
            "Builder": row.get("builder_name"),
            "Development": row.get("development_name"),
            "MLS Name": row.get("mls_name"),
            "MLS Number": row.get("mls_number"),
            "Price per Sq Ft": ppsf,
            "Source File": f"{source} warehouse {'inventory' if inventory else 'closed sales'}",
            "Data Provenance": source,
            "Warehouse Source System": source,
            "Warehouse Source Record ID": row.get("source_record_id"),
            "Warehouse Observed At": pd.to_datetime(row.get("observed_at"), errors="coerce"),
            "Inventory Snapshot At": pd.to_datetime(row.get("inventory_snapshot_at"), errors="coerce"),
            "Inventory Snapshot As Of": pd.to_datetime(row.get("inventory_snapshot_as_of"), errors="coerce"),
            "Inventory Presence State": row.get("inventory_presence_state"),
        }
        # Normalize the status classes used by the existing MLS/RentCast merger.
        status_cf = status.casefold()
        if inventory:
            if "pending" in status_cf:
                record["Standard Status"] = "Pending"
            elif "active" in status_cf:
                record["Standard Status"] = "Active"
            else:
                continue
        else:
            record["Standard Status"] = "Closed"
        out.append(record)
    return pd.DataFrame(out)


def _latest_date(frame: pd.DataFrame, column: str) -> date | None:
    if not isinstance(frame, pd.DataFrame) or frame.empty or column not in frame.columns:
        return None
    series = pd.to_datetime(frame[column], errors="coerce", utc=True).dropna()
    if series.empty:
        return None
    return series.max().date()


def assemble_tma_from_warehouse(
    db: Any,
    *,
    mode: str,
    location: Mapping[str, Any],
    property_types: list[str],
    period_start: date,
    period_end: date,
    active_max_age_days: int = 180,
) -> WarehouseTMAAssembly:
    """Assemble source-specific analytical rows from the whole canonical warehouse.

    Repeated historical observations are collapsed at canonical property/listing/
    transaction identity. Only CRMLS/MLS rows are eligible in this build.
    """
    engine = getattr(db, "engine", None)
    if engine is None or not str(getattr(engine.url, "drivername", "")).startswith("postgresql"):
        return WarehouseTMAAssembly(pd.DataFrame(), pd.DataFrame(), {}, available=False, warning="Warehouse-wide TMA assembly requires PostgreSQL/Supabase.")

    try:
        geography_sql, params = _geography_clause(mode, location)
    except Exception as exc:
        return WarehouseTMAAssembly(pd.DataFrame(), pd.DataFrame(), {}, available=True, warning=str(exc))

    params = dict(params)
    params.update({
        "period_start": period_start,
        "period_end": period_end,
        "active_age_days": max(1, int(active_max_age_days or 180)),
    })

    snapshot_overlay = _inventory_snapshot_schema_ready(engine)
    inventory_presence_select = (
        "ics.snapshot_at as inventory_snapshot_at, ics.snapshot_as_of as inventory_snapshot_as_of, "
        "ics.is_current as inventory_presence_current, ics.presence_state as inventory_presence_state,"
        if snapshot_overlay else
        "null::timestamptz as inventory_snapshot_at, null::date as inventory_snapshot_as_of, "
        "null::boolean as inventory_presence_current, null::text as inventory_presence_state,"
    )
    inventory_presence_join = (
        "left join warehouse.inventory_current_state ics on ics.listing_id=l.id"
        if snapshot_overlay else ""
    )
    inventory_presence_filter = (
        "and not (coalesce(inventory_presence_current,true)=false and inventory_snapshot_at is not null "
        "and inventory_snapshot_at >= coalesce(last_seen_at,observed_at))"
        if snapshot_overlay else ""
    )

    inventory_sql = text(f"""
        with listing_latest as (
            select
                p.id::text as property_id,
                l.id::text as listing_id,
                null::text as transaction_id,
                p.canonical_address, ({_canonical_city_sql()}) as city, p.state, p.zip_code, p.latitude, p.longitude,
                p.apn, p.property_type, p.property_subtype, p.living_area_sqft as living_area,
                p.lot_size_sqft, p.bedrooms, p.bathrooms, p.garage_spaces, p.year_built,
                ls.source_system, ls.source_record_id::text as source_record_id,
                ls.status_normalized, ls.list_price, ls.original_list_price,
                null::numeric as close_price, null::date as close_date,
                ls.days_on_market, ls.cumulative_days_on_market, ls.list_date, ls.pending_date,
                ls.last_seen_at, ls.listing_type, ls.builder_name, ls.development_name,
                ls.mls_name, ls.mls_number, ls.observed_at,
                {inventory_presence_select}
                row_number() over (
                    partition by l.id, ls.source_system
                    order by coalesce(ls.last_seen_at, ls.observed_at) desc, ls.observed_at desc, ls.id desc
                ) as listing_rn
            from warehouse.listing_snapshots ls
            join warehouse.listings l on l.id=ls.listing_id
            join warehouse.properties p on p.id=l.property_id
            {inventory_presence_join}
            where ls.source_system='CRMLS'
              and coalesce(ls.quality_status,'accepted') in ('accepted','warning')
              and p.identity_status = 'active'
              and ({geography_sql})
        ), current_candidates as (
            select *,
                   row_number() over (
                     partition by property_id, source_system
                     order by coalesce(last_seen_at, observed_at) desc, observed_at desc, listing_id desc
                   ) as property_rn
            from listing_latest
            where listing_rn=1
              and coalesce(last_seen_at, observed_at) >= now() - (:active_age_days * interval '1 day')
              and (lower(coalesce(status_normalized,'')) like 'active%' or lower(coalesce(status_normalized,'')) like '%pending%')
              {inventory_presence_filter}
        )
        select * from current_candidates where property_rn=1
    """)

    sales_sql = text(f"""
        select
            p.id::text as property_id,
            t.listing_id::text as listing_id,
            t.id::text as transaction_id,
            p.canonical_address, ({_canonical_city_sql()}) as city, p.state, p.zip_code, p.latitude, p.longitude,
            p.apn, p.property_type, p.property_subtype, p.living_area_sqft as living_area,
            p.lot_size_sqft, p.bedrooms, p.bathrooms, p.garage_spaces, p.year_built,
            txo.source_system, txo.source_record_id::text as source_record_id,
            'Closed'::text as status_normalized,
            ls.list_price, ls.original_list_price,
            coalesce(txo.close_price,t.close_price) as close_price,
            coalesce(txo.close_date,t.close_date) as close_date,
            ls.days_on_market, ls.cumulative_days_on_market, ls.list_date, ls.pending_date,
            ls.last_seen_at, ls.listing_type, ls.builder_name, ls.development_name,
            ls.mls_name, ls.mls_number, txo.observed_at
        from warehouse.canonical_transaction_observations txo
        join warehouse.transactions t on t.id=txo.transaction_id
        join warehouse.properties p on p.id=t.property_id
        left join lateral (
            select s.*
            from warehouse.listing_snapshots s
            where t.listing_id is not null
              and s.listing_id=t.listing_id
              and s.source_system=txo.source_system
              and coalesce(s.quality_status,'accepted') in ('accepted','warning')
            order by s.observed_at desc, s.id desc
            limit 1
        ) ls on true
        where txo.source_system='CRMLS'
          and coalesce(txo.quality_status,'accepted') in ('accepted','warning')
          and p.identity_status = 'active'
          and coalesce(txo.close_date,t.close_date) between :period_start and :period_end
          and coalesce(txo.close_price,t.close_price) > 0
          and ({geography_sql})
    """)

    try:
        with engine.connect() as conn:
            inventory_rows = [dict(r) for r in conn.execute(inventory_sql, params).mappings().all()]
            sales_rows = [dict(r) for r in conn.execute(sales_sql, params).mappings().all()]
    except Exception as exc:
        return WarehouseTMAAssembly(pd.DataFrame(), pd.DataFrame(), {}, available=False, warning=f"Warehouse-wide assembly could not be read safely: {exc}")

    inventory = _standardize(inventory_rows, selected_property_types=property_types, inventory=True)
    sales = _standardize(sales_rows, selected_property_types=property_types, inventory=False)
    combined = pd.concat([inventory, sales], ignore_index=True, sort=False) if (not inventory.empty or not sales.empty) else pd.DataFrame()

    rc = pd.DataFrame()
    if combined.empty or "Warehouse Source System" not in combined.columns:
        mls = pd.DataFrame()
    else:
        mls = combined[combined["Warehouse Source System"].astype(str).eq("CRMLS")].copy().reset_index(drop=True)

    def status_count(frame: pd.DataFrame, status: str) -> int:
        if frame.empty or "Standard Status" not in frame.columns:
            return 0
        if status == "inventory":
            return int(frame["Standard Status"].astype(str).str.casefold().isin({"active", "pending"}).sum())
        return int(frame["Standard Status"].astype(str).str.casefold().eq("closed").sum())

    property_ids = set()
    for frame in (rc, mls):
        if not frame.empty and "Canonical Property ID" in frame.columns:
            property_ids.update(str(v) for v in frame["Canonical Property ID"].dropna().tolist() if str(v))

    stats = {
        "rentcast_active_rows": status_count(rc, "inventory"),
        "rentcast_sold_rows": status_count(rc, "closed"),
        "mls_inventory_rows": status_count(mls, "inventory"),
        "mls_sold_rows": status_count(mls, "closed"),
        "rentcast_rows": int(len(rc)),
        "mls_rows": int(len(mls)),
        "combined_source_rows": int(len(mls)),
        "canonical_properties": int(len(property_ids)),
        "rentcast_latest_observed": _latest_date(rc, "Warehouse Observed At"),
        "mls_latest_observed": _latest_date(mls, "Warehouse Observed At"),
        "inventory_snapshot_overlay": bool(snapshot_overlay),
        "inventory_snapshot_managed_rows": int(
            mls.get("Inventory Snapshot At", pd.Series(dtype=object)).notna().sum()
            if isinstance(mls, pd.DataFrame) and not mls.empty else 0
        ),
        "inventory_snapshot_latest_as_of": _latest_date(mls, "Inventory Snapshot As Of"),
        "assembly_mode": "warehouse_mls_only_canonical_geography",
    }
    return WarehouseTMAAssembly(rc, mls, stats, available=True, warning=None)
