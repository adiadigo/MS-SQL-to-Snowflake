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


def sp_discover(session, hostname: str, database_name: str) -> str:
    conn = _get_conn(hostname, database_name)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE IN ('BASE TABLE', 'VIEW')
        ORDER BY TABLE_SCHEMA, TABLE_NAME
    """)
    objects = [
        {"schema": r[0], "name": r[1], "type": r[2], "row_count": None}
        for r in cursor.fetchall()
    ]

    cursor.execute("""
        SELECT ROUTINE_SCHEMA, ROUTINE_NAME, ROUTINE_TYPE
        FROM INFORMATION_SCHEMA.ROUTINES
        ORDER BY ROUTINE_SCHEMA, ROUTINE_NAME
    """)
    objects += [
        {"schema": r[0], "name": r[1], "type": r[2], "row_count": None}
        for r in cursor.fetchall()
    ]

    conn.close()
    return json.dumps(objects)
