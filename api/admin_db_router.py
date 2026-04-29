"""
api/admin_db_router.py
----------------------
Infra-admin only DB browser and read-only SQL query endpoints.
"""

from pathlib import Path
from typing import Any, List
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from auth.rbac import get_current_active_user, is_infra_admin
from models.database import User, get_db

router = APIRouter(prefix="/api/admin/db", tags=["Admin DB"])

SAFE_QUERY_RE = re.compile(r"^\s*(select|with|pragma|explain)\b", re.IGNORECASE)


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=10000)
    limit: int = Field(default=200, ge=1, le=1000)


def _ensure_infra_admin(current_user: User) -> None:
    if not is_infra_admin(current_user):
        raise HTTPException(status_code=403, detail="infra-admin only.")


def _validate_query(query: str) -> str:
    q = (query or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    if not SAFE_QUERY_RE.match(q):
        raise HTTPException(
            status_code=400,
            detail="Only read-only SELECT/WITH/PRAGMA/EXPLAIN queries are allowed.",
        )

    # Keep this endpoint safe by allowing one statement only.
    if ";" in q.rstrip(";"):
        raise HTTPException(status_code=400, detail="Only one SQL statement is allowed.")

    return q.rstrip(";")


@router.get("/tables")
def list_tables(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    _ensure_infra_admin(current_user)

    rows = db.execute(
        text(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            ORDER BY name
            """
        )
    ).fetchall()
    return {"tables": [r[0] for r in rows]}


@router.post("/query")
def run_readonly_query(
    body: QueryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    _ensure_infra_admin(current_user)
    sql = _validate_query(body.query)

    result = db.execute(text(sql))
    keys: List[str] = list(result.keys())
    rows = result.fetchmany(body.limit)

    payload: List[List[Any]] = [list(r) for r in rows]
    return {
        "columns": keys,
        "rows": payload,
        "row_count": len(payload),
        "limit": body.limit,
    }


@router.get("/ui", response_class=HTMLResponse, include_in_schema=False)
def serve_db_ui():

    template_path = Path(__file__).parent.parent / "ui" / "templates" / "admin_db.html"
    if not template_path.exists():
        raise HTTPException(status_code=404, detail="Admin DB UI template not found.")
    return HTMLResponse(content=template_path.read_text(encoding="utf-8"))
