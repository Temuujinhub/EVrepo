"""DB-д хадгалагдах нууц утгын шифрлэлт (Fernet) — PARKING-ийн secretbox-ийн
хуулбар зарчим: EVHUB_SECRET_ENC_KEY хоосон бол шифрлэхгүй (нийцтэй горим),
"enc:" угтвартай утгыг тайлж чадахгүй бол ЧАНГА алдаа.

Юуг хамгаалдаг: цэнэглэгч бүрийн OCPP Basic auth нууц үг
(chargers.auth_pass_enc). Backup/pg_dump-д ил гарахаас сэргийлнэ.

Түлхүүр үүсгэх:
  venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
import logging

from .config import settings

log = logging.getLogger("evhub.secretbox")

_PREFIX = "enc:"


def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(settings.secret_enc_key.encode())


def is_encrypted(value: str | None) -> bool:
    return bool(value and value.startswith(_PREFIX))


def encrypt_secret(value: str | None) -> str | None:
    if not value or not settings.secret_enc_key:
        return value
    if is_encrypted(value):
        return value
    return _PREFIX + _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str | None) -> str:
    if not value:
        return ""
    if not is_encrypted(value):
        return value
    if not settings.secret_enc_key:
        raise ValueError("Шифрлэгдсэн нууц утга байна, гэвч EVHUB_SECRET_ENC_KEY "
                         "тохируулаагүй — .env-ээ шалгана уу")
    from cryptography.fernet import InvalidToken
    try:
        return _fernet().decrypt(value[len(_PREFIX):].encode()).decode()
    except InvalidToken as e:
        log.error("Нууц утгыг тайлж чадсангүй — EVHUB_SECRET_ENC_KEY буруу эсвэл солигдсон")
        raise ValueError("Нууц утгыг тайлж чадсангүй (түлхүүр зөрсөн)") from e
