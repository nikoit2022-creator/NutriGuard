from app.models.enums import ScanType
from app.schemas.common import ORMModel


class ScanHistoryOut(ORMModel):
    """Mirrors com.example.data.model.ScanHistoryEntity (API Contract 5.6)."""

    id: int
    barcode: str | None = None
    product_name: str
    brand: str
    health_score: int
    scanned_at: int
    scan_type: ScanType
