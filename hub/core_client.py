"""Core (easy-parking) руу үйл явдал хүргэх + эрхийн асуулга.

Хүргэлт: hub_events outbox-оос дарааллаар (at-least-once). Core унтарсан үед
PENDING хэвээр хуримтлагдаж, сэргэмэгц он цагийн дарааллаар хүргэгдэнэ —
цэнэглэлтийн тооцоо АЛДАГДАХГҮЙ, зөвхөн хойшилно.

Idempotency: event id-г Idempotency-Key болгож явуулна — core давхар
боловсруулахаас өөрөө хамгаална.
"""
import asyncio
import logging
from datetime import datetime

import httpx

from .config import settings
from .database import SessionLocal
from .models import HubEvent

log = logging.getLogger("evhub.core")

_BATCH = 50
_RETRY_SLEEP = 3.0
_MAX_ATTEMPT_SLEEP = 30.0


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.core_api_key}"}


def core_authorize_sync(cp_id: str, id_tag: str) -> bool:
    """Authorize-ийн синхрон асуулга (WS handler дотроос дуудагдана).

    core_url тохируулаагүй эсвэл core хариу өгөхгүй бол False — урьдчилсан
    төлбөрт систем тул таньж чадаагүй idTag-ийг ЭХЛҮҮЛЭХГҮЙ (§4.3
    AllowOfflineTxForUnknownId=false-тэй нийцнэ)."""
    if not settings.core_url:
        return False
    try:
        r = httpx.post(
            f"{settings.core_url}/api/integration/evhub/authorize",
            json={"cp_id": cp_id, "id_tag": id_tag},
            headers=_headers(), timeout=settings.core_timeout,
        )
        if r.status_code == 200:
            return bool(r.json().get("accepted"))
    except httpx.HTTPError as e:
        log.warning("core authorize холбогдсонгүй: %s", e)
    return False


async def deliver_events_forever():
    """Outbox worker — PENDING үйл явдлуудыг core руу дарааллаар хүргэнэ."""
    if not settings.core_url:
        log.warning("EVHUB_CORE_URL тохируулаагүй — үйл явдал зөвхөн DB-д хуримтлагдана")
    async with httpx.AsyncClient(timeout=settings.core_timeout) as client:
        while True:
            try:
                sent = await _deliver_batch(client)
                await asyncio.sleep(0.2 if sent else 1.0)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("event хүргэлтийн worker алдаа")
                await asyncio.sleep(_RETRY_SLEEP)


async def _deliver_batch(client: httpx.AsyncClient) -> int:
    if not settings.core_url:
        await asyncio.sleep(5)
        return 0
    db = SessionLocal()
    try:
        rows = (db.query(HubEvent)
                .filter(HubEvent.status == "PENDING")
                .order_by(HubEvent.id)
                .limit(_BATCH).all())
        if not rows:
            return 0
        sent = 0
        for ev in rows:
            try:
                r = await client.post(
                    f"{settings.core_url}/api/integration/evhub/events",
                    json={"id": ev.id, "kind": ev.kind, "payload": ev.payload,
                          "created_at": ev.created_at.isoformat()},
                    headers={**_headers(), "Idempotency-Key": f"evhub-{ev.id}"},
                )
            except httpx.HTTPError as e:
                # Core холбогдохгүй — дараа дахин оролдоно (дараалал зогсоно,
                # учир нь ДАРААЛАЛ чухал: started → meter → stopped)
                log.warning("core events хүрсэнгүй (%s) — түр хүлээнэ", e)
                await asyncio.sleep(min(_RETRY_SLEEP * (ev.attempts + 1),
                                        _MAX_ATTEMPT_SLEEP))
                ev.attempts += 1
                db.commit()
                return sent
            if r.status_code in (200, 201, 202, 409):
                # 409 = core аль хэдийн боловсруулсан (idempotent давхардал)
                ev.status = "DONE"
                ev.delivered_at = datetime.utcnow()
                sent += 1
                db.commit()
            elif r.status_code in (400, 422):
                # Буруу бүтэцтэй event — дарааллыг түгжихгүйн тулд FAILED
                log.error("core event %s-ийг гологдуулав (%s): %s",
                          ev.id, r.status_code, r.text[:300])
                ev.status = "FAILED"
                ev.attempts += 1
                db.commit()
            else:
                log.warning("core events %s → %s — дахин оролдоно",
                            ev.id, r.status_code)
                ev.attempts += 1
                db.commit()
                await asyncio.sleep(_RETRY_SLEEP)
                return sent
        return sent
    finally:
        db.close()
