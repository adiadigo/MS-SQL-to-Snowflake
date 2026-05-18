import json
import _snowflake
import pyodbc


def _get_conn(hostname: str, database_name: str):
    cred = _snowflake.get_username_password("cred")
    conn_str = (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={hostname},1433;"
        f"DATABASE={database_name};"
        f"UID={cred.username};"
        f"PWD={cred.password};"
        f"Encrypt=yes;TrustServerCertificate=no;"
    )
    return pyodbc.connect(conn_str, timeout=30)


def sp_validate_table(
    session,
    hostname: str,
    database_name: str,
    src_schema: str,
    table_name: str,
    target_schema: str,
) -> str:
    # ── Source (MS SQL) ────────────────────────────────────────────────────
    conn = _get_conn(hostname, database_name)
    cursor = conn.cursor()

    cursor.execute(f"SELECT COUNT(*) FROM [{src_schema}].[{table_name}]")
    source_count = cursor.fetchone()[0]

    # Best-effort hash — row-order sensitive (documented limitation)
    try:
        cursor.execute(
            f"SELECT CONVERT(VARCHAR(32), HASHBYTES('MD5', "
            f"(SELECT * FROM [{src_schema}].[{table_name}] FOR XML AUTO)), 2)"
        )
        source_hash = cursor.fetchone()[0]
    except Exception:
        source_hash = None

    conn.close()

    # ── Target (Snowflake) ─────────────────────────────────────────────────
    sf_table = f"{target_schema}.{table_name.upper()}"
    target_count = session.sql(f"SELECT COUNT(*) FROM {sf_table}").collect()[0][0]

    try:
        target_hash = session.sql(
            f"SELECT MD5(LISTAGG(TO_JSON(OBJECT_CONSTRUCT(*)), ',') "
            f"WITHIN GROUP (ORDER BY 1)) FROM {sf_table}"
        ).collect()[0][0]
    except Exception:
        target_hash = None

    return json.dumps({
        "table": table_name,
        "source_count": source_count,
        "target_count": target_count,
        "count_match": source_count == target_count,
        "hash_match": (source_hash == target_hash) if (source_hash and target_hash) else None,
    })
