"""cp_id → идэвхтэй WS холболтын бүртгэл.

Дүрэм (EV_CHARGING_PLAN.md §3.4):
  • Нэг cp_id-д нэг л идэвхтэй холболт — шинэ нь хуучныг хаана.
  • Бүртгэл ЗААВАЛ finally-д цэвэрлэгдэнэ — PARKING-ийн `_open_inflight`
    леакийн алдааг давтахгүй.
  • Бидний илгээсэн Call-ийн хариуг message_id-аар Future-т буулгана.
"""
import asyncio
import logging
from datetime import datetime

from . import protocol

log = logging.getLogger("evhub.registry")


class Connection:
    def __init__(self, cp_id: str, ws):
        self.cp_id = cp_id
        self.ws = ws
        self.connected_at = datetime.utcnow()
        self.last_seen = datetime.utcnow()
        # message_id → Future (бидний илгээсэн Call-ийн хариу хүлээгчид)
        self.pending: dict[str, asyncio.Future] = {}
        self.send_lock = asyncio.Lock()
        self.closed = False

    async def send_text(self, text: str):
        async with self.send_lock:
            await self.ws.send_text(text)

    async def send_call(self, action: str, payload: dict, timeout: float) -> dict:
        """Call илгээж CallResult-ийн payload-ыг хүлээж буцаана.
        CallError ирвэл RuntimeError, timeout болвол asyncio.TimeoutError."""
        mid, text = protocol.call(action, payload)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[mid] = fut
        try:
            await self.send_text(text)
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self.pending.pop(mid, None)

    def resolve(self, message_id: str, ok: bool, payload: dict):
        fut = self.pending.get(message_id)
        if fut and not fut.done():
            if ok:
                fut.set_result(payload)
            else:
                fut.set_exception(RuntimeError(
                    f"CallError: {payload.get('description') or payload}"))

    def fail_all_pending(self):
        for fut in self.pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("WS холболт тасарсан"))
        self.pending.clear()


class Registry:
    def __init__(self):
        self._by_cp: dict[str, Connection] = {}
        self._lock = asyncio.Lock()

    async def register(self, cp_id: str, ws) -> Connection:
        """Шинэ холболт бүртгэнэ; өмнөх идэвхтэй холболтыг ХААНА (нэг эзэн)."""
        async with self._lock:
            old = self._by_cp.get(cp_id)
            conn = Connection(cp_id, ws)
            self._by_cp[cp_id] = conn
        if old and not old.closed:
            old.closed = True
            old.fail_all_pending()
            try:
                await old.ws.close(code=1000)
                log.info("cp=%s: хуучин холболтыг шинээр орлууллаа", cp_id)
            except Exception:
                pass
        return conn

    async def unregister(self, cp_id: str, conn: Connection):
        """finally-аас ЗААВАЛ дуудагдана. Зөвхөн ӨӨРИЙНХ нь бүртгэлийг арилгана
        (шинэ холболт аль хэдийн орлуулсан байж болно)."""
        conn.closed = True
        conn.fail_all_pending()
        async with self._lock:
            if self._by_cp.get(cp_id) is conn:
                del self._by_cp[cp_id]

    def get(self, cp_id: str) -> Connection | None:
        conn = self._by_cp.get(cp_id)
        return conn if conn and not conn.closed else None

    def online_ids(self) -> list[str]:
        return [k for k, c in self._by_cp.items() if not c.closed]

    def stats(self) -> dict:
        return {
            "connected": len(self.online_ids()),
            "chargers": {
                k: {"connected_at": c.connected_at.isoformat(),
                    "last_seen": c.last_seen.isoformat()}
                for k, c in self._by_cp.items() if not c.closed
            },
        }


registry = Registry()
