import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.enums import ScanType


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


class ScanHistory(Base):
    """Mirrors com.example.data.model.ScanHistoryEntity"""

    __tablename__ = "scan_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    product_name: Mapped[str] = mapped_column("product_name", String(255), nullable=False)
    brand: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    health_score: Mapped[int] = mapped_column("health_score", Integer, nullable=False)
    scanned_at: Mapped[int] = mapped_column("scanned_at", BigInteger, default=_now_ms, index=True)
    scan_type: Mapped[ScanType] = mapped_column("scan_type", Enum(ScanType, name="scan_type", native_enum=True))
