import streamlit as st
import pandas as pd
import json
from snowflake.snowpark.context import get_active_session

session = get_active_session()

st.title("MS SQL → Snowflake Migration Tool")

tab_setup, tab_discover, tab_migrate, tab_validate = st.tabs(
    ["⚙️ Setup", "🔍 Discovery", "🚀 Migration", "✅ Validation"]
)

# ── Setup ──────────────────────────────────────────────────────────────────
with tab_setup:
    st.subheader("Azure MS SQL Connection")
    hostname = st.text_input("Hostname", placeholder="myserver.database.windows.net")
    database = st.text_input("Database Name")
    username = st.text_input("Migration User")
    password = st.text_input("Password", type="password")

    if st.button("Save Credentials"):
        session.sql(f"""
            CREATE OR REPLACE SECRET app_schema.mssql_secret
                TYPE = PASSWORD
                USERNAME = '{username}'
                PASSWORD = '{password}'
        """).collect()
        session.sql(f"""
            CREATE OR REPLACE NETWORK RULE app_schema.mssql_network_rule
                TYPE = HOST_PORT MODE = EGRESS
                VALUE_LIST = ('{hostname}:1433')
        """).collect()
        st.session_state.update({"hostname": hostname, "database": database})
        st.success("Credentials and network rule saved.")

    if st.button("Test Connection"):
        h = st.session_state.get("hostname", hostname)
        d = st.session_state.get("database", database)
        if not h or not d:
            st.error("Enter hostname and database first.")
        else:
            try:
                raw = session.sql(f"CALL app_schema.sp_discover('{h}', '{d}')").collect()[0][0]
                st.success(f"Connected — {len(json.loads(raw))} objects found.")
            except Exception as e:
                st.error(f"Connection failed: {e}")

# ── Discovery ──────────────────────────────────────────────────────────────
with tab_discover:
    st.subheader("Source Object Inventory")
    if st.button("Discover Objects"):
        h = st.session_state.get("hostname", "")
        d = st.session_state.get("database", "")
        if not h:
            st.warning("Complete Setup tab first.")
        else:
            try:
                raw = session.sql(f"CALL app_schema.sp_discover('{h}', '{d}')").collect()[0][0]
                st.session_state["discovered"] = pd.DataFrame(json.loads(raw))
            except Exception as e:
                st.error(str(e))

    if "discovered" in st.session_state:
        df = st.session_state["discovered"]
        st.dataframe(df, use_container_width=True)
        tables = df[df["type"] == "BASE TABLE"].apply(
            lambda r: f"{r['schema']}.{r['name']}", axis=1
        ).tolist()
        selected = st.multiselect("Select tables to migrate", options=tables)
        if selected:
            st.session_state["selected_tables"] = selected

# ── Migration ──────────────────────────────────────────────────────────────
with tab_migrate:
    st.subheader("Run Migration")
    target_schema = st.text_input("Target Snowflake Schema", value="PUBLIC")
    chunk_size = st.number_input("Chunk Size (rows)", value=100000, step=10000)

    if st.button("Start Migration"):
        selected = st.session_state.get("selected_tables", [])
        h = st.session_state.get("hostname", "")
        d = st.session_state.get("database", "")
        if not selected:
            st.warning("Select tables in the Discovery tab first.")
        else:
            results, progress = [], st.progress(0)
            for i, full_name in enumerate(selected):
                src_schema, table = full_name.split(".", 1)
                with st.spinner(f"Migrating {full_name}…"):
                    try:
                        raw = session.sql(
                            f"CALL app_schema.sp_migrate_table("
                            f"'{h}', '{d}', '{src_schema}', '{table}', {chunk_size}, '{target_schema}')"
                        ).collect()[0][0]
                        res = {**json.loads(raw), "table": full_name}
                    except Exception as e:
                        res = {"table": full_name, "status": "error", "message": str(e)}
                    results.append(res)
                progress.progress((i + 1) / len(selected))
            st.session_state["migration_results"] = results
            st.dataframe(pd.DataFrame(results), use_container_width=True)

# ── Validation ─────────────────────────────────────────────────────────────
with tab_validate:
    st.subheader("Validation Results")
    val_schema = st.text_input("Target Schema to Validate", value="PUBLIC", key="val_schema")

    if st.button("Run Validation"):
        selected = st.session_state.get("selected_tables", [])
        h = st.session_state.get("hostname", "")
        d = st.session_state.get("database", "")
        if not selected:
            st.warning("Run migration first.")
        else:
            val_results = []
            for full_name in selected:
                src_schema, table = full_name.split(".", 1)
                try:
                    raw = session.sql(
                        f"CALL app_schema.sp_validate_table("
                        f"'{h}', '{d}', '{src_schema}', '{table}', '{val_schema}')"
                    ).collect()[0][0]
                    val_results.append(json.loads(raw))
                except Exception as e:
                    val_results.append({
                        "table": full_name, "count_match": False,
                        "hash_match": False, "message": str(e),
                    })

            df = pd.DataFrame(val_results)

            def _color(val):
                if val is True:
                    return "background-color:#d4edda;color:#155724"
                if val is False:
                    return "background-color:#f8d7da;color:#721c24"
                return ""

            st.dataframe(
                df.style.applymap(_color, subset=["count_match", "hash_match"]),
                use_container_width=True,
            )
