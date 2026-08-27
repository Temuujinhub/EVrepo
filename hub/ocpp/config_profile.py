"""Цэнэглэгч холбогдмогц (BootNotification) автоматаар тулгах тохиргоо —
EV_CHARGING_PLAN.md §4.3. Гараар HMI дээр тохируулах алдааг үндсээр арилгана.

Тохиргоо бүр ТУСДАА ChargeConfiguration команд болж дараалалд орно: аль нэг
түлхүүрийг цэнэглэгч дэмжихгүй (NotSupported) байсан ч бусад нь тавигдана.
"""
from ..config import settings


def boot_config_items() -> list[dict]:
    return [
        {"key": "MeterValueSampleInterval", "value": str(settings.meter_sample_interval)},
        {"key": "MeterValuesSampledData",
         "value": "Energy.Active.Import.Register,Power.Active.Import,SoC"},
        {"key": "HeartbeatInterval", "value": str(settings.heartbeat_interval)},
        {"key": "WebSocketPingInterval", "value": "60"},
        {"key": "AllowOfflineTxForUnknownId", "value": "false"},
        {"key": "LocalPreAuthorize", "value": "false"},
        {"key": "StopTransactionOnInvalidId", "value": "true"},
        {"key": "ConnectionTimeOut", "value": "120"},
    ]
