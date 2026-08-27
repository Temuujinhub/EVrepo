"""Hub-ийн өгөгдлийн загвар (EV_CHARGING_PLAN.md §5-ийн hub тал).

Hub нь ТӨХӨӨРӨМЖИЙН үнэнийг хадгална: холболт, төлөв, тоолуур, түүхий
OCPP гүйлгээ. МӨНГӨНИЙ үнэн (данс, тариф, тооцоо) core (easy-parking)
дээр — энд тарифын ганц ч талбар байхгүй.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, BigInteger,
    Numeric, String, Text, JSON, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import UUID

from .database import Base


def uid():
    return str(uuid.uuid4())


class Charger(Base):
    """Нэг физик цэнэглэгч (Winline UX 40kW DC г.м). cp_id нь OCPP URL-ын
    сүүлийн хэсэг бөгөөд Basic username-тэй ЗААВАЛ ижил (§3.4)."""
    __tablename__ = "chargers"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    cp_id = Column(String(60), unique=True, nullable=False, index=True)
    name = Column(String(120), nullable=False, default="")
    serial = Column(String(80), default="")
    vendor = Column(String(60), default="")
    model = Column(String(80), default="")
    fw_version = Column(String(60), default="")
    ocpp_proto = Column(String(20), default="ocpp1.6")
    connector_count = Column(Integer, nullable=False, default=2)
    # OCPP Basic auth (цэнэглэгч → hub). auth_pass_enc нь secretbox-оор шифрлэгдэнэ.
    auth_user = Column(String(60), nullable=False, default="")
    auth_pass_enc = Column(String(200), nullable=False, default="")
    # NEW (auto-register, идэвхжүүлээгүй) → ACTIVE → DISABLED
    status = Column(String(20), nullable=False, default="NEW", index=True)
    # core талын site/price холбоос — hub НӨХЦӨЛГҮЙ дамжуулна, утгыг нь ашиглахгүй
    core_ref = Column(String(60), nullable=True)
    last_boot_at = Column(DateTime, nullable=True)
    last_heartbeat_at = Column(DateTime, nullable=True)
    last_connect_at = Column(DateTime, nullable=True)
    last_disconnect_at = Column(DateTime, nullable=True)
    boot_payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class ChargerConnector(Base):
    """Цэнэглэгчийн бууц (connector) бүрийн сүүлийн мэдэгдсэн төлөв."""
    __tablename__ = "charger_connectors"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    charger_id = Column(UUID(as_uuid=False), ForeignKey("chargers.id"),
                        nullable=False, index=True)
    connector_id = Column(Integer, nullable=False)  # 0 = бүхэлдээ, 1..N = бууц
    # OCPP статусууд: Available, Preparing, Charging, SuspendedEV, SuspendedEVSE,
    # Finishing, Reserved, Unavailable, Faulted
    status = Column(String(30), nullable=False, default="Unknown")
    error_code = Column(String(60), default="NoError")
    vendor_error = Column(String(120), default="")
    last_meter_wh = Column(BigInteger, nullable=True)
    power_w = Column(Numeric(12, 2), nullable=True)
    soc = Column(Numeric(5, 2), nullable=True)
    active_tx_id = Column(Integer, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("charger_id", "connector_id",
                                       name="uq_connector"),)


class OcppTransaction(Base):
    """Түүхий OCPP гүйлгээ — hub-ийн үнэн. Core-ийн charge_sessions (мөнгө)
    үүн дээр тулгуурлана. ocpp_tx_id нь hub-ээс олгогдоно (autoincrement биш
    UNIQUE тоо — офлайнаас давхар ирсэн StopTransaction-ийг DB түвшинд шүүнэ)."""
    __tablename__ = "ocpp_transactions"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    ocpp_tx_id = Column(Integer, unique=True, nullable=False, index=True)
    charger_id = Column(UUID(as_uuid=False), ForeignKey("chargers.id"),
                        nullable=False, index=True)
    cp_id = Column(String(60), nullable=False)
    connector_id = Column(Integer, nullable=False)
    id_tag = Column(String(40), nullable=False, index=True)
    meter_start_wh = Column(BigInteger, nullable=True)
    meter_stop_wh = Column(BigInteger, nullable=True)
    energy_wh = Column(BigInteger, nullable=True)      # stop − start
    max_power_w = Column(Numeric(12, 2), nullable=True)
    soc_start = Column(Numeric(5, 2), nullable=True)
    soc_end = Column(Numeric(5, 2), nullable=True)
    started_at = Column(DateTime, nullable=True)
    stopped_at = Column(DateTime, nullable=True)
    stop_reason = Column(String(60), nullable=True)
    # RUNNING → STOPPED; офлайн ирсэн (сэргээгдсэн) бол offline=true
    status = Column(String(20), nullable=False, default="RUNNING", index=True)
    offline = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (Index("ix_tx_charger_started", "charger_id", "started_at"),)


class MeterSample(Base):
    """MeterValues бүрийн дээж — core-ийн watchdog (98%) болон UI-ийн амьд
    явцын эх сурвалж. 90 хоногийн retention (config)."""
    __tablename__ = "meter_samples"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ocpp_tx_id = Column(Integer, nullable=False, index=True)
    charger_id = Column(UUID(as_uuid=False), nullable=False)
    connector_id = Column(Integer, nullable=False)
    energy_wh = Column(BigInteger, nullable=True)      # Energy.Active.Import.Register
    power_w = Column(Numeric(12, 2), nullable=True)    # Power.Active.Import
    soc = Column(Numeric(5, 2), nullable=True)
    sampled_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (Index("ix_meter_tx_time", "ocpp_tx_id", "sampled_at"),)


class ChargerCommand(Base):
    """core → hub → цэнэглэгч командын дараалал (barrier_commands-ийн загвар,
    EV_CHARGING_PLAN.md §3.3: Redis-гүй DB дараалал + SKIP LOCKED поллинг)."""
    __tablename__ = "charger_commands"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    charger_id = Column(UUID(as_uuid=False), ForeignKey("chargers.id"),
                        nullable=False, index=True)
    # RemoteStartTransaction, RemoteStopTransaction, ChangeConfiguration,
    # SetChargingProfile, Reset, UnlockConnector, TriggerMessage, GetConfiguration
    action = Column(String(60), nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    # PENDING → SENT → DONE | FAILED | EXPIRED
    status = Column(String(20), nullable=False, default="PENDING", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    result = Column(JSON, nullable=True)
    requested_by = Column(String(60), default="core")
    expires_at = Column(DateTime, nullable=True)   # хугацаа нь өнгөрвөл EXPIRED
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    sent_at = Column(DateTime, nullable=True)
    done_at = Column(DateTime, nullable=True)


class OcppMessage(Base):
    """Оношилгооны түүхий лог — 7 хоногийн retention (§5)."""
    __tablename__ = "ocpp_messages"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    cp_id = Column(String(60), nullable=False, index=True)
    direction = Column(String(4), nullable=False)  # in | out
    action = Column(String(60), nullable=False, default="")
    message_id = Column(String(60), nullable=False, default="")
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class HubEvent(Base):
    """Core руу push хийх үйл явдлын outbox (at-least-once хүргэлт).

    Core унтарсан үед ч үйл явдал алдагдахгүй: PENDING хэвээр үлдэж,
    core сэргэмэгц дарааллаараа хүргэгдэнэ. Амжилттай хүргэгдсэн нь DONE.
    """
    __tablename__ = "hub_events"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # ev.tx.started | ev.meter | ev.tx.stopped | ev.status | ev.boot | ev.offline
    kind = Column(String(40), nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    status = Column(String(20), nullable=False, default="PENDING", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    delivered_at = Column(DateTime, nullable=True)


class TxCounter(Base):
    """ocpp_tx_id-ийн атомар counter (нэг мөр). Postgres sequence ашиглаж
    болох ч SQLite тесттэй нийцтэй байлгахаар энгийн мөр + FOR UPDATE."""
    __tablename__ = "tx_counter"
    id = Column(Integer, primary_key=True, default=1)
    value = Column(Integer, nullable=False, default=1000)
