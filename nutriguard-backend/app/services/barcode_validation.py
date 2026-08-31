"""
Barcode format validation and normalization for GTIN-family barcodes
(EAN-8, EAN-13, UPC-A, UPC-E).

The Android client does not perform any client-side checksum validation
today — it hands whatever the camera/ML Kit scanner (or manual text
entry) produced straight to `POST /scan/barcode` as an opaque string,
and the backend's own OCR/label-image pipeline mints synthetic,
non-numeric barcodes (`ocr_...`, `img_...`) for products it creates from
label content. Both of those MUST keep working exactly as before for the
*local database* lookup (see `product_repository.get_by_barcode`), which
is why validation here is used to gate one thing only: whether a barcode
is a plausible real-world GTIN worth spending an external HTTP request
on. Anything that fails this check is never sent to a barcode provider —
it falls straight through to the "not found / label scan required"
response, matching the "reject invalid barcodes before contacting
providers" requirement.

Standard mod-10 (GS1) check digit, and the standard UPC-E -> UPC-A
zero-suppression expansion, per the public GS1 General Specifications.
No proprietary/Android-specific validation logic exists to mirror (grep
confirms no checksum validation anywhere in `android-app/`); this
reimplements the same public GS1 standard the Android scanner's own
barcode library (ML Kit / ZXing) already relies on internally.
"""
import re
from dataclasses import dataclass

_STRIP_CHARS = re.compile(r"[\s-]")


@dataclass(frozen=True)
class BarcodeInfo:
    """A validated, normalized barcode, in every representation a
    provider adapter might need."""

    raw: str
    format: str  # "EAN_13" | "EAN_8" | "UPC_A" | "UPC_E"
    gtin13: str  # zero-padded to 13 digits — the canonical storage/lookup key (see product_repository)
    gtin14: str  # zero-padded to 14 digits — canonical GS1 Digital Link form
    # True only for an 8-digit value that validates as BOTH EAN-8 (used)
    # AND, independently, as a UPC-E encoding that expands to a
    # checksum-valid UPC-A -- see `validate_and_normalize`'s EAN-8/UPC-E
    # precedence rule. Surfaced (rather than silently resolved) so a
    # caller can log/flag it; the barcode itself is still resolved
    # deterministically as EAN-8.
    ambiguous_upc_e: bool = False


def _checksum_ok(digits: str) -> bool:
    """Standard GS1 mod-10 check digit for a digit string of length >= 2
    (the last digit is the check digit, weights alternate 3/1 starting
    from the rightmost digit of the body)."""
    body, check = digits[:-1], digits[-1]
    total = 0
    for i, ch in enumerate(reversed(body)):
        weight = 3 if i % 2 == 0 else 1
        total += int(ch) * weight
    expected = (10 - (total % 10)) % 10
    return expected == int(check)


def _expand_upc_e_to_upc_a(digits8: str) -> str | None:
    """Standard zero-suppressed UPC-E (number system digit + 6 data
    digits + check digit) -> full 12-digit UPC-A, per the GS1 General
    Specifications expansion table. Returns None for a number system
    digit UPC-E doesn't support (only 0 and 1 are defined)."""
    number_system, data, check = digits8[0], digits8[1:7], digits8[7]
    if number_system not in ("0", "1"):
        return None

    last = data[5]
    if last in ("0", "1", "2"):
        expanded_body = data[0:2] + last + "0000" + data[2:5]
    elif last == "3":
        expanded_body = data[0:3] + "00000" + data[3:5]
    elif last == "4":
        expanded_body = data[0:4] + "00000" + data[4]
    else:  # 5, 6, 7, 8, 9
        expanded_body = data[0:5] + "0000" + last

    return number_system + expanded_body + check


def validate_and_normalize(raw: str | None) -> BarcodeInfo | None:
    """Returns a `BarcodeInfo` for a structurally valid EAN-8/EAN-13/
    UPC-A/UPC-E barcode (correct length + correct GS1 check digit), or
    `None` for anything else (wrong length, non-numeric, synthetic
    `ocr_.../img_...` ids, bad checksum). `None` means "do not contact
    any barcode provider for this value" — it does NOT affect the local
    DB lookup, which already happened earlier and accepts any string."""
    if not raw:
        return None
    candidate = _STRIP_CHARS.sub("", raw.strip())
    if not candidate.isdigit():
        return None

    if len(candidate) == 13:
        if not _checksum_ok(candidate):
            return None
        return BarcodeInfo(raw=raw, format="EAN_13", gtin13=candidate, gtin14="0" + candidate)

    if len(candidate) == 12:
        if not _checksum_ok(candidate):
            return None
        return BarcodeInfo(raw=raw, format="UPC_A", gtin13="0" + candidate, gtin14="00" + candidate)

    if len(candidate) == 8:
        # EAN-8 and an explicit (number-system + check-digit) UPC-E are
        # both valid 8-digit encodings, checked independently of one
        # another -- an 8-digit string can, for some inputs, validate as
        # BOTH (see tests/unit/test_barcode_validation.py for a
        # constructed example). This is a genuine, explicit ambiguity in
        # the input, not a bug: GS1 defines no way to distinguish them
        # from the digits alone (a real scanner knows which symbology it
        # read; a bare 8-digit string does not carry that). The
        # documented precedence rule: EAN-8 wins when both validate --
        # it is by far the more common 8-digit retail format -- and the
        # collision is surfaced via `ambiguous_upc_e` rather than
        # silently dropped, so a caller can log/flag it if it matters.
        ean8_valid = _checksum_ok(candidate)
        upc_a = _expand_upc_e_to_upc_a(candidate)
        upc_e_valid = upc_a is not None and _checksum_ok(upc_a)

        if ean8_valid:
            return BarcodeInfo(
                raw=raw,
                format="EAN_8",
                gtin13="00000" + candidate,
                gtin14="000000" + candidate,
                ambiguous_upc_e=upc_e_valid,
            )
        if upc_e_valid:
            return BarcodeInfo(raw=raw, format="UPC_E", gtin13="0" + upc_a, gtin14="00" + upc_a)
        return None

    return None


def alias_keys(info: BarcodeInfo) -> list[str]:
    """
    Every plausible storage key a PRE-EXISTING row for this exact
    physical barcode could already be under, canonical form first.
    Used only to find a legacy row (e.g. one stored before/without this
    module's canonical-GTIN-13 convention, or written by some other
    path) — new rows are always persisted under `gtin13` itself (see
    `product_repository.insert_new` / `food_analysis.py`), never under
    an alias.

    UPC-A and its EAN-13-zero-padded form are the same physical barcode
    (PR #7 review, round 2, finding 3): scanning "0036000291452"
    (EAN-13) must still find a legacy row stored as "036000291452"
    (UPC-A), and vice versa. EAN-8/UPC-E have no such shorter/longer
    alias to consider — an EAN-8 is a genuinely distinct (shorter)
    symbology, not a zero-padding variant of a 13-digit GTIN.
    """
    keys = [info.gtin13]
    if info.format in ("EAN_13", "UPC_A") and info.gtin13.startswith("0"):
        twelve_digit = info.gtin13[1:]
        if twelve_digit and twelve_digit != info.gtin13:
            keys.append(twelve_digit)
    return keys
