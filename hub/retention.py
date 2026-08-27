"""Retention — ocpp_messages 7 хоног (§5), meter_samples 90 хоног.
Цагт нэг удаа багцалж устгана (нэг том DELETE-ээр lock барихгүй)."""
import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import text as sql_text

from .config import settings
from .database import SessionLocal

log = logging.getLogger("evhub.retention")

_BATCH = 5000


async def retention_forever():
    while True:
        try:
            _cleanup("ocpp_messages", settings.ocpp_messages_keep_days)
            _cleanup("meter_samples", settings.meter_values_keep_days)
            _cleanup_done_rows()
        except Exception:
            log.exception("retention алдаа")
        await asyncio.sleep(3600)


def _cleanup(table: str, keep_days: int):
    cutoff = datetime.utcnow() - timedelta(days=keep_days)
    db = SessionLocal()
    try:
        while True:
            res = db.execute(sql_text(
                f"DELETE FROM {table} WHERE id IN "
                f"(SELECT id FROM {table} WHERE created_at < :cutoff LIMIT :n)"),
                {"cutoff": cutoff, "n": _BATCH})
            db.commit()
            if res.rowcount < _BATCH:
                break
        if res.rowcount:
            log.info("%s: %s хоногоос хуучин мөрүүд цэвэрлэгдэв", table, keep_days)
    finally:
        db.close()


def _cleanup_done_rows():
    """Хүргэгдсэн hub_events + дууссан командуудыг 14 хоногийн дараа устгана."""
    cutoff = datetime.utcnow() - timedelta(days=14)
    db = SessionLocal()
    try:
        db.execute(sql_text(
            "DELETE FROM hub_events WHERE status='DONE' AND created_at < :c"),
            {"c": cutoff})
        db.execute(sql_text(
            "DELETE FROM charger_commands WHERE status IN ('DONE','FAILED','EXPIRED') "
            "AND created_at < :c"), {"c": cutoff})
        db.commit()
    finally:
        db.close()
