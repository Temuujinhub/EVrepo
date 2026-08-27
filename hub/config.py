"""EV Hub тохиргоо — бүх утга орчны хувьсагч/.env-ээс (EVHUB_ угтвартай).

Зарчим (EV_CHARGING_PLAN.md §3): hub нь МӨНГӨНИЙ ЛОГИК АГУУЛАХГҮЙ.
Тэр зөвхөн OCPP терминаци + түүхий үйл явдлыг DB-д бичих + core-ийн
командыг цэнэглэгч рүү дамжуулах үүрэгтэй. Тооцоог core (easy-parking)
хийнэ.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVHUB_", env_file=".env",
                                      extra="ignore")

    # ── DB ────────────────────────────────────────────────────────────────
    # Одоо: WSS сервер (172.16.100.32) дээрх локал Postgres.
    # Дараа: DB сервер (172.16.100.33) руу салгахдаа ЗӨВХӨН энэ URL-ыг солино.
    database_url: str = "postgresql://evhub:evhub@127.0.0.1:5432/evhub"

    # ── Сүлжээ ────────────────────────────────────────────────────────────
    host: str = "127.0.0.1"          # nginx-ийн ард — гадагш шууд нээхгүй
    port: int = 8100
    # Гадаад endpoint (лог, QR, баримтад харуулахад): цэнэглэгчид
    # ws(s)://202.21.117.180:8080/ocpp/1.6/{cp_id} хаягаар холбогдоно.
    public_ws_url: str = "ws://202.21.117.180:8080/ocpp/1.6"

    # ── Цэнэглэгчийн Basic auth ───────────────────────────────────────────
    # Цэнэглэгч бүр DB-д (chargers.auth_user/auth_pass_enc) өөрийн нууц
    # үгтэй. Энэ нь шинэ цэнэглэгч ПРОВИЖН хийх үеийн түр нууц үг:
    # DB-д бүртгэлгүй cp_id ирвэл энэ хосоор л орж ирж болно (auto-register).
    provision_user: str = ""
    provision_password: str = ""
    # true үед DB-д байхгүй cp_id BootNotification илгээвэл автоматаар
    # chargers мөр үүснэ (status=NEW). Тохиргоо дуустал идэвхгүй байдлаар.
    auto_register: bool = True

    # ── Core (easy-parking) холболт ──────────────────────────────────────
    core_url: str = ""               # ж: https://test.easy-parking.mn
    core_api_key: str = ""           # core-ийн /api/integration/evhub-д Bearer
    core_timeout: float = 5.0
    # core унтарсан үед ч цэнэглэлт үргэлжилнэ (hub DB-д бүх юм бичигдэнэ);
    # core сэргэхээрээ /internal/events/replay-аас нөхөж авна.

    # ── Internal API (core → hub команд) ─────────────────────────────────
    internal_api_key: str = ""       # хоосон бол internal API идэвхгүй (403)

    # ── Нууц утгын шифрлэлт (PARKING-ийн secretbox-той ижил зарчим) ──────
    secret_enc_key: str = ""         # Fernet key; хоосон бол шифрлэхгүй

    # ── OCPP параметрүүд ─────────────────────────────────────────────────
    heartbeat_interval: int = 300
    meter_sample_interval: int = 10
    ocpp_call_timeout: float = 30.0  # бидний илгээсэн Call-ийн хариу хүлээх
    command_poll_seconds: float = 1.0
    boot_backoff_max: int = 120

    # ── Retention ────────────────────────────────────────────────────────
    ocpp_messages_keep_days: int = 7
    meter_values_keep_days: int = 90

    log_level: str = "INFO"


settings = Settings()
