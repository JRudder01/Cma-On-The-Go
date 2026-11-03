"""Strictly read-only accessors for the existing desktop warehouse schema.

No schema creation, migrations, UPDATEs, INSERTs, or project normalization occurs here.
Use a dedicated SELECT-only PostgreSQL credential in Streamlit Secrets.
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from core.warehouse_tma_assembly import assemble_tma_from_warehouse


@dataclass
class DbHandle:
    engine: Any


def configured() -> bool:
    try:
        return bool(st.secrets.get("READONLY_DATABASE_URL", ""))
    except Exception:
        return False


@st.cache_resource(show_spinner=False)
def get_db() -> DbHandle:
    url = st.secrets.get("READONLY_DATABASE_URL", "")
    if not url:
        raise RuntimeError("Read-only warehouse connection has not been configured.")
    if not (url.startswith("postgresql+psycopg://") or url.startswith("postgresql://")):
        raise ValueError("Only PostgreSQL read-only URLs are supported.")
    parsed = make_url(url)
    # A public Streamlit app must NEVER accept the database owner, postgres or
    # another privileged role. The username suffix is required by Supavisor.
    expected_names = {
        "cma_on_the_go_reader",
        "cma_on_the_go_reader.fbdnizwixnkcybjbwosf",
    }
    if parsed.username not in expected_names:
        raise ValueError("Only the dedicated cma_on_the_go_reader database login is permitted.")
    if parsed.query.get("sslmode") not in ("require", "verify-ca", "verify-full"):
        raise ValueError("The database connection must require TLS (sslmode=require).")
    if parsed.host is None or not (
        parsed.host.endswith(".pooler.supabase.com")
        or parsed.host == "db.fbdnizwixnkcybjbwosf.supabase.co"
    ):
        raise ValueError("Connection host must belong to the configured Supabase database.")
    engine = create_engine(
        url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=0,
        connect_args={
            "options": "-c default_transaction_read_only=on -c statement_timeout=12000",
            "connect_timeout": 10,
        },
    )
    try:
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT current_user AS role_name,
                       current_setting('transaction_read_only') AS read_only,
                       r.rolsuper, r.rolcreaterole, r.rolcreatedb,
                       r.rolreplication, r.rolbypassrls,
                       has_table_privilege(current_user,'warehouse.projects','INSERT') AS can_insert_projects,
                       has_table_privilege(current_user,'warehouse.projects','UPDATE') AS can_update_projects,
                       has_table_privilege(current_user,'warehouse.projects','DELETE') AS can_delete_projects,
                       has_schema_privilege(current_user,'warehouse','CREATE') AS can_create_in_warehouse
                  FROM pg_roles r WHERE r.rolname=current_user
            """)).mappings().one()
            if row["role_name"] != "cma_on_the_go_reader" or row["read_only"] != "on":
                raise PermissionError("Warehouse connection is not using the approved read-only identity.")
            forbidden = ("rolsuper","rolcreaterole","rolcreatedb","rolreplication","rolbypassrls",
                         "can_insert_projects","can_update_projects","can_delete_projects",
                         "can_create_in_warehouse")
            if any(bool(row[key]) for key in forbidden):
                raise PermissionError("Warehouse login has permissions beyond the approved read-only scope.")
    except Exception:
        engine.dispose()
        raise
    return DbHandle(engine)


def connection_check() -> dict:
    """Verify the mobile reader can see each required relation (no user data returned)."""
    required = (
        "public.builders", "public.communities", "public.plans",
        "public.plan_versions", "warehouse.projects",
        "warehouse.project_workspace_state", "warehouse.properties",
        "warehouse.listings", "warehouse.listing_snapshots",
        "warehouse.transactions", "warehouse.transaction_observations",
        "warehouse.canonical_transaction_observations",
        "warehouse.inventory_current_state",
    )
    with get_db().engine.connect() as conn:
        missing = [
            name for name in required
            if not conn.execute(
                text("select has_table_privilege(current_user, :relation, 'SELECT')"),
                {"relation": name},
            ).scalar_one()
        ]
        if missing:
            raise PermissionError("Read-only role is missing SELECT privileges on " + ", ".join(missing))
        return {"reader_verified": True, "required_objects_checked": len(required)}


def _rows(sql: str, params: dict | None = None) -> list[dict]:
    with get_db().engine.connect() as conn:
        return [dict(row) for row in conn.execute(text(sql), params or {}).mappings()]


def list_projects(search: str = "", limit: int = 100) -> list[dict]:
    return _rows("""
      SELECT p.id::text AS id, p.name, p.city, p.state, p.zip_code,
             ws.updated_at, ws.app_version
      FROM warehouse.projects p
      JOIN warehouse.project_workspace_state ws ON ws.project_id = p.id
      WHERE p.status <> 'archived'
        AND (lower(p.name) LIKE :search OR lower(coalesce(p.city,'')) LIKE :search)
      ORDER BY ws.updated_at DESC LIMIT :limit
    """, {"search": f"%{search.strip().lower()}%", "limit": min(max(1, limit), 100)})


def load_project(project_id: str) -> dict:
    try:
        pid = str(UUID(project_id))
    except (TypeError, ValueError):
        raise ValueError("Invalid project reference.") from None
    rows = _rows("""
      SELECT p.name, p.city, p.state, p.zip_code,
             ws.state_json, ws.updated_at, ws.workspace_version
      FROM warehouse.projects p
      JOIN warehouse.project_workspace_state ws ON ws.project_id = p.id
      WHERE p.id = cast(:project_id AS uuid) AND p.status <> 'archived'
      LIMIT 1
    """, {"project_id": pid})
    if not rows:
        raise ValueError("Project not found.")
    result = rows[0]
    if isinstance(result["state_json"], str):
        result["state_json"] = json.loads(result["state_json"])
    return result


def search_communities(query: str = "", city: str = "", limit: int = 100) -> list[dict]:
    return _rows("""
       SELECT c.id, c.name, c.city, c.state, c.zip_code, c.product_type,
              c.latitude, c.longitude, b.name AS builder,
              count(DISTINCT p.id) AS plan_count
       FROM communities c
       JOIN builders b ON b.id = c.builder_id
       LEFT JOIN plans p ON p.community_id = c.id
       WHERE c.active = true AND lower(c.name || ' ' || b.name || ' ' || coalesce(c.city,'')) LIKE :query
         AND (:city = '' OR lower(coalesce(c.city,'')) = :city)
       GROUP BY c.id, c.name, c.city, c.state, c.zip_code, c.product_type,
                c.latitude, c.longitude, b.name
       ORDER BY c.city, c.name LIMIT :limit
    """, {"query": f"%{query.strip().lower()}%", "city": city.strip().lower(), "limit": min(max(1, limit), 100)})


def current_plans(community_ids: list[int]) -> list[dict]:
    ids = list(dict.fromkeys(int(cid) for cid in community_ids))[:30]
    if not ids:
        return []
    # Only dynamically bind values, never construct untrusted SQL tokens.
    from sqlalchemy import bindparam
    statement = text("""
        SELECT p.community_id, p.id AS plan_id, p.name, pv.sqft, pv.base_price,
               pv.product_type, pv.last_verified_date, pv.effective_date
        FROM plans p
        JOIN communities c ON c.id = p.community_id AND c.active = true
        JOIN LATERAL (
          SELECT v.* FROM plan_versions v
          WHERE v.plan_id = p.id AND v.is_active = true
          ORDER BY v.effective_date DESC, v.id DESC LIMIT 1
        ) pv ON true
        WHERE p.community_id IN :ids ORDER BY p.community_id, pv.sqft
    """).bindparams(bindparam("ids", expanding=True))
    with get_db().engine.connect() as conn:
        return [dict(row) for row in conn.execute(statement, {"ids": ids}).mappings()]


def run_tma(mode: str, location: dict, product_types: list[str],
            start: date, end: date, active_age: int = 180):
    if start > end:
        raise ValueError("Analysis start date must be on or before its end date.")
    if (end - start).days > 3650:
        raise ValueError("Analysis period is too long.")
    return assemble_tma_from_warehouse(
        get_db(), mode=mode, location=location, property_types=[],  # Warehouse selector uses broad buckets; filter exact TMA class in UI.
        period_start=start, period_end=end, active_max_age_days=active_age
    )
