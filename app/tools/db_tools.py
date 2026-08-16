"""Read-only PostgreSQL tools for the structured business-data subagent."""

from __future__ import annotations

import os
import re

from dotenv import load_dotenv
from langchain_core.tools import tool
from psycopg import Error, connect, sql

from app.api.monitor import monitor
from app.rag.config import settings as rag_settings

load_dotenv()

BUSINESS_SCHEMA = os.getenv("BUSINESS_DB_SCHEMA", "business")
FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|merge|alter|drop|truncate|create|grant|revoke|copy|call|do|vacuum|analyze|refresh|reindex|cluster|comment|security|pg_read_file|pg_write_file|lo_import|dblink)\b",
    re.IGNORECASE,
)


def _connect_readonly():
    dsn = os.getenv("BUSINESS_DATABASE_URL", rag_settings.database_url)
    connection = connect(dsn, autocommit=True)
    connection.execute("SET default_transaction_read_only = on")
    connection.execute(
        "SELECT set_config('statement_timeout', %s, false)",
        (os.getenv("BUSINESS_DB_TIMEOUT_MS", "10000"),),
    )
    return connection


def _csv_result(cursor) -> str:
    if not cursor.description:
        return "查询没有返回结果集。"
    columns = [column.name for column in cursor.description]
    rows = cursor.fetchmany(500)
    lines = [",".join(columns)]
    lines.extend(",".join("" if value is None else str(value) for value in row) for row in rows)
    return "\n".join(lines)


def _validate_readonly_query(query: str) -> str:
    normalized = query.strip()
    if normalized.endswith(";"):
        normalized = normalized[:-1].strip()
    if ";" in normalized:
        raise ValueError("不允许执行多条 SQL")
    if not re.match(r"^(select|with|explain|show)\b", normalized, re.IGNORECASE):
        raise ValueError("只允许 SELECT、WITH、EXPLAIN 或 SHOW 查询")
    if FORBIDDEN_SQL.search(normalized) or re.search(r"\bfor\s+update\b", normalized, re.I):
        raise ValueError("查询包含禁止的写入或高风险语句")
    if re.search(r"\brag_(tenants|knowledge_bases|documents|chunks|ingestion_jobs)\b", normalized, re.I):
        raise ValueError("数据库助手不能访问 RAG 系统表")
    return normalized


@tool
def list_sql_tables() -> str:
    """列出 PostgreSQL 业务 Schema 中可查询的表。"""
    monitor.report_tool(tool_name="PostgreSQL表名查询工具：list_sql_tables", args={})
    try:
        with _connect_readonly() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema = %s AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                    """,
                    (BUSINESS_SCHEMA,),
                )
                tables = [row[0] for row in cursor.fetchall()]
                return f"可用的表有：{', '.join(tables)}" if tables else "没有可用的表"
    except (Error, ValueError) as error:
        return f"查询出现异常：{error}"


@tool
def get_table_data(table_name: str) -> str:
    """安全预览 PostgreSQL 业务表的前 100 行。"""
    monitor.report_tool(
        tool_name="PostgreSQL表数据查询工具：get_table_data",
        args={"table_name": table_name},
    )
    try:
        with _connect_readonly() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
                    (BUSINESS_SCHEMA, table_name),
                )
                if not cursor.fetchone():
                    return f"数据表 {table_name} 不存在或不可访问。"
                cursor.execute(
                    sql.SQL("SELECT * FROM {}.{} LIMIT 100").format(
                        sql.Identifier(BUSINESS_SCHEMA), sql.Identifier(table_name)
                    )
                )
                return _csv_result(cursor)
    except (Error, ValueError) as error:
        return f"查询出现异常：{error}"


@tool
def execute_sql_query(query: str) -> str:
    """执行经过只读校验的 PostgreSQL 查询，最多返回 500 行。"""
    monitor.report_tool(
        tool_name="PostgreSQL只读查询工具：execute_sql_query", args={"query": query}
    )
    try:
        normalized = _validate_readonly_query(query)
        with _connect_readonly() as connection:
            with connection.cursor() as cursor:
                cursor.execute(normalized)
                return _csv_result(cursor)
    except (Error, ValueError) as error:
        return f"查询出现异常：{error}"
