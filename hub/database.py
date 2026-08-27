"""DB engine — PARKING-ийн database.py-тэй ижил хамгаалалттай.

Hub нь async event loop дотроос sync psycopg2 ашигладаг тул query/lock-ийн
хатуу дээд хугацаанууд заавал: үгүй бол нэг гацсан query 10 цэнэглэгчийн
WS холболтыг бүгдийг царцаана (PARKING дээр хаалт 101 секунд болсон сургамж).
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

_connect_args = {}
if settings.database_url.startswith("postgres"):
    _connect_args = {
        "connect_timeout": 10,
        "options": "-c statement_timeout=30000 -c lock_timeout=3000 "
                   "-c idle_in_transaction_session_timeout=300000",
    }

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    connect_args=_connect_args,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
