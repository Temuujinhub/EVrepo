"""Цэнэглэгчээс ирэх OCPP 1.6 үйлдлүүдийн боловсруулагч.

Гол дүрэм (EV_CHARGING_PLAN.md §3.2): hub «энэ цэнэглэлт хэдэн төгрөг вэ»
гэдгийг МЭДЭХГҮЙ. Түүхий үйл явдлыг DB-д бичээд hub_events outbox-оор core
руу нийтэлнэ. Эрхийн шийдвэрийг (Authorize) core гаргана; core унтарсан үед
зөвхөн саяхан RemoteStart-аар олгогдсон idTag-уудыг л зөвшөөрнө.
"""
import asyncio
import logging
import time
from datetime import datetime

from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..models import (
    Charger, ChargerConnector, HubEvent, MeterSample, OcppMessage,
    OcppTransaction, TxCounter,
)
from . import protocol

log = logging.getLogger("evhub.handlers")

# RemoteStartTransaction-д өгөгдсөн idTag-ууд (cp_id, id_tag) → олгосон цаг.
# Authorize/StartTransaction ирэхэд core-оос дахин асуулгүй зөвшөөрнө —
# core түр унтарсан ч аль хэдийн зөвшөөрөгдсөн цэнэглэлт эхэлж чадна.
_recent_idtags: dict[tuple[str, str], float] = {}
_IDTAG_TTL = 600.0


def remember_idtag(cp_id: str, id_tag: str):
    now = time.monotonic()
    # хуучирсныг цэвэрлэ (жижиг dict — шугаман гүйлт хангалттай)
    for k, t in list(_recent_idtags.items()):
        if now - t > _IDTAG_TTL:
            del _recent_idtags[k]
    _recent_idtags[(cp_id, id_tag)] = now


def _idtag_known(cp_id: str, id_tag: str) -> bool:
    t = _recent_idtags.get((cp_id, id_tag))
    return t is not None and time.monotonic() - t <= _IDTAG_TTL


def utcnow_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def log_message(db: Session, cp_id: str, direction: str, action: str,
                message_id: str, payload: dict):
    db.add(OcppMessage(cp_id=cp_id, direction=direction, action=action or "",
                       message_id=message_id or "", payload=payload or {}))


def emit(db: Session, kind: str, payload: dict):
    """Core руу очих үйл явдлыг outbox-д нэмнэ (core_client ард нь хүргэнэ)."""
    db.add(HubEvent(kind=kind, payload=payload))


def _next_tx_id(db: Session) -> int:
    """Атомар өсөх гүйлгээний дугаар (мөрийн түгжээтэй)."""
    row = db.query(TxCounter).filter(TxCounter.id == 1).with_for_update().first()
    if not row:
        row = TxCounter(id=1, value=1000)
        db.add(row)
        db.flush()
        row = db.query(TxCounter).filter(TxCounter.id == 1).with_for_update().first()
    row.value += 1
    return row.value


def _connector(db: Session, charger: Charger, connector_id: int) -> ChargerConnector:
    conn = (db.query(ChargerConnector)
            .filter(ChargerConnector.charger_id == charger.id,
                    ChargerConnector.connector_id == connector_id).first())
    if not conn:
        conn = ChargerConnector(charger_id=charger.id, connector_id=connector_id)
        db.add(conn)
        db.flush()
    return conn


# ── sampledValue задлагч ────────────────────────────────────────────────────

def _to_wh(value: str, unit: str) -> int | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if (unit or "Wh").lower() == "kwh":
        v *= 1000.0
    return int(round(v))


def _to_w(value: str, unit: str) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if (unit or "W").lower() == "kw":
        v *= 1000.0
    return v


def parse_meter_values(payload: dict) -> list[dict]:
    """MeterValues payload → [{sampled_at, energy_wh, power_w, soc}] жагсаалт."""
    out = []
    for mv in payload.get("meterValue") or []:
        row = {"sampled_at": None, "energy_wh": None, "power_w": None, "soc": None}
        ts = mv.get("timestamp")
        if ts:
            try:
                row["sampled_at"] = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                pass
        for sv in mv.get("sampledValue") or []:
            meas = sv.get("measurand") or "Energy.Active.Import.Register"
            unit = sv.get("unit") or ""
            if meas == "Energy.Active.Import.Register":
                row["energy_wh"] = _to_wh(sv.get("value"), unit)
            elif meas == "Power.Active.Import":
                row["power_w"] = _to_w(sv.get("value"), unit)
            elif meas == "SoC":
                try:
                    row["soc"] = float(sv.get("value"))
                except (TypeError, ValueError):
                    pass
        out.append(row)
    return out


# ── Үйлдэл бүрийн боловсруулагч (db, charger, payload) → хариу payload ─────

def on_boot_notification(db: Session, charger: Charger, payload: dict) -> dict:
    charger.vendor = payload.get("chargePointVendor") or charger.vendor
    charger.model = payload.get("chargePointModel") or charger.model
    charger.serial = payload.get("chargePointSerialNumber") or charger.serial
    charger.fw_version = payload.get("firmwareVersion") or charger.fw_version
    charger.boot_payload = payload
    charger.last_boot_at = datetime.utcnow()
    charger.last_heartbeat_at = datetime.utcnow()
    emit(db, "ev.boot", {"cp_id": charger.cp_id, "vendor": charger.vendor,
                         "model": charger.model, "fw": charger.fw_version})
    return {"status": "Accepted", "currentTime": utcnow_iso(),
            "interval": settings.heartbeat_interval}


def on_heartbeat(db: Session, charger: Charger, payload: dict) -> dict:
    charger.last_heartbeat_at = datetime.utcnow()
    return {"currentTime": utcnow_iso()}


def on_status_notification(db: Session, charger: Charger, payload: dict) -> dict:
    connector_id = int(payload.get("connectorId") or 0)
    conn = _connector(db, charger, connector_id)
    new_status = payload.get("status") or conn.status
    # OCPP: Available = гүйлгээгүй. Цэнэглэгч гүйлгээний дундаас unclean
    # унтарч (StopTransaction илгээлгүй) Available болж ирвэл хуучин
    # active_tx_id-г цэвэрлэнэ — бууц «завгүй» гэж мөнхөд гацахгүй.
    # Гүйлгээний мөнгөн тооцоог orphan sweeper (sweeper.py) хаана.
    if new_status == "Available" and conn.active_tx_id:
        log.warning("cp=%s conn=%s: Available ирэхэд tx=%s идэвхтэй байсан — цэвэрлэв",
                    charger.cp_id, connector_id, conn.active_tx_id)
        conn.active_tx_id = None
    conn.status = new_status
    conn.error_code = payload.get("errorCode") or "NoError"
    conn.vendor_error = payload.get("vendorErrorCode") or ""
    conn.updated_at = datetime.utcnow()
    emit(db, "ev.status", {"cp_id": charger.cp_id, "connector_id": connector_id,
                           "status": conn.status, "error_code": conn.error_code})
    return {}


def on_authorize(db: Session, charger: Charger, payload: dict,
                 core_authorize) -> dict:
    """core_authorize: (cp_id, id_tag) → bool | None (None = core холбогдсонгүй)."""
    id_tag = str(payload.get("idTag") or "")
    if _idtag_known(charger.cp_id, id_tag):
        return {"idTagInfo": {"status": "Accepted"}}
    verdict = core_authorize(charger.cp_id, id_tag)
    status = "Accepted" if verdict else "Invalid"
    return {"idTagInfo": {"status": status}}


def on_start_transaction(db: Session, charger: Charger, payload: dict) -> dict:
    connector_id = int(payload.get("connectorId") or 1)
    id_tag = str(payload.get("idTag") or "")
    meter_start = payload.get("meterStart")
    tx_id = _next_tx_id(db)
    ts = payload.get("timestamp")
    started_at = None
    if ts:
        try:
            started_at = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass
    tx = OcppTransaction(
        ocpp_tx_id=tx_id, charger_id=charger.id, cp_id=charger.cp_id,
        connector_id=connector_id, id_tag=id_tag,
        meter_start_wh=int(meter_start) if meter_start is not None else None,
        started_at=started_at or datetime.utcnow(), status="RUNNING",
    )
    db.add(tx)
    conn = _connector(db, charger, connector_id)
    conn.active_tx_id = tx_id
    if tx.meter_start_wh is not None:
        conn.last_meter_wh = tx.meter_start_wh
    emit(db, "ev.tx.started", {
        "cp_id": charger.cp_id, "connector_id": connector_id, "id_tag": id_tag,
        "ocpp_tx_id": tx_id, "meter_start_wh": tx.meter_start_wh,
        "started_at": tx.started_at.isoformat(),
    })
    return {"transactionId": tx_id, "idTagInfo": {"status": "Accepted"}}


def on_meter_values(db: Session, charger: Charger, payload: dict) -> dict:
    connector_id = int(payload.get("connectorId") or 0)
    tx_id = payload.get("transactionId")
    conn = _connector(db, charger, connector_id)
    rows = parse_meter_values(payload)
    last = None
    for r in rows:
        if tx_id is not None:
            db.add(MeterSample(
                ocpp_tx_id=int(tx_id), charger_id=charger.id,
                connector_id=connector_id, energy_wh=r["energy_wh"],
                power_w=r["power_w"], soc=r["soc"],
                sampled_at=r["sampled_at"] or datetime.utcnow(),
            ))
        last = r
    if last:
        if last["energy_wh"] is not None:
            conn.last_meter_wh = last["energy_wh"]
        if last["power_w"] is not None:
            conn.power_w = last["power_w"]
        if last["soc"] is not None:
            conn.soc = last["soc"]
        conn.updated_at = datetime.utcnow()
    if tx_id is not None and last:
        tx = (db.query(OcppTransaction)
              .filter(OcppTransaction.ocpp_tx_id == int(tx_id)).first())
        if tx:
            if last["soc"] is not None and tx.soc_start is None:
                tx.soc_start = last["soc"]
            if last["power_w"] is not None and (
                    tx.max_power_w is None or float(last["power_w"]) > float(tx.max_power_w)):
                tx.max_power_w = last["power_w"]
            if last["energy_wh"] is not None and tx.meter_start_wh is not None:
                energy = max(0, last["energy_wh"] - tx.meter_start_wh)
                emit(db, "ev.meter", {
                    "cp_id": charger.cp_id, "connector_id": connector_id,
                    "ocpp_tx_id": int(tx_id), "energy_wh": energy,
                    "meter_wh": last["energy_wh"],
                    "power_w": float(last["power_w"]) if last["power_w"] is not None else None,
                    "soc": last["soc"],
                })
    return {}


def on_stop_transaction(db: Session, charger: Charger, payload: dict) -> dict:
    tx_id = payload.get("transactionId")
    tx = None
    if tx_id is not None:
        tx = (db.query(OcppTransaction)
              .filter(OcppTransaction.ocpp_tx_id == int(tx_id))
              .with_for_update().first())
    if not tx:
        # Мэдэхгүй гүйлгээ — хүлээн авснаа хэлээд (цэнэглэгч дахин илгээхгүй
        # байх үүднээс) core-д мэдэгдэнэ. Мөнгөний эрсдэлгүй: core ocpp_tx_id-гүй
        # session-ыг тохируулж чадахгүй тул гараар шалгана.
        log.warning("cp=%s: үл мэдэх StopTransaction tx=%s", charger.cp_id, tx_id)
        emit(db, "ev.tx.stopped", {"cp_id": charger.cp_id, "ocpp_tx_id": tx_id,
                                   "unknown": True, "payload": payload})
        return {"idTagInfo": {"status": "Accepted"}}
    if tx.status == "STOPPED":
        # Офлайнаас сэргэсэн ДАВХАР StopTransaction (§6.5) — idempotent
        log.info("cp=%s: давхар StopTransaction tx=%s — үл тооно", charger.cp_id, tx_id)
        return {"idTagInfo": {"status": "Accepted"}}
    ts = payload.get("timestamp")
    stopped_at = None
    if ts:
        try:
            stopped_at = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass
    meter_stop = payload.get("meterStop")
    tx.meter_stop_wh = int(meter_stop) if meter_stop is not None else None
    if tx.meter_stop_wh is not None and tx.meter_start_wh is not None:
        tx.energy_wh = max(0, tx.meter_stop_wh - tx.meter_start_wh)
    tx.stopped_at = stopped_at or datetime.utcnow()
    tx.stop_reason = payload.get("reason") or ""
    tx.status = "STOPPED"
    # transactionData дотор эцсийн SoC байж болно
    for r in parse_meter_values({"meterValue": payload.get("transactionData") or []}):
        if r["soc"] is not None:
            tx.soc_end = r["soc"]
    conn = _connector(db, charger, tx.connector_id)
    if conn.active_tx_id == tx.ocpp_tx_id:
        conn.active_tx_id = None
    emit(db, "ev.tx.stopped", {
        "cp_id": charger.cp_id, "connector_id": tx.connector_id,
        "ocpp_tx_id": tx.ocpp_tx_id, "id_tag": tx.id_tag,
        "meter_start_wh": tx.meter_start_wh, "meter_stop_wh": tx.meter_stop_wh,
        "energy_wh": tx.energy_wh, "stop_reason": tx.stop_reason,
        "started_at": tx.started_at.isoformat() if tx.started_at else None,
        "stopped_at": tx.stopped_at.isoformat(),
        "soc_end": float(tx.soc_end) if tx.soc_end is not None else None,
    })
    return {"idTagInfo": {"status": "Accepted"}}


def on_data_transfer(db: Session, charger: Charger, payload: dict) -> dict:
    # Vendor-ийн өргөтгөл — бүртгээд (ocpp_messages-д аль хэдийн орсон) зөвшөөрнө
    return {"status": "Accepted"}


def on_noop(db: Session, charger: Charger, payload: dict) -> dict:
    """DiagnosticsStatusNotification, FirmwareStatusNotification г.м —
    хүлээж авснаа л хэлэхэд хангалттай (лог ocpp_messages-д үлдэнэ)."""
    return {}


HANDLERS = {
    "BootNotification": on_boot_notification,
    "Heartbeat": on_heartbeat,
    "StatusNotification": on_status_notification,
    "StartTransaction": on_start_transaction,
    "MeterValues": on_meter_values,
    "StopTransaction": on_stop_transaction,
    "DataTransfer": on_data_transfer,
    "DiagnosticsStatusNotification": on_noop,
    "FirmwareStatusNotification": on_noop,
}
