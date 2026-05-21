# MS SQL → Snowflake Migration Native App — Documentation

## Table of Contents
1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Tools & Technologies](#3-tools--technologies)
4. [Component Deep-Dive](#4-component-deep-dive)
5. [Data Flow](#5-data-flow)
6. [File Reference](#6-file-reference)
7. [Setup & Deployment Guide](#7-setup--deployment-guide)
8. [Known Limitations](#8-known-limitations)

---

## 1. Project Overview

This project is a **Snowflake Native App** distributed via the Snowflake Marketplace. It provides a self-contained, plug-and-play solution for performing a one-time bulk migration of data and schema from an **Azure MS SQL Server** database into **Snowflake** — with no external ETL infrastructure required.

Everything runs inside the consumer's Snowflake account:
- Connectivity to MS SQL is handled via Snowflake's **External Network Access**
- Schema and stored procedure translation is handled by a **SnowConvert (SCAI) service** running in **Snowflake Container Services (SPCS)**
- Data is read from MS SQL and written to Snowflake via **Snowpark Python stored procedures**
- The user interface is a **Streamlit** app embedded in the Native App

---

## 2. Architecture

### Block Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Snowflake Native App                          │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                    Streamlit UI (4 Tabs)                     │    │
│  │   [⚙️ Setup]  [🔍 Discovery]  [🚀 Migration]  [✅ Validation] │    │
│  └────────────────────────┬────────────────────────────────────┘    │
│                           │  CALL stored procedures                  │
│  ┌────────────────────────▼────────────────────────────────────┐    │
│  │              Snowpark Python Stored Procedures               │    │
│  │                                                              │    │
│  │   sp_discover()        sp_migrate_table()   sp_validate()   │    │
│  │   • List tables        • Chunked full-load  • COUNT(*)      │    │
│  │   • List views         • pandas read_sql    • MD5 hash      │    │
│  │   • List routines      • write_pandas()     • JSON result   │    │
│  └──────────┬─────────────────────────┬───────────────────────┘    │
│             │ External Network Access  │ Internal service call       │
│             │ (pyodbc, port 1433)      │                             │
│  ┌──────────▼──────────┐  ┌───────────▼───────────────────────┐    │
│  │  External Access    │  │   SPCS: scai_service (CONTAINER)  │    │
│  │  Integration        │  │                                    │    │
│  │  • Network Rule     │  │   FastAPI  +  SnowConvert CLI      │    │
│  │  • Secret (creds)   │  │   POST /translate                  │    │
│  └──────────┬──────────┘  │   GET  /health                     │    │
│             │              └───────────────────────────────────┘    │
└─────────────┼────────────────────────────────────────────────────────┘
              │ TCP :1433
              ▼
┌─────────────────────────────┐
│     Azure MS SQL Server     │
│                             │
│  • Source tables / views    │
│  • Stored procedures        │
│  • Firewall: Snowflake IPs  │
│    allowlisted              │
└─────────────────────────────┘
```

### Component Relationships

```
manifest.yml
  └── setup.sql
        ├── app_schema (schema)
        │     ├── mssql_network_rule  (NETWORK RULE)
        │     ├── mssql_secret        (SECRET — username/password)
        │     ├── migration_eai       (EXTERNAL ACCESS INTEGRATION)
        │     ├── scai_repo           (IMAGE REPOSITORY)
        │     ├── scai_service        (SERVICE → scai_service Docker image)
        │     ├── translate_tsql()    (SERVICE FUNCTION → scai_service)
        │     ├── sp_discover()       (STORED PROCEDURE — Python/Snowpark)
        │     ├── sp_migrate_table()  (STORED PROCEDURE — Python/Snowpark)
        │     ├── sp_validate_table() (STORED PROCEDURE — Python/Snowpark)
        │     └── migration_ui        (STREAMLIT)
        └── migration_role (APPLICATION ROLE)
              └── USAGE granted on all objects above
```

---

## 3. Tools & Technologies

| Layer | Technology | Purpose |
|---|---|---|
| **App packaging** | Snowflake Native Apps Framework | Marketplace distribution, versioning, consumer install |
| **UI** | Streamlit (Snowflake-hosted) | Four-tab browser interface inside Snowflake |
| **Data pipeline** | Snowpark Python | Stored procedures that run inside Snowflake |
| **Source connectivity** | pyodbc + ODBC Driver 18 for SQL Server | Connect from Snowpark to Azure MS SQL over TCP 1433 |
| **Network security** | Snowflake External Network Access | Egress network rule + secret binding for outbound connections |
| **Schema translation** | SnowConvert CLI (`scai`) | Converts T-SQL DDL/stored procedures to Snowflake SQL — free, installed via public installer script |
| **Translation host** | Snowflake Container Services (SPCS) | Runs the SnowConvert Docker container inside Snowflake |
| **Translation API** | FastAPI + uvicorn | HTTP wrapper around the scai CLI binary |
| **Data chunking** | pandas `read_sql` with `chunksize` | Streams large tables in configurable row batches |
| **Data loading** | `session.write_pandas()` | Writes pandas DataFrames into Snowflake tables |
| **Validation** | MS SQL `HASHBYTES('MD5')` + Snowflake `MD5(LISTAGG())` | Row count and hash comparison between source and target |
| **Secrets management** | Snowflake SECRET object | Stores MS SQL credentials; never exposed in plain text |
| **Container registry** | Snowflake Image Repository | Hosts the scai Docker image inside Snowflake |
| **Infrastructure** | Azure SQL Database (PaaS) | Source database |
| **CLI tooling** | Snowflake CLI (`snow`) | App deployment, image registry auth, stage uploads |
| **Container tooling** | Docker | Build and push the scai service image |

---

## 4. Component Deep-Dive

### 4.1 Azure MS SQL Server

The source system. Before migration begins, two things must be configured on the Azure side:

**Firewall rules** — Snowflake's outbound IPs must be allowlisted so the stored procedures can reach port 1433. These IPs are retrieved by running:
```sql
SELECT value:host::STRING, value:port::STRING
FROM TABLE(FLATTEN(input => PARSE_JSON(SYSTEM$ALLOWLIST())))
WHERE value:type::STRING = 'SNOWFLAKE_DEPLOYMENT';
```

**Migration user** — A read-only SQL login is created via `scripts/azure_migration_user.sql`. It is granted `db_datareader` (read all tables) and `VIEW DEFINITION` (read schema metadata). It has no write access.

---

### 4.2 External Network Access (EAI)

Snowflake blocks all outbound network calls by default. Three objects work together to allow the stored procedures to call Azure MS SQL:

- **Network Rule** (`mssql_network_rule`) — declares the allowed egress destination: the Azure SQL hostname on port 1433.
- **Secret** (`mssql_secret`) — stores the migration user's username and password. Retrieved inside stored procedures via `_snowflake.get_username_password('cred')` — credentials are never visible in code or logs.
- **External Access Integration** (`migration_eai`) — links the network rule and secret together. Stored procedures declare `EXTERNAL_ACCESS_INTEGRATIONS = (migration_eai)` to opt in.

The EAI is declared as a `reference` in `manifest.yml`, which means the consumer's account admin must explicitly approve it during app installation — a Snowflake Marketplace security requirement.

---

### 4.3 SPCS SnowConvert Service (`scai_service`)

A Docker container running inside Snowflake Container Services that wraps the SnowConvert CLI.

**Why SPCS?** The SnowConvert CLI (`scai`) is a binary that cannot run inside a Snowpark Python stored procedure directly. SPCS provides a compute environment where arbitrary binaries can run. The stored procedures and Streamlit app communicate with it via a **service function** (UDF).

**How it works:**
1. Streamlit calls `SELECT app_schema.translate_tsql('<T-SQL string>')`
2. The service function routes the call to `POST /translate` on the container
3. The FastAPI app writes the T-SQL to a temp file, runs `scai --input <file> --output <dir>`, reads the output `.sql` file, and returns the translated Snowflake SQL
4. If `scai` fails or is unavailable, the endpoint returns HTTP 503 and the UI displays a clear error

**Container spec:**
- Base image: `ubuntu:22.04`
- Python 3.11 + FastAPI + uvicorn
- `scai` installed at Docker build time via the official public installer script (`https://snowconvert.snowflake.com/storage/linux/prod/cli/install.sh`) — free, no license key required
- `scai terms accept` run non-interactively during build
- Authentication at runtime uses the SPCS container's inherited Snowflake service identity — no extra credential config needed
- Readiness probe: `GET /health`

---

### 4.4 Snowpark Stored Procedures

All three procedures share the same connection pattern:
1. Retrieve credentials from the Secret object (never hardcoded)
2. Build a pyodbc connection string targeting the Azure SQL hostname
3. Execute queries against MS SQL
4. Return results as a JSON string

**`sp_discover(hostname, database_name)`**
Queries `INFORMATION_SCHEMA.TABLES` and `INFORMATION_SCHEMA.ROUTINES` on the source database. Returns a JSON array of objects with `schema`, `name`, `type`, and `row_count` fields. Used by the Discovery tab to show the consumer what exists in the source.

**`sp_migrate_table(hostname, database_name, src_schema, table_name, chunk_size, target_schema)`**
Performs the full-load migration of a single table:
1. Connects to MS SQL via pyodbc
2. Reads the table in chunks using `pd.read_sql(..., chunksize=chunk_size)`
3. Coerces mixed-type object columns to strings to prevent pandas type inference errors
4. Calls `session.write_pandas(chunk, table_name, auto_create_table=True, overwrite=True)` for the first chunk, then `overwrite=False` for subsequent chunks (append mode)
5. Returns `{"status", "rows_migrated", "chunks"}` JSON

MS SQL to Snowflake type mapping applied before write:

| MS SQL Type | Snowflake Type |
|---|---|
| `datetime2`, `datetime`, `smalldatetime` | `TIMESTAMP_NTZ` |
| `date` | `DATE` |
| `time` | `TIME` |
| `bit` | `BOOLEAN` |
| `uniqueidentifier` | `VARCHAR(36)` |
| `nvarchar`, `ntext` | `TEXT` |
| `image`, `varbinary` | `BINARY` |

**`sp_validate_table(hostname, database_name, src_schema, table_name, target_schema)`**
Runs two checks:
- **Count check** — `SELECT COUNT(*)` on both source and target; compares values
- **Hash check** — `HASHBYTES('MD5', (SELECT * FROM table FOR XML AUTO))` on MS SQL; `MD5(LISTAGG(TO_JSON(OBJECT_CONSTRUCT(*))))` on Snowflake; compares values

Returns:
```json
{
  "table": "Orders",
  "source_count": 50000,
  "target_count": 50000,
  "count_match": true,
  "hash_match": null
}
```
`hash_match` is `null` when the table is too large for XML serialisation (MS SQL 2 GB XML limit) or when row ordering differs between source and target.

---

### 4.5 Streamlit UI

A four-tab browser application embedded in the Native App, running inside Snowflake's Streamlit hosting.

**⚙️ Setup tab**
- Input fields: hostname, database name, username, password
- "Save Credentials" — recreates the `mssql_secret` and `mssql_network_rule` objects with the consumer's actual values
- "Test Connection" — calls `sp_discover` and reports success/failure

**🔍 Discovery tab**
- "Discover Objects" — calls `sp_discover` and renders the full object inventory as a table
- Multi-select widget to choose which tables to migrate
- Selected tables are stored in `st.session_state` for use in subsequent tabs

**🚀 Migration tab**
- Target schema input and chunk size input
- "Start Migration" — iterates over selected tables, calls `sp_migrate_table` for each, updates a `st.progress` bar, and displays a per-table results table

**✅ Validation tab**
- "Run Validation" — calls `sp_validate_table` for each migrated table
- Results rendered as a styled dataframe: green cells for `True`, red cells for `False`, grey for `null`

---

## 5. Data Flow

```
Consumer opens Streamlit
        │
        ▼
[Setup Tab] Enter hostname, database, username, password
        │  Saves to mssql_secret + mssql_network_rule
        │
        ▼
[Discovery Tab] Click "Discover Objects"
        │  CALL sp_discover(hostname, database)
        │    └─ pyodbc → INFORMATION_SCHEMA.TABLES / ROUTINES
        │  Returns JSON → rendered as table
        │  Consumer selects tables to migrate
        │
        ▼
[Migration Tab] Click "Start Migration"
        │  For each selected table:
        │    CALL sp_migrate_table(...)
        │      └─ pd.read_sql in chunks
        │           └─ session.write_pandas → Snowflake table
        │  Progress bar updates per table
        │
        ▼
[Validation Tab] Click "Run Validation"
        │  For each migrated table:
        │    CALL sp_validate_table(...)
        │      ├─ COUNT(*) on MS SQL  vs  COUNT(*) on Snowflake
        │      └─ MD5 hash on MS SQL  vs  MD5 hash on Snowflake
        │  Results table: green ✅ / red ❌ per check
        ▼
        Done
```

---

## 6. File Reference

```
migration_acc/
│
├── manifest.yml              # Native App manifest: version, artifacts, privileges, EAI reference
├── setup.sql                 # All Snowflake DDL: role, schema, network rule, secret, EAI,
│                             # SPCS service, 3 stored procedures, Streamlit object
├── documentation.md          # This file
├── README.md                 # Quick-start guide and Marketplace checklist
│
├── scripts/
│   └── azure_migration_user.sql   # Run on Azure MS SQL to create the read-only migration login
│
├── app/
│   ├── streamlit_app.py      # Four-tab Streamlit UI
│   └── environment.yml       # Conda dependencies for the Streamlit runtime
│
├── procedures/
│   ├── sp_discover.py        # Snowpark procedure: inventory source objects
│   ├── sp_migrate.py         # Snowpark procedure: chunked full-load migration
│   └── sp_validate.py        # Snowpark procedure: count + hash validation
│
└── scai_service/
    ├── Dockerfile            # Ubuntu 22.04 + Python 3.11 + scai binary + FastAPI
    ├── main.py               # FastAPI app: POST /translate, GET /health
    └── requirements.txt      # fastapi, uvicorn
```

---

## 7. Setup & Deployment Guide

### Step 1 — Azure: Allowlist Snowflake IPs

Run in Snowflake to get the IP list:
```sql
SELECT value:host::STRING AS host, value:port::STRING AS port
FROM TABLE(FLATTEN(input => PARSE_JSON(SYSTEM$ALLOWLIST())))
WHERE value:type::STRING = 'SNOWFLAKE_DEPLOYMENT';
```
Add each IP in **Azure Portal → SQL Server → Networking → Firewall rules**.

### Step 2 — Azure: Create Migration User

Connect to your Azure SQL Server in SSMS. Run against `master`:
```sql
CREATE LOGIN migration_user WITH PASSWORD = 'YourStrongPassword!';
```
Then open a new connection to your target database and run:
```sql
CREATE USER migration_user FOR LOGIN migration_user;
ALTER ROLE db_datareader ADD MEMBER migration_user;
GRANT VIEW DEFINITION TO migration_user;
```

### Step 3 — Snowflake: Run setup.sql

In a Snowflake worksheet (as ACCOUNTADMIN), run `setup.sql` in full. This creates all app objects. Replace `<ORGNAME>-<ACCTNAME>` in Section 7 with your actual Snowflake account identifier before running.

Get your identifiers:
```sql
SELECT CURRENT_ORGANIZATION_NAME(), CURRENT_ACCOUNT_NAME();
```

### Step 4 — Build & Push the Docker Image

Requires: Snowflake CLI + Docker Desktop installed locally.

SnowConvert AI CLI (`scai`) is free — no license key required. The Dockerfile installs it automatically.

```bash
# Authenticate Docker to Snowflake's image registry
snow spcs image-registry login

# Build (from the migration_acc/ directory — no --build-arg needed)
docker build \
  -t <ORGNAME>-<ACCTNAME>.registry.snowflakecomputing.com/app_schema/scai_repo/scai_service:latest \
  scai_service/

# Push
docker push <ORGNAME>-<ACCTNAME>.registry.snowflakecomputing.com/app_schema/scai_repo/scai_service:latest
```

### Step 5 — Upload Procedure Files to Stage

```bash
snow stage copy procedures/sp_discover.py @app_schema.app_stage/
snow stage copy procedures/sp_migrate.py  @app_schema.app_stage/
snow stage copy procedures/sp_validate.py @app_schema.app_stage/
snow stage copy app/streamlit_app.py      @app_schema.app_stage/app/
snow stage copy app/environment.yml       @app_schema.app_stage/app/
```

### Step 6 — Deploy

```bash
snow app run
```

For Marketplace submission, run `snow app validate` first and resolve any errors before submitting.

---

## 8. Known Limitations

| Limitation | Detail |
|---|---|
| **Hash validation is row-order sensitive** | `count_match: true` with `hash_match: false` usually means row ordering differs between source and target, not data corruption. Treat `count_match` as the authoritative check. |
| **Hash validation skipped for very large tables** | MS SQL's `FOR XML AUTO` serialisation has a 2 GB limit. Tables exceeding this return `hash_match: null`. |
| **SPCS availability** | SPCS must be enabled in the consumer's Snowflake account and region. If the compute pool fails to start, the app displays a clear error. SPCS is not available in all Snowflake regions. |
| **One-time full load only** | No incremental/CDC support. Re-running migration on an existing table overwrites it completely. |
| **Sequential table migration** | Tables are migrated one at a time. For databases with hundreds of tables, total migration time is the sum of all individual table migration times. |
| **pyodbc ODBC driver** | The Snowpark Python runtime must have ODBC Driver 18 for SQL Server available. This is included in the `pyodbc` Snowflake Anaconda package. |
