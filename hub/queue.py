"""charger_commands дараалал — core → hub → цэнэглэгч (§3.3, DB + SKIP LOCKED).

1 секундын поллинг: PENDING командыг түгжиж аваад (SKIP LOCKED — олон worker
зэрэг ажилласан ч давхардахгүй), онлайн цэнэглэгч рүү Call болгон илгээж,
хариуг result-д бичнэ. Цэнэглэгч офлайн бол PENDING хэвээр хүлээнэ
(expires_at хүртэл).
"""
import asyncio
import logging
from datetime import datetime

from sqlalchemy import text as sql_text

from .config import settings
from .database import SessionLocal, engine
from .models import Charger, ChargerCommand
from .ocpp.handlers import remember_idtag
from .ocpp.registry import registry

log = logging.getLogger("evhub.queue")

_SENDABLE = {
    "RemoteStartTransaction", "RemoteStopTransaction", "ChangeConfiguration",
    "GetConfiguration", "SetChargingProfile", "ClearChargingProfile",
    "Reset", "UnlockConnector", "TriggerMessage",
}


def _claim_commands(db, limit: int = 10) -> list[str]:
    """PENDING командуудын id-г SKIP LOCKED-оор түгжиж SENT болгоно.
    SQLite (тест) дээр SKIP LOCKED байхгүй тул энгийн UPDATE-аар унана."""
    dialect = engine.dialect.name
    if dialect == "postgresql":
        rows = db.execute(sql_text(
            "SELECT id FROM charger_commands WHERE status='PENDING' "
            "ORDER BY created_at LIMIT :n FOR UPDATE SKIP LOCKED"), {"n": limit}).fetchall()
    else:
        rows = db.execute(sql_text(
            "SELECT id FROM charger_commands WHERE status='PENDING' "
            "ORDER BY created_at LIMIT :n"), {"n": limit}).fetchall()
    ids = [r[0] for r in rows]
    if ids:
        db.query(ChargerCommand).filter(ChargerCommand.id.in_(ids)).update(
            {"status": "SENT", "sent_at": datetime.utcnow()},
            synchronize_session=False)
    db.commit()
    return ids


async def process_commands_forever():
    while True:
        try:
            await _tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("командын дарааллын worker алдаа")
        await asyncio.sleep(settings.command_poll_seconds)


async def _tick():
    db = SessionLocal()
    try:
        # Хугацаа нь дууссан PENDING командуудыг EXPIRED болгоно
        db.query(ChargerCommand).filter(
            ChargerCommand.status == "PENDING",
            ChargerCommand.expires_at.isnot(None),
            ChargerCommand.expires_at < datetime.utcnow(),
        ).update({"status": "EXPIRED", "done_at": datetime.utcnow()},
                 synchronize_session=False)
        db.commit()

        ids = _claim_commands(db)
        if not ids:
            return
        for cmd_id in ids:
            cmd = db.get(ChargerCommand, cmd_id)
            if not cmd:
                continue
            await _execute(db, cmd)
    finally:
        db.close()


async def _execute(db, cmd: ChargerCommand):
    charger = db.get(Charger, cmd.charger_id)
    if not charger:
        cmd.status = "FAILED"
        cmd.result = {"error": "charger олдсонгүй"}
        cmd.done_at = datetime.utcnow()
        db.commit()
        return
    if cmd.action not in _SENDABLE:
        cmd.status = "FAILED"
        cmd.result = {"error": f"дэмжигдэхгүй үйлдэл: {cmd.action}"}
        cmd.done_at = datetime.utcnow()
        db.commit()
        return
    conn = registry.get(charger.cp_id)
    if not conn:
        # Офлайн — буцааж PENDING болгоод дараагийн tick-д хүлээнэ
        cmd.status = "PENDING"
        cmd.sent_at = None
        db.commit()
        return
    # RemoteStart-ийн idTag-ийг санаж авна: удахгүй ирэх Authorize/
    # StartTransaction-д core-оос дахин асуулгүй Accepted өгнө
    if cmd.action == "RemoteStartTransaction":
        id_tag = str((cmd.payload or {}).get("idTag") or "")
        if id_tag:
            remember_idtag(charger.cp_id, id_tag)
    cmd.attempts += 1
    db.commit()
    try:
        result = await conn.send_call(cmd.action, cmd.payload or {},
                                      timeout=settings.ocpp_call_timeout)
        cmd.status = "DONE"
        cmd.result = result
        cmd.done_at = datetime.utcnow()
        log.info("cp=%s %s → %s", charger.cp_id, cmd.action, result)
    except (asyncio.TimeoutError, ConnectionError, RuntimeError) as e:
        if cmd.attempts >= cmd.max_attempts:
            cmd.status = "FAILED"
            cmd.result = {"error": str(e)}
            cmd.done_at = datetime.utcnow()
            log.warning("cp=%s %s НУРСАН (%s оролдлого): %s",
                        charger.cp_id, cmd.action, cmd.attempts, e)
        else:
            cmd.status = "PENDING"   # дахин оролдоно
            cmd.sent_at = None
            cmd.result = {"error": str(e)}
    db.commit()
