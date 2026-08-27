# EV Hub ⚡ — OCPP 1.6J цэнэглэгчийн сервер

10 × 40 kW Winline UX DC цэнэглэгчийг удирдах **тусдаа WSS (device hub) сервер**.
Төлөвлөгөө: [`docs/evcharger/EV_CHARGING_PLAN.md` (PARKING repo)](https://github.com/Temuujinhub/PARKING/blob/main/docs/evcharger/EV_CHARGING_PLAN.md).

**Гол дүрэм:** hub мөнгөний логик АГУУЛАХГҮЙ. Данс, тариф, төлбөр —
easy-parking (core) дээр. Hub зөвхөн: OCPP терминаци, түүхий үйл явдал → DB,
командын гүйцэтгэл.

```
цэнэглэгч ──ws──► nginx :8080 ──► hub :8100 ──► Postgres (локал, дараа нь 172.16.100.33)
   ▲                                  │ hub_events outbox (at-least-once)
   │ RemoteStart/Stop, config         ▼
   └────────── charger_commands ◄── core (easy-parking) API
```

## Байрлал

| Юу | Хаана |
|---|---|
| WSS сервер | **172.16.100.32** (`/opt/evhub`) |
| Гадаад OCPP endpoint | `ws://202.21.117.180:8080/ocpp/1.6/{cp_id}` |
| DB | одоо локал Postgres; дараа **172.16.100.33** ([DB салгах](#db-салгах)) |
| Core | https://test.easy-parking.mn (данс, тооцоо, QPay, e-Barimt) |

## Бүтэц

```
hub/
  main.py            # FastAPI: WS /ocpp/1.6/{cp_id} + /internal API + /healthz
  config.py          # EVHUB_* орчны тохиргоо
  models.py          # chargers, connectors, ocpp_transactions, meter_samples,
                     # charger_commands, ocpp_messages, hub_events (outbox)
  auth.py            # Basic auth (cp_id ≡ username), internal Bearer
  ocpp/
    protocol.py      # [2,id,action,payload] frame
    registry.py      # cp_id → холболт (нэг эзэн, finally цэвэрлэгээ)
    handlers.py      # Boot/Heartbeat/Status/Authorize/Start/Stop/MeterValues
    config_profile.py# холбогдмогц тулгах тохиргоо (§4.3)
  queue.py           # командын дараалал (SKIP LOCKED, 1с поллинг)
  core_client.py     # outbox → core хүргэлт + Authorize асуулга
  retention.py       # ocpp_messages 7 хоног, meter_samples 90 хоног
simulator/cp_sim.py  # цэнэглэгчийн симулятор (бодит төмөргүйгээр шалгах)
deploy/              # install.sh, systemd, nginx, autodeploy
evctl                # CLI deploy/удирдлага (дотоод сүлжээнээс)
```

## Анхны суулгац (шинэ сервер)

Сервер дээр root-оор:

```bash
git clone https://github.com/Temuujinhub/EVrepo.git /opt/evhub
cd /opt/evhub && bash deploy/install.sh
```

эсвэл өөрийн машинаас (Git Bash / WSL):

```bash
./evctl install
```

`install.sh` нь Postgres, venv, systemd (`evhub.service`), nginx (8080→8100),
autodeploy timer-ийг бүгдийг тохируулаад **provision нууц үг** ба **internal
API түлхүүр**-ийг хэвлэнэ — тэмдэглэж аваад:

1. `EVHUB_CORE_URL`, `EVHUB_CORE_API_KEY`-г `/opt/evhub/.env`-д гараар бөглөнө.
2. Core (PARKING) талын `.env`-д: `PARKING_EVHUB_URL=http://202.21.117.180:8080`,
   `PARKING_EVHUB_API_KEY=<internal түлхүүр>`.

## Deploy (CLI)

Гаднаас SSH орох боломжгүй (202.21.117.180:8080 нь зөвхөн OCPP порт) тул
deploy нь **pull-суурьт**: сервер 2 минут тутам GitHub `main`-ыг татна.

```bash
git push origin main        # л хийхэд 2 минутын дотор сервер дээр гарна
./evctl deploy              # эсвэл шууд одоо татуул (SSH дотоод сүлжээнээс)
./evctl status              # systemd + холбогдсон цэнэглэгчид
./evctl logs                # амьд лог
./evctl chargers            # цэнэглэгчдийн төлөв (internal API)
```

Autodeploy нь `hub/` эсвэл `requirements.txt` өөрчлөгдсөн үед Л restart
хийнэ — бусад commit цэнэглэгчийн холболтыг таслахгүй (§4.2).

## Цэнэглэгч холбох

1. HMI (админ нууц үгээр) → Network → **Server url**:
   `ws://202.21.117.180:8080/ocpp/1.6/CP01` (цэнэглэгч бүрд өөр cp_id)
2. **Auth1 user** = `CP01` (cp_id-тэй ЗААВАЛ ижил), **password** = provision
   нууц үг (install.sh хэвлэсэн).
3. Цэнэглэгч Boot хиймэгц hub автоматаар:
   бүртгэнэ (status=NEW) + §4.3-ийн тохиргоог тулгана
   (MeterValues 10с, Heartbeat 300с, LocalPreAuthorize=false…).
4. Админ идэвхжүүлэлт + өөрийн нууц үг олгох:
   ```bash
   curl -X PUT http://172.16.100.32:8080/internal/chargers/CP01 \
     -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
     -d '{"status":"ACTIVE","auth_password":"шинэ-нууц-үг","name":"1-р цэнэглэгч"}'
   ```
   (дараа нь HMI дээр password-оо мөн шинэчилнэ)

## Симулятороор шалгах

```bash
./evctl sim SIM01                     # сервер дээр
# эсвэл локал:
python simulator/cp_sim.py --url ws://127.0.0.1:8100/ocpp/1.6/SIM01 \
    --cp-id SIM01 --password <provision> --auto
```

`--auto` үед RemoteStartTransaction команд илгээхэд симулятор цэнэглэлт
эхлүүлж, 2с тутам MeterValues явуулна — core-ийн бүх урсгал бодит
цэнэглэгчгүйгээр шалгагдана.

## Internal API (core + админ)

Бүгд `Authorization: Bearer <EVHUB_INTERNAL_API_KEY>`:

```
GET  /internal/health                      холболт, дарааллын төлөв
GET  /internal/chargers                    жагсаалт + амьд төлөв
GET  /internal/chargers/{cp_id}
PUT  /internal/chargers/{cp_id}            {status, name, auth_password, core_ref}
POST /internal/chargers/{cp_id}/commands   {action, payload, expires_in}
GET  /internal/commands/{id}               командын үр дүн
GET  /internal/transactions?since_tx_id=   тулгалт/сэргээлт
GET  /internal/transactions/{tx}/samples   тоолуурын дээжүүд
POST /internal/events/replay               {from_id} — event дахин хүргүүлэх
```

Hub → core чиглэлд (core дээр байх endpoint-ууд):

```
POST /api/integration/evhub/events         үйл явдал (Idempotency-Key-тэй)
POST /api/integration/evhub/authorize      {cp_id, id_tag} → {accepted}
```

## DB салгах

DB-г 172.16.100.33 руу нүүлгэхдээ:

```bash
# 1. .33 дээр: Postgres суулгаад evhub DB/хэрэглэгч үүсгэнэ,
#    postgresql.conf: listen_addresses='*'; pg_hba.conf: 172.16.100.32/32 md5
# 2. .32 дээр:
systemctl stop evhub
sudo -u postgres pg_dump evhub | psql -h 172.16.100.33 -U evhub evhub
# 3. /opt/evhub/.env:
#    EVHUB_DATABASE_URL=postgresql://evhub:НУУЦ@172.16.100.33:5432/evhub
systemctl start evhub
```

Код өөрчлөгдөхгүй — зөвхөн `EVHUB_DATABASE_URL`.

## TLS (wss://)

Одоо `ws://` (IP дээр Let's Encrypt боломжгүй). Домэйн
(ж: `ocpp.easy-parking.mn` → 202.21.117.180) гармагц
`deploy/nginx-evhub.conf`-ын ssl блокыг нээж `wss://` руу шилжинэ.
Түр хамгаалалт: цэнэглэгч бүрийн Basic нууц үг + cp_id≡username + үл таних
idTag эхлүүлдэггүй урьдчилсан төлбөрийн загвар.
