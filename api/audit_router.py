"""
api/audit_router.py
-------------------
FastAPI router for audit log access and export.

Routes
------
GET  /api/audit/logs              Recent logs (admin = all, others = own)
GET  /api/audit/logs/app/{name}   Logs for a specific app
GET  /api/audit/denied            Recent denied access attempts
GET  /api/audit/export            Download audit log as CSV
"""

from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
import io

from sqlalchemy.orm import Session

from models.database import get_db, User
from models.schemas import AuditLogOut
from auth.rbac import (
    get_current_active_user, is_infra_admin, check_app_access,
)
from utils.audit import (
    get_recent_logs, get_logs_for_app,
    get_denied_attempts, export_audit_csv,
)

router = APIRouter(prefix="/api/audit", tags=["Audit Log"])


@router.get("/logs", response_model=List[AuditLogOut])
def list_audit_logs(
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Return recent audit log entries.
    infra-admin sees all users; others see only their own entries.
    """
    user_id = None if is_infra_admin(current_user) else current_user.id
    return get_recent_logs(db=db, user_id=user_id, limit=limit)


@router.get("/logs/app/{app_name}", response_model=List[AuditLogOut])
def app_audit_logs(
    app_name: str,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return audit log entries for a specific application."""
    if not check_app_access(current_user, app_name, db):
        raise HTTPException(status_code=403, detail=f"Access denied to '{app_name}'.")
    return get_logs_for_app(app_name=app_name, db=db, limit=limit)


@router.get("/denied", response_model=List[AuditLogOut])
def denied_attempts(
    hours: int = Query(24, ge=1, le=168),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Return access-denied events in the last N hours.
    Useful for security monitoring. infra-admin only.
    """
    if not is_infra_admin(current_user):
        raise HTTPException(status_code=403, detail="infra-admin only.")
    return get_denied_attempts(db=db, hours=hours)


@router.get("/export")
def export_csv(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Export audit log as a CSV file.
    infra-admin only. Useful for compliance reporting.
    """
    if not is_infra_admin(current_user):
        raise HTTPException(status_code=403, detail="infra-admin only.")

    csv_data = export_audit_csv(db=db, start_date=start_date, end_date=end_date)
    filename = f"k8s_ai_audit_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"

    return StreamingResponse(
        io.StringIO(csv_data),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

    @router.post("/log")
    def log_frontend_event(
        action: str,
        resource_type: Optional[str] = None,
        resource_name: Optional[str] = None,
        app_name: Optional[str] = None,
        namespace: Optional[str] = None,
        query_text: Optional[str] = None,
        result_summary: Optional[str] = None,
        success: bool = True,
        extra: Optional[dict] = None,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_active_user),
    ):
        """
        Frontend endpoint to log user actions (reads, mutations, etc).
        Creates audit log entry with frontend event details.
        """
        from models.database import AuditLog
        log_entry = AuditLog(
            user_id=current_user.id,
            username=current_user.username,
            action=action,
            resource_type=resource_type,
            resource_name=resource_name,
            app_name=app_name,
            namespace=namespace,
            query_text=query_text,
            result_summary=result_summary,
            success=success,
            extra=extra or {},
        )
        db.add(log_entry)
        db.commit()
        db.refresh(log_entry)
        return {"status": "logged", "log_id": log_entry.id}
