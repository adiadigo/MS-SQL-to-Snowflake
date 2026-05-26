-- =============================================================================
-- setup.sql — MS SQL → Snowflake Migration Native App
-- All DDL is idempotent (CREATE … IF NOT EXISTS / CREATE OR REPLACE).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- SECTION 1: Application Role & Schema
-- ---------------------------------------------------------------------------
CREATE APPLICATION ROLE IF NOT EXISTS migration_role;

CREATE SCHEMA IF NOT EXISTS app_schema;
GRANT USAGE ON SCHEMA app_schema TO APPLICATION ROLE migration_role;

-- ---------------------------------------------------------------------------
-- SECTION 2: Stage (holds procedure source files)
-- ---------------------------------------------------------------------------
CREATE STAGE IF NOT EXISTS app_schema.app_stage
    DIRECTORY = (ENABLE = TRUE);

-- ---------------------------------------------------------------------------
-- SECTION 3: Network Rule  (placeholder — updated at runtime via Setup tab)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE NETWORK RULE app_schema.mssql_network_rule
    TYPE        = HOST_PORT
    MODE        = EGRESS
    VALUE_LIST  = ('placeholder.database.windows.net:1433');

-- ---------------------------------------------------------------------------
-- SECTION 4: Secret  (placeholder — updated at runtime via Setup tab)
-- ---------------------------------------------------------------------------
CREATE SECRET IF NOT EXISTS app_schema.mssql_secret
    TYPE     = PASSWORD
    USERNAME = 'migration_user'
    PASSWORD = 'placeholder';

-- ---------------------------------------------------------------------------
-- SECTION 5: External Access Integration
-- ---------------------------------------------------------------------------
CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION app_schema.migration_eai
    ALLOWED_NETWORK_RULES        = (app_schema.mssql_network_rule)
    ALLOWED_AUTHENTICATION_SECRETS = (app_schema.mssql_secret)
    ENABLED                      = TRUE;

GRANT USAGE ON INTEGRATION app_schema.migration_eai
    TO APPLICATION ROLE migration_role;

-- ---------------------------------------------------------------------------
-- SECTION 6: EAI Reference Callbacks  (required by manifest.yml references block)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE app_schema.register_eai_callback(
    ref_name    STRING,
    operation   STRING,
    ref_or_alias STRING
)
RETURNS STRING
LANGUAGE SQL
AS $$
BEGIN
    CASE operation
        WHEN 'ADD'    THEN SELECT SYSTEM$SET_REFERENCE(:ref_name, :ref_or_alias);
        WHEN 'REMOVE' THEN SELECT SYSTEM$REMOVE_REFERENCE(:ref_name);
        WHEN 'CLEAR'  THEN SELECT SYSTEM$REMOVE_REFERENCE(:ref_name);
    END CASE;
    RETURN 'SUCCESS';
END;
$$;

CREATE OR REPLACE PROCEDURE app_schema.get_eai_config()
RETURNS STRING
LANGUAGE SQL
AS $$
    SELECT OBJECT_CONSTRUCT(
        'type',    'CONFIGURATION',
        'payload', OBJECT_CONSTRUCT(
            'host_ports',       ARRAY_CONSTRUCT('placeholder.database.windows.net:1433'),
            'allowed_secrets',  'LIST'
        )
    )::STRING;
$$;

GRANT USAGE ON PROCEDURE app_schema.register_eai_callback(STRING, STRING, STRING)
    TO APPLICATION ROLE migration_role;
GRANT USAGE ON PROCEDURE app_schema.get_eai_config()
    TO APPLICATION ROLE migration_role;

-- ---------------------------------------------------------------------------
-- SECTION 7: SPCS — Compute Pool, Image Repository, Service, Service Function
-- NOTE: Replace <ORGNAME>-<ACCTNAME> with your Snowflake account identifier
--       before running, or set via snow app run --variable.
-- ---------------------------------------------------------------------------
CREATE COMPUTE POOL IF NOT EXISTS scai_pool
    MIN_NODES          = 1
    MAX_NODES          = 1
    INSTANCE_FAMILY    = CPU_X64_XS
    AUTO_RESUME        = TRUE
    AUTO_SUSPEND_SECS  = 300;

CREATE IMAGE REPOSITORY IF NOT EXISTS app_schema.scai_repo;

CREATE SERVICE IF NOT EXISTS app_schema.scai_service
    IN COMPUTE POOL scai_pool
    FROM SPECIFICATION $$
        spec:
          containers:
            - name: scai
              image: /<ORGNAME>-<ACCTNAME>/app_schema/scai_repo/scai_service:latest
              readinessProbe:
                port: 8000
                path: /health
          endpoints:
            - name: translate
              port: 8000
              public: false
    $$
    EXTERNAL_ACCESS_INTEGRATIONS = (app_schema.migration_eai);

CREATE OR REPLACE FUNCTION app_schema.translate_tsql(tsql VARCHAR)
    RETURNS VARCHAR
    SERVICE  = app_schema.scai_service
    ENDPOINT = translate
    AS '/translate';

GRANT USAGE ON FUNCTION app_schema.translate_tsql(VARCHAR)
    TO APPLICATION ROLE migration_role;
GRANT USAGE ON SERVICE app_schema.scai_service
    TO APPLICATION ROLE migration_role;

-- ---------------------------------------------------------------------------
-- SECTION 8: Discovery Stored Procedure
-- ---------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE app_schema.sp_discover(
    hostname      VARCHAR,
    database_name VARCHAR
)
RETURNS VARCHAR
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES        = ('snowflake-snowpark-python', 'pyodbc')
HANDLER         = 'sp_discover.sp_discover'
IMPORTS         = ('@app_schema.app_stage/sp_discover.py')
EXTERNAL_ACCESS_INTEGRATIONS = (app_schema.migration_eai)
SECRETS         = ('cred' = app_schema.mssql_secret);

GRANT USAGE ON PROCEDURE app_schema.sp_discover(VARCHAR, VARCHAR)
    TO APPLICATION ROLE migration_role;

-- ---------------------------------------------------------------------------
-- SECTION 9: Migration Stored Procedure
-- ---------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE app_schema.sp_migrate_table(
    hostname      VARCHAR,
    database_name VARCHAR,
    src_schema    VARCHAR,
    table_name    VARCHAR,
    chunk_size    INT,
    target_schema VARCHAR
)
RETURNS VARCHAR
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES        = ('snowflake-snowpark-python', 'pyodbc', 'pandas')
HANDLER         = 'sp_migrate.sp_migrate_table'
IMPORTS         = ('@app_schema.app_stage/sp_migrate.py')
EXTERNAL_ACCESS_INTEGRATIONS = (app_schema.migration_eai)
SECRETS         = ('cred' = app_schema.mssql_secret);

GRANT USAGE ON PROCEDURE app_schema.sp_migrate_table(VARCHAR, VARCHAR, VARCHAR, VARCHAR, INT, VARCHAR)
    TO APPLICATION ROLE migration_role;

-- ---------------------------------------------------------------------------
-- SECTION 10: Validation Stored Procedure
-- ---------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE app_schema.sp_validate_table(
    hostname      VARCHAR,
    database_name VARCHAR,
    src_schema    VARCHAR,
    table_name    VARCHAR,
    target_schema VARCHAR
)
RETURNS VARCHAR
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES        = ('snowflake-snowpark-python', 'pyodbc')
HANDLER         = 'sp_validate.sp_validate_table'
IMPORTS         = ('@app_schema.app_stage/sp_validate.py')
EXTERNAL_ACCESS_INTEGRATIONS = (app_schema.migration_eai)
SECRETS         = ('cred' = app_schema.mssql_secret);

GRANT USAGE ON PROCEDURE app_schema.sp_validate_table(VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR)
    TO APPLICATION ROLE migration_role;

-- ---------------------------------------------------------------------------
-- SECTION 11: Streamlit Object
-- ---------------------------------------------------------------------------
CREATE OR REPLACE STREAMLIT app_schema.migration_ui
    ROOT_LOCATION = '@app_schema.app_stage/app'
    MAIN_FILE     = 'streamlit_app.py';

GRANT USAGE ON STREAMLIT app_schema.migration_ui
    TO APPLICATION ROLE migration_role;
