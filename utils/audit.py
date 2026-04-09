"""
utils/audit.py
--------------
Utility functions for querying and exporting audit logs.

Functions
---------
get_recent_logs(user_id, limit, db)         -> List[AuditLog]
get_logs_for_app(app_name, limit, db)       -> List[AuditLog]
get_denied_attempts(hours, db)              -> List[AuditLog]
export_audit_csv(start_date, end_date, db)  -> str
"""

import csv
import io
from datetime import datetime, timedelta
from typing import List, Optional
from sqlalchemy.orm import Session

from models.database import AuditLog


def get_recent_logs(
    db: Session,
    user_id: Optional[str] = None,
    limit: int = 50,
) -> List[AuditLog]:
    """Return the most recent audit log entries, optionally filtered by user."""
    q = db.query(AuditLog).order_by(AuditLog.timestamp.desc())
    if user_id:
        q = q.filter(AuditLog.user_id == user_id)
    return q.limit(limit).all()


def get_logs_for_app(app_name: str, db: Session, limit: int = 100) -> List[AuditLog]:
    """Return audit logs for a specific application."""
    return (
        db.query(AuditLog)
        .filter(AuditLog.app_name == app_name)
        .order_by(AuditLog.timestamp.desc())
        .limit(limit)
        .all()
    )


def get_denied_attempts(db: Session, hours: int = 24) -> List[AuditLog]:
    """Return all DENIED access attempts in the last N hours."""
    since = datetime.utcnow() - timedelta(hours=hours)
    return (
        db.query(AuditLog)
        .filter(AuditLog.action == "DENIED", AuditLog.timestamp >= since)
        .order_by(AuditLog.timestamp.desc())
        .all()
    )


def export_audit_csv(
    db: Session,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> str:
    """Export audit logs as a CSV string for compliance reporting."""
    q = db.query(AuditLog).order_by(AuditLog.timestamp.asc())
    if start_date:
        q = q.filter(AuditLog.timestamp >= start_date)
    if end_date:
        q = q.filter(AuditLog.timestamp <= end_date)
    logs = q.all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "timestamp", "username", "action", "resource_type",
        "app_name", "cluster_name", "namespace", "query_text",
        "result_summary", "success",
    ])
    for log in logs:
        writer.writerow([
            log.id, log.timestamp, log.username, log.action,
            log.resource_type, log.app_name, log.cluster_name,
            log.namespace, log.query_text, log.result_summary, log.success,
        ])
    return output.getvalue()
