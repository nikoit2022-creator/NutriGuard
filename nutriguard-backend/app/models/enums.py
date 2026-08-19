import enum


class RiskLevel(str, enum.Enum):
    """Mirrors com.example.data.model.RiskLevel"""
    SAFE = "SAFE"
    MODERATE = "MODERATE"
    POTENTIAL_CONCERN = "POTENTIAL_CONCERN"
    HIGH_CONCERN = "HIGH_CONCERN"


class WarningSeverity(str, enum.Enum):
    """Mirrors com.example.util.WarningSeverity"""
    HIGH = "HIGH"
    MODERATE = "MODERATE"
    INFO = "INFO"


class ScanType(str, enum.Enum):
    """Mirrors the scanType string values used in ScanHistoryEntity"""
    BARCODE = "BARCODE"
    OCR_LABEL = "OCR_LABEL"
    MANUAL_INPUT = "MANUAL_INPUT"


class Platform(str, enum.Enum):
    ANDROID = "ANDROID"
    IOS = "IOS"
