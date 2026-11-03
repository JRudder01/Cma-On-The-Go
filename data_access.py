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
from core.warehouse_tma_assembly import assemble_tma_from_warehouse


@dataclass
class DbHandle:
    engine: Any


def configured() -> bool:
    try:\n        return bool(st.secrets.get("READONLY_DATABASE_URL", ""))\n    except Exception:\n        return False


@st.cache_resource(show_spinner=False)
def get_db() -> DbHandle:
    url = st.secrets.get("READONLY_DATABASE_URL", "")
    if not url:
        raise RuntimeError("Read-only warehouse connection has not been configured.")
    if not (url.startswith("postgresql+psycopg://") or url.startswith("postgresql://")):
        raise ValueError("Only PostgreSQL read-only URLs are supported.")
    engine = create_engine(
        url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=1,
        connect_args={"options": "-c default_transaction_read_only=on -c statement_timeout=12000"},
    )
    return DbHandle(engine)


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
