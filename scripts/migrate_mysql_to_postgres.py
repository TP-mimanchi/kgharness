"""Helpers for the guarded legacy MySQL-to-PostgreSQL migration.

The data-copy command was removed when PostgreSQL became the primary store, but the
type mapping remains the compatibility contract used by migration validation.
"""

from __future__ import annotations

from typing import Any


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
    if data_type == "float":
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
    if data_type in {
        "binary",
        "varbinary",
        "blob",
        "tinyblob",
        "mediumblob",
        "longblob",
    }:
        return "bytea"
    if data_type == "json":
        return "jsonb"
    return "text"
