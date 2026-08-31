"""Barcode format validation/normalization — EAN-8, EAN-13, UPC-A, UPC-E."""
from app.services.barcode_validation import validate_and_normalize


def test_valid_ean13_is_normalized():
    # Well-known real EAN-13 test barcode.
    info = validate_and_normalize("4006381333931")
    assert info is not None
    assert info.format == "EAN_13"
    assert info.gtin13 == "4006381333931"
    assert info.gtin14 == "04006381333931"


def test_valid_upc_a_is_normalized_to_gtin13():
    # Well-known real UPC-A test barcode.
    info = validate_and_normalize("036000291452")
    assert info is not None
    assert info.format == "UPC_A"
    assert info.gtin13 == "0036000291452"
    assert info.gtin14 == "00036000291452"


def test_valid_ean8_is_normalized():
    info = validate_and_normalize("40170725")
    assert info is not None
    assert info.format == "EAN_8"
    assert info.gtin13 == "0000040170725"


def test_checksum_mismatch_rejected():
    # Same digits as a valid EAN-13 but with the check digit corrupted.
    assert validate_and_normalize("4006381333930") is None


def test_wrong_length_rejected():
    assert validate_and_normalize("123") is None
    assert validate_and_normalize("12345678901234") is None


def test_non_numeric_rejected():
    assert validate_and_normalize("not-a-barcode") is None


def test_synthetic_ocr_and_image_barcodes_rejected():
    """These are the backend's own OCR/image-analysis synthetic ids —
    must never be treated as a real GTIN worth an external HTTP call."""
    assert validate_and_normalize("ocr_1735689600000") is None
    assert validate_and_normalize("img_1735689600000") is None


def test_none_and_empty_rejected():
    assert validate_and_normalize(None) is None
    assert validate_and_normalize("") is None


def test_whitespace_and_dashes_are_stripped_before_validation():
    info = validate_and_normalize(" 4006381333931 ")
    assert info is not None
    assert info.format == "EAN_13"

    info_dashed = validate_and_normalize("400-638-133-3931")
    assert info_dashed is not None
    assert info_dashed.gtin13 == "4006381333931"
