"""EV Hub — OCPP 1.6J WSS терминаци (EV_CHARGING_PLAN.md §3).

Процессын бүтэц:
  • WS endpoint  /ocpp/1.6/{cp_id}  (мөн /ocpp/{cp_id} — nginx-ийн уян зам)
  • Internal API /internal/*        (core + админ, Bearer түлхүүр)
  • Background:  командын дараалал (1с поллинг), event outbox → core,
                 retention (цагт нэг)

Ажиллуулах: uvicorn hub.main:app --host 127.0.0.1 --port 8100
(deploy/evhub.service үүнийг systemd-ээр хийнэ)
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool
from starlette.websockets import WebSocketState

from . import core_client, queue, retention, sweeper
from .admin_api import router as internal_router
from .config import settings
from .database import Base, SessionLocal, engine
from .models import Charger, ChargerCommand, HubEvent
from .ocpp import handlers, protocol
from .ocpp.config_profile import boot_config_items
from .ocpp.registry import registry
from .auth import check_charger_auth

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("evhub.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    tasks = [
        asyncio.create_task(queue.process_commands_forever(), name="cmd-queue"),
        asyncio.create_task(core_client.deliver_events_forever(), name="events"),
        asyncio.create_task(retention.retention_forever(), name="retention"),
        asyncio.create_task(sweeper.sweep_forever(), name="orphan-sweeper"),
    ]
    log.info("EV Hub эхэллээ — port=%s, db=%s", settings.port,
             settings.database_url.split("@")[-1])
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()


app = FastAPI(title="EV Hub (OCPP 1.6J)", docs_url=None, redoc_url=None,
              lifespan=lifespan)
app.include_router(internal_router)


@app.get("/healthz")
def healthz():
    """nginx/systemd-ийн амьдын шалгалт (нээлттэй, нууцгүй)."""
    return {"ok": True, "connected": len(registry.online_ids())}


def _enqueue_boot_config(db, charger: Charger):
    """Boot ирмэгц §4.3-ийн тохиргоог ТУСДАА командуудаар дараалалд нэмнэ.
    Өмнөх PENDING config командуудыг давхардуулахгүй."""
    (db.query(ChargerCommand)
     .filter(ChargerCommand.charger_id == charger.id,
             ChargerCommand.action == "ChangeConfiguration",
             ChargerCommand.status == "PENDING")
     .update({"status": "EXPIRED", "done_at": datetime.utcnow()},
             synchronize_session=False))
    from datetime import timedelta
    for item in boot_config_items():
        db.add(ChargerCommand(charger_id=charger.id, action="ChangeConfiguration",
                              payload=item, requested_by="hub-boot",
                              expires_at=datetime.utcnow() + timedelta(minutes=10)))


def _handle_call_sync(cp_id: str, action: str, mid: str, payload: dict) -> str:
    """Нэг CALL-ийг бүрэн боловсруулна (thread pool дотор — sync DB).
    Ямар ч алдаанд WS холболтыг унагахгүй: CallError буцаана."""
    db = SessionLocal()
    try:
        charger = db.query(Charger).filter(Charger.cp_id == cp_id).first()
        if not charger:
            return protocol.call_error(mid, protocol.ERR_INTERNAL, "charger алга")
        handlers.log_message(db, cp_id, "in", action, mid, payload)
        fn = handlers.HANDLERS.get(action)
        if action == "Authorize":
            result = handlers.on_authorize(db, charger, payload,
                                           core_client.core_authorize_sync)
        elif fn:
            result = fn(db, charger, payload)
        else:
            db.commit()
            return protocol.call_error(mid, protocol.ERR_NOT_IMPLEMENTED, action)
        if action == "BootNotification":
            _enqueue_boot_config(db, charger)
        reply = protocol.call_result(mid, result)
        handlers.log_message(db, cp_id, "out", action, mid, result)
        db.commit()
        return reply
    except Exception:
        db.rollback()
        log.exception("cp=%s: %s боловсруулахад алдаа", cp_id, action)
        return protocol.call_error(mid, protocol.ERR_INTERNAL, "internal error")
    finally:
        db.close()


def _mark_disconnect(cp_id: str):
    db = SessionLocal()
    try:
        charger = db.query(Charger).filter(Charger.cp_id == cp_id).first()
        if charger:
            charger.last_disconnect_at = datetime.utcnow()
            db.add(HubEvent(kind="ev.offline", payload={"cp_id": cp_id}))
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


async def _ocpp_session(ws: WebSocket, cp_id: str):
    # ── Нэвтрэлт (Basic) — handshake-ийн Authorization header ──
    auth_header = ws.headers.get("authorization")
    db = SessionLocal()
    try:
        charger, reason = await run_in_threadpool(
            check_charger_auth, db, cp_id, auth_header)
    finally:
        db.close()
    if not charger:
        log.warning("cp=%s: нэвтрэлт амжилтгүй — %s", cp_id, reason)
        # RFC:401-ээр татгалзах нь зөв ч WS handshake дээр subprotocol-гүй
        # хаалт илүү нийцтэй: accept хийлгүй шууд хаана
        await ws.close(code=1008)
        return

    # ── Subprotocol negotiation: ocpp1.6 ──
    offered = [p.strip() for p in (ws.headers.get("sec-websocket-protocol") or "").split(",") if p.strip()]
    sub = "ocpp1.6" if "ocpp1.6" in offered else (offered[0] if offered else None)
    await ws.accept(subprotocol=sub)

    conn = await registry.register(cp_id, ws)
    db = SessionLocal()
    try:
        c = db.query(Charger).filter(Charger.cp_id == cp_id).first()
        if c:
            c.last_connect_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()
    log.info("cp=%s: холбогдлоо (subprotocol=%s)", cp_id, sub)

    try:
        while True:
            raw = await ws.receive_text()
            conn.last_seen = datetime.utcnow()
            try:
                mtype, mid, action, payload = protocol.parse(raw)
            except protocol.OcppProtocolError as e:
                await conn.send_text(protocol.call_error(
                    e.message_id or "0", e.code, e.description))
                continue
            if mtype == protocol.CALL:
                reply = await run_in_threadpool(
                    _handle_call_sync, cp_id, action, mid, payload)
                await conn.send_text(reply)
            elif mtype == protocol.CALLRESULT:
                conn.resolve(mid, True, payload)
            elif mtype == protocol.CALLERROR:
                conn.resolve(mid, False, payload)
    except WebSocketDisconnect:
        log.info("cp=%s: холболт тасарлаа", cp_id)
    except Exception:
        log.exception("cp=%s: WS давталтын алдаа", cp_id)
    finally:
        # §3.4: бүртгэл ЗААВАЛ цэвэрлэгдэнэ (leak-гүй)
        await registry.unregister(cp_id, conn)
        await run_in_threadpool(_mark_disconnect, cp_id)
        if ws.client_state == WebSocketState.CONNECTED:
            try:
                await ws.close()
            except Exception:
                pass


@app.websocket("/ocpp/1.6/{cp_id}")
async def ocpp_16(ws: WebSocket, cp_id: str):
    await _ocpp_session(ws, cp_id)


@app.websocket("/ocpp/{cp_id}")
async def ocpp_default(ws: WebSocket, cp_id: str):
    await _ocpp_session(ws, cp_id)
