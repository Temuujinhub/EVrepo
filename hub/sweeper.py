"""Orphan гүйлгээний цэвэрлэгч — цэнэглэлтийн дунд цэнэглэгч/сүлжээ unclean
тасарч StopTransaction хэзээ ч ирэхгүй болсон RUNNING гүйлгээг хаана.

Яагаад чухал: ийм гүйлгээ (1) бууцыг «завгүй» гэж гацаана, (2) core талд
жолоочийн hold мөнхөд суусаар мөнгө нь түгжигдэнэ.

Дүрэм: RUNNING гүйлгээ ORPHAN_MINUTES минутын турш ямар ч MeterValues
аваагүй бол (хэвийн үед 10 секунд тутам ирдэг):
  • сүүлийн мэдэгдсэн тоолуураар STOPPED (stop_reason=Orphaned)
  • ev.tx.stopped үйл явдал → core бодит хэмжигдсэн энергиэр тооцоод
    үлдэгдлийг нь буцаана — жолооч хэмжигдээгүй энергид төлөхгүй.
Цэнэглэгч дараа нь ЖИНХЭНЭ StopTransaction-оо (офлайн дараалалаас) илгээвэл
tx аль хэдийн STOPPED тул idempotent алгасагдана (§6.5); зөрүү гарвал
ocpp_messages логоос гараар тулгана.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from .database import SessionLocal
from .models import ChargerConnector, HubEvent, MeterSample, OcppTransaction

log = logging.getLogger("evhub.sweeper")

ORPHAN_MINUTES = 10
SWEEP_SECONDS = 60


def sweep_once(db) -> int:
    cutoff = datetime.utcnow() - timedelta(minutes=ORPHAN_MINUTES)
    stale = (db.query(OcppTransaction)
             .filter(OcppTransaction.status == "RUNNING",
                     OcppTransaction.started_at < cutoff).all())
    n = 0
    for tx in stale:
        last = (db.query(MeterSample)
                .filter(MeterSample.ocpp_tx_id == tx.ocpp_tx_id)
                .order_by(MeterSample.sampled_at.desc()).first())
        if last and last.sampled_at > cutoff:
            continue  # амьд байна
        meter_stop = last.energy_wh if last and last.energy_wh is not None else tx.meter_start_wh
        tx.meter_stop_wh = meter_stop
        if meter_stop is not None and tx.meter_start_wh is not None:
            tx.energy_wh = max(0, int(meter_stop) - int(tx.meter_start_wh))
        else:
            tx.energy_wh = 0
        tx.stopped_at = last.sampled_at if last else datetime.utcnow()
        tx.stop_reason = "Orphaned"
        tx.status = "STOPPED"
        conn = (db.query(ChargerConnector)
                .filter(ChargerConnector.charger_id == tx.charger_id,
                        ChargerConnector.connector_id == tx.connector_id).first())
        if conn and conn.active_tx_id == tx.ocpp_tx_id:
            conn.active_tx_id = None
        db.add(HubEvent(kind="ev.tx.stopped", payload={
            "cp_id": tx.cp_id, "connector_id": tx.connector_id,
            "ocpp_tx_id": tx.ocpp_tx_id, "id_tag": tx.id_tag,
            "meter_start_wh": tx.meter_start_wh, "meter_stop_wh": tx.meter_stop_wh,
            "energy_wh": tx.energy_wh, "stop_reason": "Orphaned",
            "started_at": tx.started_at.isoformat() if tx.started_at else None,
            "stopped_at": tx.stopped_at.isoformat(),
            "soc_end": None,
        }))
        n += 1
        log.warning("orphan tx=%s (cp=%s): %s Wh дээр хаав — жолоочийн hold чөлөөлөгдөнө",
                    tx.ocpp_tx_id, tx.cp_id, tx.energy_wh)
    if n:
        db.commit()
    return n


async def sweep_forever():
    while True:
        try:
            db = SessionLocal()
            try:
                sweep_once(db)
            finally:
                db.close()
        except Exception:
            log.exception("orphan sweeper алдаа")
        await asyncio.sleep(SWEEP_SECONDS)
