"""OCPP-J 1.6 хүрээ (frame) — [MessageTypeId, UniqueId, ...] задлах/угсрах.

  CALL       = [2, "id", "Action", {payload}]
  CALLRESULT = [3, "id", {payload}]
  CALLERROR  = [4, "id", "code", "desc", {details}]

Гадны бохир өгөгдөлд УНАХГҮЙ: буруу frame бүр OcppProtocolError болж,
дуудагч тал CallError буцаана — WS холболт таслахгүй.
"""
import json
import uuid

CALL = 2
CALLRESULT = 3
CALLERROR = 4

# OCPP 1.6J-ийн стандарт алдааны кодууд
ERR_NOT_IMPLEMENTED = "NotImplemented"
ERR_NOT_SUPPORTED = "NotSupported"
ERR_INTERNAL = "InternalError"
ERR_PROTOCOL = "ProtocolError"
ERR_FORMATION = "FormationViolation"


class OcppProtocolError(Exception):
    def __init__(self, code: str, description: str, message_id: str | None = None):
        super().__init__(description)
        self.code = code
        self.description = description
        self.message_id = message_id


def new_id() -> str:
    return uuid.uuid4().hex


def parse(raw: str) -> tuple[int, str, str | None, dict]:
    """raw JSON → (type, message_id, action|error_code, payload).

    CALLRESULT үед action нь None, payload нь үр дүн.
    CALLERROR үед action нь алдааны код, payload нь {"description":..,"details":..}.
    """
    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise OcppProtocolError(ERR_FORMATION, f"JSON задлагдсангүй: {e}") from e
    if not isinstance(msg, list) or len(msg) < 3:
        raise OcppProtocolError(ERR_FORMATION, "OCPP frame нь 3+ элементтэй list байх ёстой")
    mtype = msg[0]
    mid = str(msg[1])
    if mtype == CALL:
        if len(msg) < 4 or not isinstance(msg[2], str):
            raise OcppProtocolError(ERR_FORMATION, "CALL: [2,id,action,payload]", mid)
        payload = msg[3] if isinstance(msg[3], dict) else {}
        return CALL, mid, msg[2], payload
    if mtype == CALLRESULT:
        payload = msg[2] if isinstance(msg[2], dict) else {}
        return CALLRESULT, mid, None, payload
    if mtype == CALLERROR:
        desc = msg[3] if len(msg) > 3 else ""
        details = msg[4] if len(msg) > 4 and isinstance(msg[4], dict) else {}
        return CALLERROR, mid, str(msg[2]), {"description": desc, "details": details}
    raise OcppProtocolError(ERR_PROTOCOL, f"Үл мэдэх MessageTypeId: {mtype}", mid)


def call(action: str, payload: dict, message_id: str | None = None) -> tuple[str, str]:
    mid = message_id or new_id()
    return mid, json.dumps([CALL, mid, action, payload], ensure_ascii=False)


def call_result(message_id: str, payload: dict) -> str:
    return json.dumps([CALLRESULT, message_id, payload], ensure_ascii=False)


def call_error(message_id: str, code: str, description: str = "",
               details: dict | None = None) -> str:
    return json.dumps([CALLERROR, message_id, code, description, details or {}],
                      ensure_ascii=False)
