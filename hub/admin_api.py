"""Internal REST API — core (easy-parking) болон админ хэрэгслүүдэд.

Бүх endpoint Bearer түлхүүрээр (EVHUB_INTERNAL_API_KEY) хамгаалагдана.
nginx энэ замуудыг гадагш гаргадаг тул түлхүүргүй hub БҮХ хүсэлтийг 403-оор
буцаана (fail-closed).
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .auth import check_internal_key
from .database import get_db
from .models import (Charger, ChargerCommand, ChargerConnector, HubEvent,
                     MeterSample, OcppTransaction)
from .ocpp.registry import registry
from .secretbox import encrypt_secret

router = APIRouter(prefix="/internal")


def require_key(authorization: str | None = Header(default=None)):
    if not check_internal_key(authorization):
        raise HTTPException(403, "internal API түлхүүр буруу эсвэл тохируулаагүй")


def _charger_dict(c: Charger, connectors: list[ChargerConnector]) -> dict:
    online = registry.get(c.cp_id) is not None
    return {
        "id": c.id, "cp_id": c.cp_id, "name": c.name, "serial": c.serial,
        "vendor": c.vendor, "model": c.model, "fw_version": c.fw_version,
        "status": c.status, "core_ref": c.core_ref, "online": online,
        "connector_count": c.connector_count,
        "last_boot_at": c.last_boot_at.isoformat() if c.last_boot_at else None,
        "last_heartbeat_at": c.last_heartbeat_at.isoformat() if c.last_heartbeat_at else None,
        "connectors": [{
            "connector_id": x.connector_id, "status": x.status,
            "error_code": x.error_code, "last_meter_wh": x.last_meter_wh,
            "power_w": float(x.power_w) if x.power_w is not None else None,
            "soc": float(x.soc) if x.soc is not None else None,
            "active_tx_id": x.active_tx_id,
            "updated_at": x.updated_at.isoformat() if x.updated_at else None,
        } for x in connectors],
    }


@router.get("/health")
def health(db: Session = Depends(get_db), _=Depends(require_key)):
    total = db.query(Charger).count()
    pending_events = db.query(HubEvent).filter(HubEvent.status == "PENDING").count()
    pending_cmds = db.query(ChargerCommand).filter(ChargerCommand.status == "PENDING").count()
    return {"ok": True, "chargers_total": total, **registry.stats(),
            "events_pending": pending_events, "commands_pending": pending_cmds}


@router.get("/chargers")
def list_chargers(db: Session = Depends(get_db), _=Depends(require_key)):
    chargers = db.query(Charger).order_by(Charger.cp_id).all()
    conns = db.query(ChargerConnector).all()
    by_charger: dict[str, list] = {}
    for x in conns:
        by_charger.setdefault(x.charger_id, []).append(x)
    return [_charger_dict(c, sorted(by_charger.get(c.id, []),
                                    key=lambda x: x.connector_id))
            for c in chargers]


@router.get("/chargers/{cp_id}")
def get_charger(cp_id: str, db: Session = Depends(get_db), _=Depends(require_key)):
    c = db.query(Charger).filter(Charger.cp_id == cp_id).first()
    if not c:
        raise HTTPException(404, "цэнэглэгч олдсонгүй")
    conns = (db.query(ChargerConnector)
             .filter(ChargerConnector.charger_id == c.id)
             .order_by(ChargerConnector.connector_id).all())
    return _charger_dict(c, conns)


@router.put("/chargers/{cp_id}")
def update_charger(cp_id: str, body: dict, db: Session = Depends(get_db),
                   _=Depends(require_key)):
    c = db.query(Charger).filter(Charger.cp_id == cp_id).first()
    if not c:
        raise HTTPException(404, "цэнэглэгч олдсонгүй")
    if "name" in body:
        c.name = str(body["name"])[:120]
    if "status" in body:
        if body["status"] not in ("NEW", "ACTIVE", "DISABLED"):
            raise HTTPException(422, "status: NEW|ACTIVE|DISABLED")
        c.status = body["status"]
    if "core_ref" in body:
        c.core_ref = (str(body["core_ref"])[:60] or None)
    if "connector_count" in body:
        c.connector_count = int(body["connector_count"])
    if body.get("auth_password"):
        # Нууц үг СОЛИХ — дараагийн холболтоос шинэ нууц үг үйлчилнэ
        c.auth_pass_enc = encrypt_secret(str(body["auth_password"])) or ""
        c.auth_user = c.cp_id
    db.commit()
    return {"ok": True}


@router.post("/chargers/{cp_id}/commands", status_code=201)
def enqueue_command(cp_id: str, body: dict, db: Session = Depends(get_db),
                    _=Depends(require_key)):
    c = db.query(Charger).filter(Charger.cp_id == cp_id).first()
    if not c:
        raise HTTPException(404, "цэнэглэгч олдсонгүй")
    action = str(body.get("action") or "")
    if not action:
        raise HTTPException(422, "action шаардлагатай")
    expires_in = int(body.get("expires_in") or 300)
    cmd = ChargerCommand(
        charger_id=c.id, action=action, payload=body.get("payload") or {},
        requested_by=str(body.get("requested_by") or "core")[:60],
        max_attempts=int(body.get("max_attempts") or 3),
        expires_at=datetime.utcnow() + timedelta(seconds=expires_in),
    )
    db.add(cmd)
    db.commit()
    return {"command_id": cmd.id, "status": cmd.status}


@router.get("/commands/{command_id}")
def get_command(command_id: str, db: Session = Depends(get_db), _=Depends(require_key)):
    cmd = db.get(ChargerCommand, command_id)
    if not cmd:
        raise HTTPException(404, "команд олдсонгүй")
    return {"command_id": cmd.id, "action": cmd.action, "status": cmd.status,
            "attempts": cmd.attempts, "result": cmd.result,
            "created_at": cmd.created_at.isoformat(),
            "done_at": cmd.done_at.isoformat() if cmd.done_at else None}


@router.get("/transactions")
def list_transactions(since_tx_id: int = 0, limit: int = 200,
                      db: Session = Depends(get_db), _=Depends(require_key)):
    """Core сэргээлт/тулгалтад: ocpp_tx_id-аас хойшхи гүйлгээнүүд."""
    rows = (db.query(OcppTransaction)
            .filter(OcppTransaction.ocpp_tx_id > since_tx_id)
            .order_by(OcppTransaction.ocpp_tx_id)
            .limit(min(limit, 1000)).all())
    return [{
        "ocpp_tx_id": t.ocpp_tx_id, "cp_id": t.cp_id,
        "connector_id": t.connector_id, "id_tag": t.id_tag,
        "meter_start_wh": t.meter_start_wh, "meter_stop_wh": t.meter_stop_wh,
        "energy_wh": t.energy_wh, "status": t.status,
        "stop_reason": t.stop_reason,
        "started_at": t.started_at.isoformat() if t.started_at else None,
        "stopped_at": t.stopped_at.isoformat() if t.stopped_at else None,
    } for t in rows]


@router.get("/transactions/{ocpp_tx_id}/samples")
def tx_samples(ocpp_tx_id: int, limit: int = 500,
               db: Session = Depends(get_db), _=Depends(require_key)):
    rows = (db.query(MeterSample)
            .filter(MeterSample.ocpp_tx_id == ocpp_tx_id)
            .order_by(MeterSample.sampled_at)
            .limit(min(limit, 5000)).all())
    return [{
        "energy_wh": r.energy_wh,
        "power_w": float(r.power_w) if r.power_w is not None else None,
        "soc": float(r.soc) if r.soc is not None else None,
        "sampled_at": r.sampled_at.isoformat(),
    } for r in rows]


@router.post("/events/replay")
def replay_events(body: dict, db: Session = Depends(get_db), _=Depends(require_key)):
    """Core өгөгдөл алдсан үед: from_id-ээс хойшхи DONE үйл явдлуудыг дахин
    PENDING болгож дахин хүргүүлнэ (idempotency талдаа core хариуцна)."""
    from_id = int(body.get("from_id") or 0)
    n = (db.query(HubEvent)
         .filter(HubEvent.id >= from_id, HubEvent.status == "DONE")
         .update({"status": "PENDING", "delivered_at": None},
                 synchronize_session=False))
    db.commit()
    return {"requeued": n}
