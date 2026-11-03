# Secure shared warehouse connection (manual setup)

**Project:** existing Supabase project `fbdnizwixnkcybjbwosf` (the desktop CMA warehouse). This document does not execute SQL. It does not change any existing repository or deployment.

## Reviewed 2026-11-03

Read-only inspection confirmed:

- Canonical warehouse tables and `warehouse.canonical_transaction_observations` exist.
- Existing project and workspace state tables exist, with current desktop project records.
- There is currently no `cma_on_the_go_reader` database login.
- `anon` and `authenticated` do **not** have direct `SELECT` privileges on the core CMA tables inspected, and neither has `USAGE` on the warehouse schema.
- Numerous tables have RLS disabled. Supabase reported a critical advisory about these tables. The effective grants checked on the sampled CMA tables were not publicly readable. **Do not assume that disabled RLS implies exposure when SELECT/USAGE are revoked.** Perform a separate access-control audit before wider access. **Do not turn RLS on without coordinated desktop-impact review**; doing so might interrupt existing workflows.
- The canonical transaction view is `security_invoker=true`, so the new reader also needs SELECT access to the view's underlying tables.

## Before running any SQL

The user has required that no existing database structure or data be changed without explicit approval. Role creation and GRANT statements **do change database authorization configuration**. They are not executed by this repository and must be explicitly authorized and run by the owner in Supabase SQL Editor.

If you approve this narrowly scoped credential creation, choose a *new and strong* password locally. **Never put the real password in GitHub, a ticket, screenshots, or chat.** Replace the placeholder in the query *in your local SQL editor only*.

## SQL Editor template (run manually only after permission approval)

```sql
-- This creates a separate LOGIN ROLE and grants only SELECT on the
-- precise existing desktop sources used by mobile v0.1.
-- No existing tables, policies, rows, or views are modified.

CREATE ROLE cma_on_the_go_reader WITH
  LOGIN
  PASSWORD 'REPLACE_WITH_A_NEW_LONG_RANDOM_PASSWORD'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS
  CONNECTION LIMIT 3;

ALTER ROLE cma_on_the_go_reader SET default_transaction_read_only = on;
ALTER ROLE cma_on_the_go_reader SET statement_timeout = '12s';

GRANT CONNECT ON DATABASE postgres TO cma_on_the_go_reader;
GRANT USAGE ON SCHEMA public, warehouse TO cma_on_the_go_reader;

GRANT SELECT ON TABLE
  public.builders,
  public.communities,
  public.plans,
  public.plan_versions,
  warehouse.projects,
  warehouse.project_workspace_state,
  warehouse.properties,
  warehouse.listings,
  warehouse.listing_snapshots,
  warehouse.transactions,
  warehouse.transaction_observations,
  warehouse.canonical_transaction_observations,
  warehouse.inventory_current_state
TO cma_on_the_go_reader;

-- Do not grant CREATE, INSERT, UPDATE, DELETE, TRUNCATE or admin privileges.
-- Do not grant ALL TABLES or ALTER DEFAULT PRIVILEGES.
```

The `warehouse.canonical_transaction_observations` view is SECURITY INVOKER, hence grants on underlying `transaction_observations` and `transactions` are intentional.

## Read-only verification in SQL Editor (no credentials shown)

```sql
SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
FROM pg_roles
WHERE rolname='cma_on_the_go_reader';

SELECT
  has_table_privilege('cma_on_the_go_reader', 'warehouse.projects', 'SELECT') AS projects_select,
  has_table_privilege('cma_on_the_go_reader', 'warehouse.projects', 'INSERT') AS projects_insert,
  has_table_privilege('cma_on_the_go_reader', 'warehouse.projects', 'UPDATE') AS projects_update,
  has_schema_privilege('cma_on_the_go_reader', 'warehouse', 'CREATE') AS warehouse_create;
```

The read flags should be true and mutation flags false. The mobile app runs its own connection check against 13 required database objects before analysis.

## Streamlit deployment secrets

From Supabase dashboard for project `fbdnizwixnkcybjbwosf`, use **Connect → Session pooler**. This is commonly the more suitable IPv4-friendly choice on Streamlit Community Cloud. Copy the host and port Supabase shows. For a custom login, use the appropriate username form from that connection method, typically `cma_on_the_go_reader.fbdnizwixnkcybjbwosf` for Supavisor. Do not use `postgres` or a service-role key.

In **CMA On The Go → Manage app → Settings → Secrets** add (using *your real host and private password*):

```toml
ACCESS_PASSWORD = "your-existing-strong-access-passphrase"
READONLY_DATABASE_URL = "postgresql+psycopg://cma_on_the_go_reader.fbdnizwixnkcybjbwosf:YOUR_URL_ENCODED_PASSWORD@YOUR_SESSION_POOLER_HOST:5432/postgres?sslmode=require"
```

URL-encode any password special characters in the connection string (for example, `@` → `%40`, `#` → `%23`, `/` → `%2F`, `%` → `%25`). Do not share this URL in chat.

The application additionally checks: allowed database user, Supabase host, TLS requirement, effective read-only transaction mode, no privileged role flags, and no project writes or warehouse CREATE privilege. Then click **Check secure warehouse connection** from the app.

If connection fails, capture **only a redacted error** and a screenshot with all secrets/host credentials concealed. The UI intentionally avoids displaying raw connection exceptions.

## Authorization and CRMLS licensing

This v0.1 shared-passphrase gate is suitable only for very limited, controlled development testing. It is **not** a multi-client authorization system. Every user knowing the passphrase could browse every workspace available to the reader. Before allowing separate clients, establish individual accounts, scope projects to their owners, and review CRMLS redistribution/display licensing. The public GitHub repository must never contain MLS datasets or connection secrets.

## Boundaries

- Existing desktop GitHub repository and deployed Streamlit app remain unchanged.
- No role, schema, policy, migration or data changes were made by the development assistant.
- Only this new mobile GitHub repo may be edited during app development.
