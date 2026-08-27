"""OCPP 1.6J цэнэглэгчийн симулятор — hub-ыг бодит цэнэглэгчгүйгээр шалгана.

Хэрэглээ:
  python simulator/cp_sim.py --url ws://127.0.0.1:8100/ocpp/1.6/SIM01 \
      --cp-id SIM01 --password Provision123 --auto

--auto горим: boot → status(Available) → 5с тутам heartbeat, RemoteStart
ирвэл цэнэглэлт эхлүүлж 2 секунд тутам MeterValues (+1000 Wh алхам) илгээнэ,
RemoteStop/локал лимитэд StopTransaction.

Winline UX-ийн зан төлөвийг ойролцоо дуурайна: Preparing → Charging →
Finishing → Available.
"""
import argparse
import asyncio
import base64
import json
import random
import sys
import uuid
from datetime import datetime, timezone

import websockets


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ChargePointSim:
    def __init__(self, url: str, cp_id: str, password: str,
                 step_wh: int = 1000, interval: float = 2.0):
        self.url = url
        self.cp_id = cp_id
        self.password = password
        self.step_wh = step_wh
        self.interval = interval
        self.ws = None
        self.pending = {}
        self.meter_wh = random.randint(1_000_000, 2_000_000)  # тоолуурын эхлэл
        self.tx_id = None
        self.connector_status = "Available"
        self.soc = 30.0
        self.stop_requested = False

    async def connect(self):
        token = base64.b64encode(f"{self.cp_id}:{self.password}".encode()).decode()
        self.ws = await websockets.connect(
            self.url, subprotocols=["ocpp1.6"],
            additional_headers={"Authorization": f"Basic {token}"})
        print(f"[sim] холбогдлоо: {self.url}")

    async def call(self, action: str, payload: dict) -> dict:
        mid = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self.pending[mid] = fut
        await self.ws.send(json.dumps([2, mid, action, payload]))
        return await asyncio.wait_for(fut, timeout=30)

    async def reply(self, mid: str, payload: dict):
        await self.ws.send(json.dumps([3, mid, payload]))

    async def send_status(self, status: str, connector_id: int = 1):
        self.connector_status = status
        await self.call("StatusNotification", {
            "connectorId": connector_id, "errorCode": "NoError",
            "status": status, "timestamp": now_iso()})
        print(f"[sim] status={status}")

    async def boot(self):
        r = await self.call("BootNotification", {
            "chargePointVendor": "Winline", "chargePointModel": "UX-40kW-SIM",
            "chargePointSerialNumber": f"SIM-{self.cp_id}",
            "firmwareVersion": "sim-1.0"})
        print(f"[sim] boot → {r}")
        await self.send_status("Available")
        await self.call("Heartbeat", {})

    async def start_tx(self, id_tag: str, connector_id: int = 1):
        await self.send_status("Preparing", connector_id)
        r = await self.call("Authorize", {"idTag": id_tag})
        if r.get("idTagInfo", {}).get("status") != "Accepted":
            print(f"[sim] Authorize ГОЛОГДОВ: {r}")
            await self.send_status("Available", connector_id)
            return
        r = await self.call("StartTransaction", {
            "connectorId": connector_id, "idTag": id_tag,
            "meterStart": self.meter_wh, "timestamp": now_iso()})
        self.tx_id = r.get("transactionId")
        print(f"[sim] цэнэглэлт эхлэв tx={self.tx_id}")
        await self.send_status("Charging", connector_id)

    async def meter_loop(self):
        while self.tx_id is not None and not self.stop_requested:
            await asyncio.sleep(self.interval)
            if self.tx_id is None:
                break
            self.meter_wh += self.step_wh
            self.soc = min(100.0, self.soc + 0.5)
            power = 38000 + random.randint(-2000, 2000)
            await self.call("MeterValues", {
                "connectorId": 1, "transactionId": self.tx_id,
                "meterValue": [{
                    "timestamp": now_iso(),
                    "sampledValue": [
                        {"value": str(self.meter_wh),
                         "measurand": "Energy.Active.Import.Register", "unit": "Wh"},
                        {"value": str(power),
                         "measurand": "Power.Active.Import", "unit": "W"},
                        {"value": f"{self.soc:.1f}", "measurand": "SoC"},
                    ]}]})
            print(f"[sim] meter={self.meter_wh} Wh soc={self.soc:.0f}%")

    async def stop_tx(self, reason: str = "Remote", id_tag: str = ""):
        if self.tx_id is None:
            return
        tx, self.tx_id = self.tx_id, None
        await self.send_status("Finishing")
        payload = {"transactionId": tx, "meterStop": self.meter_wh,
                   "timestamp": now_iso(), "reason": reason}
        if id_tag:
            payload["idTag"] = id_tag
        await self.call("StopTransaction", payload)
        print(f"[sim] цэнэглэлт зогсов tx={tx} meter={self.meter_wh}")
        await self.send_status("Available")

    async def handle_incoming(self):
        async for raw in self.ws:
            msg = json.loads(raw)
            if msg[0] == 3:  # CallResult
                fut = self.pending.pop(msg[1], None)
                if fut and not fut.done():
                    fut.set_result(msg[2])
            elif msg[0] == 4:  # CallError
                fut = self.pending.pop(msg[1], None)
                if fut and not fut.done():
                    fut.set_exception(RuntimeError(str(msg[2:])))
            elif msg[0] == 2:  # hub-ээс ирсэн команд
                mid, action, payload = msg[1], msg[2], msg[3]
                print(f"[sim] ← {action} {payload}")
                if action == "RemoteStartTransaction":
                    await self.reply(mid, {"status": "Accepted"})
                    asyncio.create_task(self._remote_start(payload))
                elif action == "RemoteStopTransaction":
                    await self.reply(mid, {"status": "Accepted"})
                    asyncio.create_task(self.stop_tx("Remote"))
                elif action == "ChangeConfiguration":
                    await self.reply(mid, {"status": "Accepted"})
                elif action == "GetConfiguration":
                    await self.reply(mid, {"configurationKey": []})
                elif action == "SetChargingProfile":
                    await self.reply(mid, {"status": "Accepted"})
                elif action == "Reset":
                    await self.reply(mid, {"status": "Accepted"})
                elif action == "UnlockConnector":
                    await self.reply(mid, {"status": "Unlocked"})
                elif action == "TriggerMessage":
                    await self.reply(mid, {"status": "Accepted"})
                else:
                    await self.ws.send(json.dumps(
                        [4, mid, "NotImplemented", action, {}]))

    async def _remote_start(self, payload: dict):
        id_tag = payload.get("idTag") or "UNKNOWN"
        await self.start_tx(id_tag, int(payload.get("connectorId") or 1))
        asyncio.create_task(self.meter_loop())


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--cp-id", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--step-wh", type=int, default=1000)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--auto", action="store_true",
                    help="boot хийгээд командыг хүлээнэ")
    args = ap.parse_args()
    sim = ChargePointSim(args.url, args.cp_id, args.password,
                         args.step_wh, args.interval)
    await sim.connect()
    listener = asyncio.create_task(sim.handle_incoming())
    await sim.boot()
    if args.auto:
        try:
            while True:
                await asyncio.sleep(30)
                await sim.call("Heartbeat", {})
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
    listener.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
