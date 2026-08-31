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


# --- Canonical representation / equivalence (PR #7 review, finding 5) ------


def test_whitespace_and_dashed_variants_share_one_canonical_gtin13():
    """The three ways someone might scan/type the same EAN-13 must all
    resolve to the identical canonical storage key."""
    plain = validate_and_normalize("4006381333931")
    spaced = validate_and_normalize(" 4006381333931 ")
    dashed = validate_and_normalize("400-638-133-3931")
    assert plain.gtin13 == spaced.gtin13 == dashed.gtin13 == "4006381333931"


def test_upc_a_and_its_ean13_equivalent_share_one_canonical_gtin13():
    """"036000291452" (UPC-A, as a scanner would read it) and
    "0036000291452" (the exact same product's EAN-13-zero-padded form,
    as some scanners/manual entry produce) must converge on one
    canonical key -- this is what prevents duplicate products for what
    is, physically, the same barcode."""
    upc_a = validate_and_normalize("036000291452")
    ean13_form = validate_and_normalize("0036000291452")
    assert upc_a is not None and ean13_form is not None
    assert upc_a.gtin13 == ean13_form.gtin13 == "0036000291452"


# --- UPC-E / EAN-8 ambiguity (PR #7 review, finding 5) ----------------------


def test_ean8_takes_precedence_when_also_valid_as_upc_e():
    """"01000009" independently validates as BOTH a checksum-correct
    EAN-8 AND a UPC-E that expands to a checksum-correct UPC-A -- a
    genuine ambiguity GS1 doesn't resolve from the digits alone. The
    documented precedence rule (EAN-8 wins, the far more common 8-digit
    retail format) must be applied deterministically, and the collision
    must be surfaced rather than silently hidden."""
    info = validate_and_normalize("01000009")
    assert info is not None
    assert info.format == "EAN_8"
    assert info.gtin13 == "0000001000009"
    assert info.ambiguous_upc_e is True


def test_pure_upc_e_barcode_invalid_as_ean8_is_still_resolved():
    """"01000018" fails the direct EAN-8 checksum but is a valid UPC-E
    (number system 0) that expands to a checksum-correct UPC-A -- must
    still resolve, unambiguously, as UPC_E."""
    info = validate_and_normalize("01000018")
    assert info is not None
    assert info.format == "UPC_E"
    assert info.gtin13 == "0010100000008"
    assert info.ambiguous_upc_e is False


def test_unambiguous_ean8_is_not_flagged_ambiguous():
    info = validate_and_normalize("40170725")
    assert info is not None
    assert info.format == "EAN_8"
    assert info.ambiguous_upc_e is False
