"""One-time, guarded migration from the legacy MySQL database to PostgreSQL.

The target schema must be empty. This script intentionally never drops or truncates
existing target data, so rerunning it after a partial migration requires an operator
to inspect and clean the dedicated schema first.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any

import mysql.connector
import psycopg
from dotenv import load_dotenv
from psycopg import sql
from psycopg.types.json import Jsonb

load_dotenv()

SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _source_connection():
    return mysql.connector.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"],
        charset=os.getenv("MYSQL_CHARSET", "utf8mb4"),
        use_unicode=True,
    )


def _target_dsn() -> str:
    value = os.getenv("BUSINESS_DATABASE_URL") or os.getenv("RAG_DATABASE_URL")
    if not value:
        raise RuntimeError("BUSINESS_DATABASE_URL or RAG_DATABASE_URL is required")
    return value


def _postgres_type(column: dict[str, Any]) -> str:
    data_type = str(column["DATA_TYPE"]).lower()
    column_type = str(column["COLUMN_TYPE"]).lower()
    if data_type in {"tinyint", "smallint"}:
        return "smallint"
    if data_type in {"mediumint", "int", "integer"}:
        return "integer"
    if data_type == "bigint":
        return "numeric(20)" if "unsigned" in column_type else "bigint"
    if data_type in {"decimal", "numeric"}:
        precision = int(column.get("NUMERIC_PRECISION") or 38)
        scale = int(column.get("NUMERIC_SCALE") or 0)
        return f"numeric({min(precision, 1000)},{min(scale, precision)})"
    if data_type in {"float"}:
        return "real"
    if data_type in {"double", "real"}:
        return "double precision"
    if data_type in {"datetime", "timestamp"}:
        return "timestamp without time zone"
    if data_type == "date":
        return "date"
    if data_type == "time":
        return "time without time zone"
    if data_type == "year":
        return "smallint"
    if data_type in {"binary", "varbinary", "blob", "tinyblob", "mediumblob", "longblob"}:
        return "bytea"
    if data_type == "json":
        return "jsonb"
    return "text"


def _load_metadata(source) -> list[dict[str, Any]]:
    database = os.environ["MYSQL_DATABASE"]
    cursor = source.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT TABLE_NAME, ENGINE
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'
        ORDER BY TABLE_NAME
        """,
        (database,),
    )
    tables = []
    for table in cursor.fetchall():
        cursor.execute(
            """
            SELECT COLUMN_NAME, DATA_TYPE, COLUMN_TYPE, IS_NULLABLE,
                   NUMERIC_PRECISION, NUMERIC_SCALE, ORDINAL_POSITION
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            ORDER BY ORDINAL_POSITION
            """,
            (database, table["TABLE_NAME"]),
        )
        columns = cursor.fetchall()
        cursor.execute(
            """
            SELECT COLUMN_NAME, ORDINAL_POSITION
            FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
              AND CONSTRAINT_NAME = 'PRIMARY'
            ORDER BY ORDINAL_POSITION
            """,
            (database, table["TABLE_NAME"]),
        )
        primary_key = [row["COLUMN_NAME"] for row in cursor.fetchall()]
        tables.append(
            {"name": table["TABLE_NAME"], "columns": columns, "primary_key": primary_key}
        )
    cursor.close()
    return tables


def _ensure_empty_target(target, schema: str) -> None:
    with target.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s LIMIT 1",
            (schema,),
        )
        existing = cursor.fetchone()
        if existing:
            raise RuntimeError(
                f"target schema {schema!r} is not empty (found {existing[0]!r}); refusing to overwrite"
            )
        cursor.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))


def _adapt_value(value: Any, data_type: str) -> Any:
    if value is None or data_type.lower() != "json":
        return value
    if isinstance(value, (dict, list, int, float, bool)):
        return Jsonb(value)
    try:
        return Jsonb(json.loads(value))
    except (json.JSONDecodeError, TypeError):
        return Jsonb(value)


def migrate(batch_size: int = 500) -> dict[str, int]:
    schema = os.getenv("BUSINESS_DB_SCHEMA", "business")
    if not SAFE_IDENTIFIER.fullmatch(schema):
        raise ValueError("BUSINESS_DB_SCHEMA is not a safe PostgreSQL identifier")
    counts: dict[str, int] = {}
    with _source_connection() as source, psycopg.connect(_target_dsn()) as target:
        metadata = _load_metadata(source)
        _ensure_empty_target(target, schema)
        target.commit()
        for table in metadata:
            name = table["name"]
            columns = table["columns"]
            column_names = [column["COLUMN_NAME"] for column in columns]
            definitions = [
                sql.SQL("{} {} {}").format(
                    sql.Identifier(column["COLUMN_NAME"]),
                    sql.SQL(_postgres_type(column)),
                    sql.SQL("NOT NULL" if column["IS_NULLABLE"] == "NO" else ""),
                )
                for column in columns
            ]
            if table["primary_key"]:
                definitions.append(
                    sql.SQL("PRIMARY KEY ({})").format(
                        sql.SQL(", ").join(
                            sql.Identifier(value) for value in table["primary_key"]
                        )
                    )
                )
            with target.cursor() as target_cursor:
                target_cursor.execute(
                    sql.SQL("CREATE TABLE {}.{} ({})").format(
                        sql.Identifier(schema),
                        sql.Identifier(name),
                        sql.SQL(", ").join(definitions),
                    )
                )
            source_cursor = source.cursor(raw=False)
            quoted_table = "`" + name.replace("`", "``") + "`"
            quoted_columns = ", ".join(
                "`" + value.replace("`", "``") + "`" for value in column_names
            )
            source_cursor.execute(f"SELECT {quoted_columns} FROM {quoted_table}")
            insert_statement = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
                sql.Identifier(schema),
                sql.Identifier(name),
                sql.SQL(", ").join(sql.Identifier(value) for value in column_names),
                sql.SQL(", ").join(sql.Placeholder() for _ in column_names),
            )
            copied = 0
            while rows := source_cursor.fetchmany(batch_size):
                adapted = [
                    tuple(
                        _adapt_value(value, columns[index]["DATA_TYPE"])
                        for index, value in enumerate(row)
                    )
                    for row in rows
                ]
                with target.cursor() as target_cursor:
                    target_cursor.executemany(insert_statement, adapted)
                copied += len(adapted)
            source_cursor.close()
            with target.cursor() as target_cursor:
                target_cursor.execute(
                    sql.SQL("SELECT count(*) FROM {}.{}").format(
                        sql.Identifier(schema), sql.Identifier(name)
                    )
                )
                target_count = int(target_cursor.fetchone()[0])
            if target_count != copied:
                raise RuntimeError(
                    f"row-count mismatch for {name}: source={copied}, target={target_count}"
                )
            target.commit()
            counts[name] = copied
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    result = migrate(batch_size=args.batch_size)
    print(json.dumps({"status": "ok", "row_counts": result}, ensure_ascii=False))
