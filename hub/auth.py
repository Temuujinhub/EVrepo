"""Нэвтрэлт: (1) цэнэглэгчийн WS Basic auth, (2) internal API-ийн Bearer түлхүүр.

§3.4 дүрэм: URL дэх cp_id ≡ Basic username байх ёстой, өөр бол 403.
Цэнэглэгч бүр DB-д өөрийн нууц үгтэй; DB-д бүртгэлгүй cp_id зөвхөн
provision хосоор (auto_register=true үед) орж ирнэ.
"""
import base64
import hmac
import logging

from .config import settings
from .models import Charger
from .secretbox import decrypt_secret

log = logging.getLogger("evhub.auth")


def _consteq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def parse_basic(header: str | None) -> tuple[str, str] | None:
    if not header or not header.lower().startswith("basic "):
        return None
    try:
        raw = base64.b64decode(header.split(" ", 1)[1]).decode()
        user, _, pwd = raw.partition(":")
        return user, pwd
    except Exception:
        return None


def check_charger_auth(db, cp_id: str, auth_header: str | None):
    """→ (Charger | None, шалтгаан). Charger буцвал нэвтрэлт OK.
    auto_register үед бүртгэлгүй cp_id-д NEW charger үүсгэж буцаана."""
    creds = parse_basic(auth_header)
    if not creds:
        return None, "Basic auth ирсэнгүй"
    user, pwd = creds
    if user != cp_id:
        # §3.4: username ≡ cp_id — өөр бол шууд татгалзана
        return None, f"username ({user}) != cp_id ({cp_id})"
    charger = db.query(Charger).filter(Charger.cp_id == cp_id).first()
    if charger:
        if charger.status == "DISABLED":
            return None, "цэнэглэгч идэвхгүй (DISABLED)"
        expected = decrypt_secret(charger.auth_pass_enc)
        if expected and _consteq(pwd, expected):
            return charger, ""
        return None, "нууц үг буруу"
    # Бүртгэлгүй cp_id — provision хосоор auto-register
    if (settings.auto_register and settings.provision_user and
            settings.provision_password and
            _consteq(pwd, settings.provision_password)):
        from .secretbox import encrypt_secret
        charger = Charger(cp_id=cp_id, name=cp_id, auth_user=cp_id,
                          auth_pass_enc=encrypt_secret(settings.provision_password) or "",
                          status="NEW")
        db.add(charger)
        db.commit()
        db.refresh(charger)
        log.warning("cp=%s: шинэ цэнэглэгч auto-register хийгдлээ (status=NEW). "
                    "Админ нууц үгийг нь СОЛИХ ёстой.", cp_id)
        return charger, ""
    return None, "бүртгэлгүй cp_id"


def check_internal_key(auth_header: str | None) -> bool:
    """core → hub internal API: Authorization: Bearer <EVHUB_INTERNAL_API_KEY>."""
    if not settings.internal_api_key:
        return False
    if not auth_header or not auth_header.lower().startswith("bearer "):
        return False
    return _consteq(auth_header.split(" ", 1)[1].strip(), settings.internal_api_key)
