import logging
import threading
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from mailfallback.models import BackgroundTask, TaskStatus

logger = logging.getLogger(__name__)

_task_progress: dict[str, dict] = {}
_running_lock = threading.Lock()


def start_task(db: Session, task_type: str, requested_by: str) -> BackgroundTask | None:
    with _running_lock:
        existing = (
            db.query(BackgroundTask)
            .filter(
                BackgroundTask.task_type == task_type,
                BackgroundTask.status.in_([TaskStatus.pending, TaskStatus.running]),
            )
            .first()
        )
        if existing:
            return None
        task = BackgroundTask(task_type=task_type, requested_by=requested_by)
        db.add(task)
        db.commit()
        db.refresh(task)
    return task


def get_latest_task(db: Session, task_type: str) -> dict:
    task = (
        db.query(BackgroundTask)
        .filter(BackgroundTask.task_type == task_type)
        .order_by(BackgroundTask.created_at.desc())
        .first()
    )
    if not task:
        return {"status": "idle", "task_type": task_type}

    result = {
        "task_type": task_type,
        "status": task.status.value,
        "progress_current": task.progress_current,
        "progress_total": task.progress_total,
        "details": task.details,
        "started_at": task.started_at,
        "completed_at": task.completed_at,
    }

    live = _task_progress.get(task.id)
    if live:
        result["progress_current"] = live.get("current", task.progress_current)
        result["progress_total"] = live.get("total", task.progress_total)
        result["user_statuses"] = live.get("user_statuses", [])
        result["current_user"] = live.get("current_user", "")

    return result


def run_fts_reindex(task_id: str) -> None:
    from mailfallback.db import SessionLocal

    db = SessionLocal()
    try:
        task = db.query(BackgroundTask).filter(BackgroundTask.id == task_id).first()
        if not task:
            return

        task.status = TaskStatus.running
        task.started_at = datetime.now(UTC)
        db.commit()

        from mailfallback.services.dovecot_manager import fts_rescan
        from mailfallback.services.user_service import list_users

        users = list_users(db)
        task.progress_total = len(users)
        db.commit()

        _task_progress[task_id] = {
            "total": len(users),
            "current": 0,
            "user_statuses": [],
            "current_user": "",
        }

        errors = []
        for i, user in enumerate(users):
            _task_progress[task_id]["current_user"] = user.username
            result = fts_rescan(user.username)
            status = "ok" if result["ok"] else "error"
            _task_progress[task_id]["user_statuses"].append(
                {"username": user.username, "status": status}
            )
            _task_progress[task_id]["current"] = i + 1
            if not result["ok"]:
                errors.append(f"{user.username}: {result['error']}")

            task.progress_current = i + 1
            if (i + 1) % 5 == 0:
                db.commit()

        task.status = TaskStatus.completed if not errors else TaskStatus.failed
        task.completed_at = datetime.now(UTC)
        task.details = {"errors": errors, "users_processed": len(users)}
        db.commit()
        logger.info("FTS reindex %s: %d users, %d errors", task_id, len(users), len(errors))

    except Exception as exc:
        logger.exception("FTS reindex %s failed", task_id)
        task = db.query(BackgroundTask).filter(BackgroundTask.id == task_id).first()
        if task:
            task.status = TaskStatus.failed
            task.completed_at = datetime.now(UTC)
            task.details = {"errors": [str(exc)]}
            db.commit()
    finally:
        _task_progress.pop(task_id, None)
        db.close()


def run_force_resync(task_id: str) -> None:
    from mailfallback.db import SessionLocal

    db = SessionLocal()
    try:
        task = db.query(BackgroundTask).filter(BackgroundTask.id == task_id).first()
        if not task:
            return

        task.status = TaskStatus.running
        task.started_at = datetime.now(UTC)
        db.commit()

        from mailfallback.services.dovecot_manager import force_resync
        from mailfallback.services.user_service import list_users

        users = list_users(db)
        task.progress_total = len(users)
        db.commit()

        _task_progress[task_id] = {
            "total": len(users),
            "current": 0,
            "user_statuses": [],
            "current_user": "",
        }

        errors = []
        for i, user in enumerate(users):
            _task_progress[task_id]["current_user"] = user.username
            result = force_resync(user.username)
            status = "ok" if result["ok"] else "error"
            _task_progress[task_id]["user_statuses"].append(
                {"username": user.username, "status": status}
            )
            _task_progress[task_id]["current"] = i + 1
            if not result["ok"]:
                errors.append(f"{user.username}: {result['error']}")

            task.progress_current = i + 1
            if (i + 1) % 5 == 0:
                db.commit()

        task.status = TaskStatus.completed if not errors else TaskStatus.failed
        task.completed_at = datetime.now(UTC)
        task.details = {"errors": errors, "users_processed": len(users)}
        db.commit()
        logger.info("Force resync %s: %d users, %d errors", task_id, len(users), len(errors))

    except Exception as exc:
        logger.exception("Force resync %s failed", task_id)
        task = db.query(BackgroundTask).filter(BackgroundTask.id == task_id).first()
        if task:
            task.status = TaskStatus.failed
            task.completed_at = datetime.now(UTC)
            task.details = {"errors": [str(exc)]}
            db.commit()
    finally:
        _task_progress.pop(task_id, None)
        db.close()


def submit_fts_reindex(db: Session, requested_by: str) -> BackgroundTask | None:
    task = start_task(db, "fts_reindex", requested_by)
    if not task:
        return None
    thread = threading.Thread(target=run_fts_reindex, args=(task.id,), daemon=True)
    thread.start()
    return task


def submit_force_resync(db: Session, requested_by: str) -> BackgroundTask | None:
    task = start_task(db, "force_resync", requested_by)
    if not task:
        return None
    thread = threading.Thread(target=run_force_resync, args=(task.id,), daemon=True)
    thread.start()
    return task
