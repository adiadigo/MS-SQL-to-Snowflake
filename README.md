# MS SQL → Snowflake Migration Native App

## Prerequisites

### 1. Identify Snowflake Outbound IPs

Run this in a Snowflake worksheet and add every returned IP to your Azure firewall:

```sql
SELECT value:host::STRING AS host, value:port::STRING AS port
FROM TABLE(FLATTEN(input => PARSE_JSON(SYSTEM$ALLOWLIST())))
WHERE value:type::STRING = 'SNOWFLAKE_DEPLOYMENT';
```

In the **Azure Portal**: navigate to your SQL Server → **Networking** → **Firewall rules** → add each IP as a rule.

### 2. Create the Migration User on Azure MS SQL

Run `scripts/azure_migration_user.sql` against your Azure SQL instance as a `sysadmin`.  
This creates a login with `db_datareader` + `VIEW DEFINITION` — read-only, no write access.

### 3. Build & Push the SnowConvert Docker Image

SnowConvert AI CLI (`scai`) is **free** — no license key or download URL required.
The Dockerfile installs it automatically via the official installer script.

```bash
# Authenticate to your Snowflake image registry
snow spcs image-registry login

# Build (no --build-arg needed)
docker build \
  -t <ORGNAME>-<ACCTNAME>.registry.snowflakecomputing.com/app_schema/scai_repo/scai_service:latest \
  scai_service/

# Push
docker push <ORGNAME>-<ACCTNAME>.registry.snowflakecomputing.com/app_schema/scai_repo/scai_service:latest
```

> **Note:** `scai` authenticates to Snowflake using your Snowflake CLI connection
> (`snow connection test`). The SPCS container inherits the service's Snowflake
> identity automatically — no additional credential configuration is needed.

### 4. Upload Procedure Files to Stage

```bash
snow stage copy procedures/sp_discover.py @app_schema.app_stage/
snow stage copy procedures/sp_migrate.py  @app_schema.app_stage/
snow stage copy procedures/sp_validate.py @app_schema.app_stage/
snow stage copy app/streamlit_app.py      @app_schema.app_stage/app/
snow stage copy app/environment.yml       @app_schema.app_stage/app/
```

### 5. Deploy (dev account)

```bash
snow app run
```

---

## Architecture

```
Streamlit (4 tabs)
  └── Snowpark Stored Procedures (sp_discover, sp_migrate_table, sp_validate_table)
        ├── External Access Integration → Azure MS SQL :1433  (pyodbc)
        └── SPCS scai_service           → SnowConvert CLI     (FastAPI)
```

---

## Known Limitations

- **Hash validation** is row-order sensitive. `count_match: true` with `hash_match: false`
  typically indicates ordering differences, not data corruption. Always treat `count_match`
  as the authoritative check.
- **SPCS** must be enabled in the consumer's Snowflake account. If the compute pool fails
  to start, the Discovery tab will display a clear error message.
- The MS SQL `HASHBYTES('MD5', … FOR XML AUTO)` approach is limited to tables whose
  XML serialisation fits within SQL Server's 2 GB XML limit. For very large tables,
  hash validation is skipped and `hash_match` is returned as `null`.

---

## Marketplace Submission Checklist

- [ ] `snow app validate` passes with no errors
- [ ] `snow app run` installs cleanly in a fresh consumer account
- [ ] Account admin approval prompt appears for External Network Access
- [ ] All four Streamlit tabs function end-to-end
- [ ] Security scan completed via Snowflake partner portal
- [ ] Listing title, description, and screenshots prepared
- [ ] Pricing model selected (free / paid / consumption-based)
