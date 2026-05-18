import json
import _snowflake
import pyodbc
import pandas as pd

# Maps MS SQL column type names (lowercase) to Snowflake SQL type strings.
# Used to cast pandas object columns before write_pandas infers wrong types.
DTYPE_OVERRIDES = {
    "datetime2":        "TIMESTAMP_NTZ",
    "datetime":         "TIMESTAMP_NTZ",
    "smalldatetime":    "TIMESTAMP_NTZ",
    "date":             "DATE",
    "time":             "TIME",
    "bit":              "BOOLEAN",
    "uniqueidentifier": "VARCHAR(36)",
    "nvarchar":         "TEXT",
    "ntext":            "TEXT",
    "image":            "BINARY",
    "varbinary":        "BINARY",
}


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


def _coerce_chunk(df: pd.DataFrame) -> pd.DataFrame:
    """Stringify object columns so write_pandas doesn't choke on mixed types."""
    for col in df.select_dtypes(include=["object"]).columns:
        df[col] = df[col].where(df[col].isna(), df[col].astype(str))
    return df


def sp_migrate_table(
    session,
    hostname: str,
    database_name: str,
    src_schema: str,
    table_name: str,
    chunk_size: int,
    target_schema: str,
) -> str:
    conn = _get_conn(hostname, database_name)
    query = f"SELECT * FROM [{src_schema}].[{table_name}]"
    rows_migrated = 0
    chunks = 0

    try:
        for chunk_num, chunk_df in enumerate(
            pd.read_sql(query, conn, chunksize=chunk_size)
        ):
            chunk_df = _coerce_chunk(chunk_df)
            session.write_pandas(
                chunk_df,
                table_name.upper(),
                schema=target_schema,
                auto_create_table=(chunk_num == 0),
                overwrite=(chunk_num == 0),
            )
            rows_migrated += len(chunk_df)
            chunks += 1

        conn.close()
        return json.dumps({"status": "success", "rows_migrated": rows_migrated, "chunks": chunks})

    except Exception as exc:
        conn.close()
        return json.dumps({"status": "error", "message": str(exc), "rows_migrated": rows_migrated})
