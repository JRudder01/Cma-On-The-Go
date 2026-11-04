# CMA On The Go

An **independent mobile-first Streamlit companion** to CMA O' Matic. This repository does **not** contain a branch of the desktop repository and does **not** mutate desktop data, MLS warehouse records, project state, or database schema.

## What v0.1 includes

- Touch-friendly Quick Market Scan by city/state, ZIP, or latitude/longitude and radius.
- Canonical MLS-only TMA assembly using the *same source implementation* as desktop at initial port: `warehouse_tma_assembly.py`, `product_classifier.py`, `outlier_engine.py`, `trend_model.py`.
- Resale scatter chart with inventory, current selected new-home plans, and a robust resale trendline for the selected product category.
- Price / Price per SF / inventory modes in one Plotly chart, with zoom/pan and a separate expanded view.
- Search active, already-entered communities and their latest active plan versions.
- Open existing saved desktop project **snapshots without calling the desktop loader**, which can otherwise write normalization updates.
- Export a JSON definition of a field scan locally.
- No MLS uploads, plan changes, database writes, or unapproved schema changes.

## Important limitations (v0.1)

- Field scans **are session drafts, not synchronized saved projects**. Bidirectional mobile-to-desktop saves require a separately reviewed authorization and workspace compatibility phase.
- The Market Lens trendline reuses desktop model code, but full end-to-end desktop output parity and coverage completeness have **not yet been verified against production data**. Do not use as a client-facing price conclusion before validation.
- Outlier filtering uses the ported automatic engine. Desktop analysts may have manually reviewed exclusions; the mobile model does not import analyst-specific overrides yet.
- Payment **history** is not available as a true time series until verified historical rate and incentive snapshots are available. Do not manufacture payment trends.
- The first password gate is a limited **single shared passphrase**, not a production multi-user identity system. Before real multi-client access, implement individual authentication, roles, row-level project authorizations, and audits.
- A public GitHub/Streamlit deployment is **not** a reason to expose restricted MLS data. Check licensing before connecting or sharing any data.

## Streamlit Community Cloud deployment

1. Deploy this repository as a **separate** Streamlit app with entrypoint `app.py`.
2. Open the new app's **Settings → Secrets** (not a public GitHub file). Set:
   ```toml
   ACCESS_PASSWORD = "temporary-development-passphrase-at-least-4-characters"
   # Add only after the database and access scope are separately approved:
   # READONLY_DATABASE_URL = "postgresql+psycopg://restricted_reader:...@...:5432/postgres?sslmode=require"
   ```
3. Without `ACCESS_PASSWORD`, the app intentionally stays locked.
4. Without `READONLY_DATABASE_URL`, the app is a safe UI-only prototype: no warehouse access is made.
5. **Never** use a privileged or migration-owner database credential, service-role token, or production write account here. Provision a dedicated PostgreSQL read-only role with only approved SELECT privileges, ideally limited to permitted market and customer records. The connector forces read-only database transactions and a statement timeout as a second layer, not as a substitute for SQL permissions.
6. After selecting an access model and confirming MLS license terms, test existing project loads and identical warehouse filters side by side with the desktop.

## Local setup

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
streamlit run app.py
pytest -q
```

For local credentials create an **untracked** `.streamlit/secrets.toml` (never commit it). Use `.streamlit/secrets.example.toml` only as a placeholder.

## Architecture

```
app.py                     Mobile-first Streamlit UI and session drafts
data_access.py             SELECT-only access to existing PostgreSQL tables
core/product_classifier.py Existing desktop classifier port
core/outlier_engine.py      Existing desktop automatic outlier engine port
core/warehouse_tma_assembly.py Existing desktop canonical MLS TMA assembly port
core/trend_model.py         Existing desktop Huber resale trend model port
tests/                     Lightweight smoke and regression tests
```

Original analytical source copied **read-only** from `JRudder01/Rudder-Analytics-CMA-O-Matic-v1`. Future changes to one core should be compared and deliberately ported to the other; this code is not automatically synchronized.

## Safety boundaries

- Allowed modifications: this **new repository only**, explicitly authorized for development.
- Forbidden unless separately authorized: desktop CMA O' Matic repo, existing branches, existing Streamlit deployment, Supabase schemas, database roles, RLS settings, and all production warehouse/project data.
- No real secrets, MLS CSVs, actual client project payloads, private addresses, or partner branding in public GitHub.

## Shared Supabase integration

A dedicated, narrowly scoped, **read-only** warehouse connection is prepared but deliberately inactive until the owner authorizes role creation and enters the credentials into Streamlit Secrets. See [Warehouse connection setup](docs/WAREHOUSE_CONNECTION_SETUP.md) for the SQL template, verification steps, and security boundaries. **Never use the desktop administrator DATABASE_URL.**

**Development-only warning:** The application currently accepts a 4-character minimum access passphrase for convenient testing. This is insufficient to protect private MLS/project data on a publicly reachable deployment. Use a strong, unique actual secret and replace this shared-passphrase gate with real user authentication before inviting external users. The shorter minimum does not change database credentials.
