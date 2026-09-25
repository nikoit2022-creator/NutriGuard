import time
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_id
from app.core.config import settings
from app.core.exceptions import AppError, ImageTooLargeError, ProductNotFoundError, ValidationAppError
from app.core.rate_limit import SCAN_RATE, limiter
from app.core.scan_diagnostics import record_scan_diagnostic
from app.database.session import get_db
from app.schemas.scan import BarcodeScanRequest, FullProductAnalysisOut, OcrTextScanRequest
from app.services import food_analysis
from app.services.barcode_text_safety import clean_optional

router = APIRouter(prefix="/scan", tags=["scan"])

_logger = structlog.get_logger(__name__)


def _safe_record_scan_diagnostic(**fields: Any) -> None:
    """Local defense-in-depth on top of `record_scan_diagnostic`'s own
    internal best-effort guarantee (task: "diagnostic failures must
    never break scanning"). `record_scan_diagnostic` already swallows
    every exception itself today, but this call site must not depend
    solely on that implementation detail never regressing -- a
    diagnostics failure must never replace a real response/error with
    an unrelated exception, at every one of THIS router's own call
    sites, independent of what `app.core.scan_diagnostics` does
    internally."""
    try:
        record_scan_diagnostic(**fields)
    except Exception:  # noqa: BLE001
        _logger.warning("scan_diagnostic_write_failed")


_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png"}


def _to_analysis_out(result: dict) -> FullProductAnalysisOut:
    return FullProductAnalysisOut(
        product=result["product"],
        ingredients=result["ingredients"],
        health_score=result["health_score"],
        warnings=result["warnings"],
        is_from_database_cache=result["is_from_database_cache"],
    )


def _finish_serializing(out: FullProductAnalysisOut) -> None:
    """Forces the SAME serialization FastAPI's `response_model` machinery
    performs later, INSIDE this endpoint's own try block (task: "record
    serialization failures as failures and avoid premature success").

    Constructing `FullProductAnalysisOut(...)` (see `_to_analysis_out`)
    only runs Pydantic's field validation -- it does NOT evaluate every
    nested `@computed_field` (e.g. `IngredientOut.localizations`/
    `adi_population_scope`) or run full JSON encoding; those happen
    later, inside `fastapi.routing`'s own response rendering, AFTER this
    endpoint function has already returned -- outside any try/except
    here. A computed-field/encoding failure there would previously leave
    a false "success" diagnostic line behind (the endpoint's own
    response was never actually sent). Calling `model_dump(mode="json")`
    here evaluates everything response rendering would, so the same
    exception surfaces INSIDE this function's try block and is diagnosed
    as a genuine failure, never a guessed/deferred success. Return value
    intentionally discarded -- this call exists purely to force
    evaluation, not to replace `out`.
    """
    out.model_dump(mode="json", by_alias=True)


def _ingredient_language_counts(ingredients: list[Any]) -> dict:
    """Recognized/unresolved/untranslated ingredient counts (task:
    "recognized, unresolved, and untranslated ingredient counts") --
    computed from what `app.services.ingredient_catalog` actually
    persisted for THIS scan, never guessed. `unresolved` is every
    ingredient flagged `identity_uncertain` for ANY reason (suspected
    OCR concatenation, ambiguous segmentation, or an unreliable
    translation attempt); `untranslated` is specifically the subset
    where a translation was attempted but could not be verified (see
    `app.services.ingredient_translation`).
    """
    unresolved = 0
    untranslated = 0
    for ing in ingredients:
        if getattr(ing, "identity_uncertain", False):
            unresolved += 1
            if getattr(ing, "uncertainty_reason", None) == "TRANSLATION_UNRELIABLE":
                untranslated += 1
    return {
        "recognizedIngredientCount": len(ingredients),
        "unresolvedIngredientCount": unresolved,
        "untranslatedIngredientCount": untranslated,
    }


# `label_language_status` (see `app.services.label_language.LabelTextResult.status`)
# -> the real, OBSERVED translation attempt/result/reason for THIS
# request (task: "report actual translation attempt/result/reason where
# available, rather than inferring it solely from Product.source").
# "ok" means no foreign-language text was ever found -- no attempt was
# needed, never "translation succeeded trivially".
_TRANSLATION_RESULT_BY_STATUS = {
    "ok": "not_needed",
    "translated": "translated",
    "translation_unreliable_fallback": "unreliable_fallback",
}


def _translation_fields(result: dict) -> dict:
    """`result` is the raw dict `app.services.food_analysis` returns.
    `label_language_status` is present only on paths that actually ran
    the language policy (the barcode-linked-enrichment and standalone
    label/OCR finalize functions) -- ABSENT (not `None`, genuinely
    missing) on e.g. `analyze_barcode`'s pure identity lookup, which
    never touches label/OCR text at all. Diagnostics must treat that
    absence as honestly "not applicable", never fall back to guessing
    from `Product.source`.

    Code-review fix: `translationAttempted`/`translationResult`/
    `translationReason` used to reflect ONLY the whole-label-blob pass
    (`app.services.label_language`) -- a mixed label where that pass
    reports `status="ok"` (nothing to translate at the whole-blob level)
    could still have individual embedded foreign tokens translated or
    rejected by the SEPARATE per-ingredient pass
    (`ingredient_catalog._resolve_ingredient_languages`), which was
    silently invisible here. `ingredientTranslationAttempted`/
    `ingredientTranslationReliableCount`/
    `ingredientTranslationUnreliableCount`/`ingredientTranslationLanguages`
    below report that pass explicitly and separately -- never merged
    into the whole-label fields, so a caller can always tell which pass
    produced which outcome. `translationAttempted` is now `True` if
    EITHER pass actually ran, so it can no longer silently miss the
    per-ingredient-only case. `detectedLanguage` was computed by
    `food_analysis` (`label_detected_language`) but never surfaced here
    before -- now included whenever the whole-label pass ran."""
    status = result.get("label_language_status")
    summary = result.get("ingredient_translation_summary")
    ingredient_attempted = bool(summary.attempted) if summary is not None else False
    ingredient_reliable = summary.reliable if summary is not None else None
    ingredient_unreliable = summary.unreliable if summary is not None else None
    ingredient_languages = list(summary.detected_languages) if summary is not None else None

    if status is None and not ingredient_attempted:
        return {
            "translationAttempted": None,
            "translationResult": None,
            "translationReason": None,
            "detectedLanguage": None,
            "ingredientTranslationAttempted": ingredient_attempted if summary is not None else None,
            "ingredientTranslationReliableCount": ingredient_reliable,
            "ingredientTranslationUnreliableCount": ingredient_unreliable,
            "ingredientTranslationLanguages": ingredient_languages,
        }

    whole_label_attempted = status is not None and (
        bool(result.get("label_translation_used")) or status != "ok"
    )
    return {
        "translationAttempted": whole_label_attempted or ingredient_attempted,
        "translationResult": None if status is None else _TRANSLATION_RESULT_BY_STATUS.get(status, status),
        "translationReason": None if status is None or status == "ok" else status,
        "detectedLanguage": result.get("label_detected_language") if status is not None else None,
        "ingredientTranslationAttempted": ingredient_attempted,
        "ingredientTranslationReliableCount": ingredient_reliable,
        "ingredientTranslationUnreliableCount": ingredient_unreliable,
        "ingredientTranslationLanguages": ingredient_languages,
    }


def _label_extraction_fields(source: dict) -> dict:
    """Issue #25: the label-image extraction outcome
    (`food_analysis._label_extraction_diagnostic_fields`) from a success
    result dict or a `ProductNotFoundError.diagnostic_metadata` -- all
    `None` (and so omitted from the journal line) when absent."""
    return {
        "labelExtraction": source.get("label_extraction"),
        "productIdentityObserved": source.get("label_product_identity_observed"),
        "labelNutritionComplete": source.get("label_nutrition_complete"),
    }


def _diagnostic_base(request: Request, *, operation: str, barcode: str | None) -> dict:
    return {
        "requestId": getattr(request.state, "request_id", None),
        "operation": operation,
        # The REAL observed origin of the data behind this response
        # ("cache" for an already-verified existing row served as-is, a
        # `Product.source` value like "open_food_facts"/"label_scan"/
        # "local" once a product is known, or omitted/`None` while
        # genuinely unknown) -- callers pass `dataSource=` explicitly at
        # each write site via `_observed_data_source`, once actually
        # known, never a guess (task: "dataSource ... identifies the
        # operation, not the actual cache/provider source" -- `operation`
        # above already covers "which endpoint"; `dataSource` covers
        # "where did the data come from"). Deliberately NOT included in
        # this base dict -- every write site supplies its own, so a
        # generic failure branch that has no product yet simply omits it
        # rather than colliding with an explicit value.
        "barcode": barcode,
        # Coarse, honest stages this router layer actually observes --
        # never a guess at what happened deeper inside
        # `app.services.food_analysis`'s own pipeline, which this
        # endpoint layer does not instrument (task: "do not invent stage
        # information that was not observed").
        "stage": "request_validated",
    }


def _is_partial_result(details: dict) -> bool:
    """A PARTIAL result (a real product identity was found, just not
    enough verified data for a Health Score) vs a genuine FAILURE
    (nothing was found at all) -- task: "classify partial results
    consistently across scan endpoints".

    `labelScanRequired` alone is NOT the right discriminator: both
    `food_analysis._label_scan_required_details` (a real, partially-
    known product) AND `_not_found_details` (a barcode no source
    recognizes at all -- nothing persisted, no identity known) set it
    `True`, since both suggest the same next action ("scan the label").
    `discoveredIdentity` is the actual structural signal -- it is only
    ever present when a real product row's identity was actually
    found/persisted (see `_label_scan_required_details`); `_not_found_details`
    never includes it."""
    return bool(details.get("discoveredIdentity"))


def _observed_data_source(result: dict | None, exc_details: dict | None) -> str | None:
    """The real, already-known origin of the data behind this response --
    never inferred/guessed (task: "propagate actual observed source
    information, without guessing").

      - A successful/partial result carries the actual `Product` row:
        `is_from_database_cache` (already computed by `food_analysis`
        from real state, e.g. `analyze_barcode`'s `was_cache_hit`) wins
        when true ("cache"); otherwise the row's own persisted
        `source` column (a real provider name, "local", "label_scan",
        etc.) is the honest answer.
      - A `labelScanRequired`/not-found `AppError.details` dict may
        carry its own `dataSource` (see
        `food_analysis._label_scan_required_details`, set from the SAME
        row) when a partial identity was found even though the request
        ultimately raised.
      - Otherwise (no product ever existed for this request -- total
        not-found, plain validation error, unhandled exception before
        any product was resolved): `None`, honestly "unknown", never a
        placeholder value.
    """
    if result is not None:
        product = result.get("product")
        if product is not None:
            if result.get("is_from_database_cache"):
                return "cache"
            return getattr(product, "source", None)
    if exc_details:
        return exc_details.get("dataSource")
    return None


@router.post("/barcode", response_model=FullProductAnalysisOut)
@limiter.limit(SCAN_RATE)
async def scan_barcode(
    request: Request,
    body: BarcodeScanRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> FullProductAnalysisOut:
    """API Contract 6.1"""
    started = time.perf_counter()
    if not body.barcode.strip():
        raise ValidationAppError("barcode must not be empty.")

    diagnostic_base = _diagnostic_base(request, operation="scan_barcode", barcode=body.barcode.strip())
    try:
        result = await food_analysis.analyze_barcode(db, user_id, body.barcode.strip())
        diagnostic_base["stage"] = "analysis_complete"
        out = _to_analysis_out(result)
        _finish_serializing(out)
        diagnostic_base["stage"] = "response_built"
    except ProductNotFoundError as exc:
        # A barcode whose *identity* was found but lacks enough verified
        # data for a Health Score (`labelScanRequired`) is a PARTIAL
        # result, not a failure -- classified the same way the other two
        # scan endpoints already classify it (task: "classify partial
        # results consistently across scan endpoints").
        details = exc.details if isinstance(exc.details, dict) else {}
        _safe_record_scan_diagnostic(
            **diagnostic_base,
            dataSource=_observed_data_source(None, details),
            outcome="partial" if _is_partial_result(details) else "failed",
            errorCode=exc.code,
            durationMs=round((time.perf_counter() - started) * 1000, 2),
        )
        raise
    except AppError as exc:
        _safe_record_scan_diagnostic(
            **diagnostic_base,
            outcome="failed",
            errorCode=exc.code,
            durationMs=round((time.perf_counter() - started) * 1000, 2),
        )
        raise
    except Exception:
        _safe_record_scan_diagnostic(
            **diagnostic_base,
            outcome="failed",
            errorCode="INTERNAL_ERROR",
            durationMs=round((time.perf_counter() - started) * 1000, 2),
        )
        raise

    # Exactly one final summary record for this operation (task: "one
    # final summary record per operation") -- every branch above either
    # writes its own single record and re-raises, or falls through to
    # this one; none can run more than once for the same request.
    _safe_record_scan_diagnostic(
        **diagnostic_base,
        dataSource=_observed_data_source(result, None),
        outcome="success",
        **_ingredient_language_counts(result["ingredients"]),
        durationMs=round((time.perf_counter() - started) * 1000, 2),
    )
    return out


@router.post("/ocr-text", response_model=FullProductAnalysisOut)
@limiter.limit(SCAN_RATE)
async def scan_ocr_text(
    request: Request,
    body: OcrTextScanRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> FullProductAnalysisOut:
    """API Contract 6.2. `body.barcode` is optional (see `OcrTextScanRequest`
    docstring) -- when supplied it routes through the same barcode
    enrichment/upsert pipeline `/scan/label-image` uses; omitted, it
    behaves exactly as before."""
    started = time.perf_counter()
    if len(body.raw_text.strip()) < 3:
        raise ValidationAppError("rawText must be at least 3 characters.")

    barcode = clean_optional(body.barcode)
    diagnostic_base = _diagnostic_base(request, operation="scan_ocr_text", barcode=barcode)
    # Assigned only once `food_analysis` actually returns; stays `None`
    # if it raises before ever returning (e.g. a genuine internal error
    # mid-pipeline) -- referenced defensively below, never assumed set.
    result: dict | None = None
    try:
        if barcode:
            result = await food_analysis.analyze_ocr_text_with_barcode(
                db, user_id, body.raw_text.strip(), barcode
            )
        else:
            result = await food_analysis.analyze_ocr_text(db, user_id, body.raw_text.strip())
        diagnostic_base["stage"] = "analysis_complete"
        out = _to_analysis_out(result)
        _finish_serializing(out)
        diagnostic_base["stage"] = "response_built"
    except ProductNotFoundError as exc:
        details = exc.details if isinstance(exc.details, dict) else {}
        _safe_record_scan_diagnostic(
            **{**diagnostic_base, "barcode": barcode},
            dataSource=_observed_data_source(None, details),
            outcome="partial" if _is_partial_result(details) else "failed",
            errorCode=exc.code,
            # Code-review fix: a translation attempt observed before
            # THIS specific `ProductNotFoundError` (raised after
            # `food_analysis` already ran its language/translation
            # policy -- see `diagnostic_metadata=`'s docstring) is
            # preserved here -- never present for a not-found/failure
            # raised BEFORE any translation could have happened, since
            # `diagnostic_metadata` is `None` at those raise sites.
            **_translation_fields(exc.diagnostic_metadata or {}),
            durationMs=round((time.perf_counter() - started) * 1000, 2),
        )
        raise
    except AppError as exc:
        _safe_record_scan_diagnostic(
            **diagnostic_base,
            outcome="failed",
            errorCode=exc.code,
            durationMs=round((time.perf_counter() - started) * 1000, 2),
        )
        raise
    except Exception:
        # Code-review fix: a LATER failure (response construction /
        # `_finish_serializing`) after `food_analysis` already
        # succeeded and returned `result` must not silently drop the
        # translation work it already observed -- `result` is `None`
        # only when `food_analysis` itself never returned at all.
        _safe_record_scan_diagnostic(
            **diagnostic_base,
            outcome="failed",
            errorCode="INTERNAL_ERROR",
            **_translation_fields(result or {}),
            durationMs=round((time.perf_counter() - started) * 1000, 2),
        )
        raise

    product = result["product"]
    _safe_record_scan_diagnostic(
        **{**diagnostic_base, "barcode": product.barcode},
        dataSource=_observed_data_source(result, None),
        outcome="success",
        **_translation_fields(result),
        **_ingredient_language_counts(result["ingredients"]),
        durationMs=round((time.perf_counter() - started) * 1000, 2),
    )
    return out


@router.post("/label-image", response_model=FullProductAnalysisOut)
@limiter.limit(SCAN_RATE)
async def scan_label_image(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
    image: UploadFile = File(...),
    barcode: str | None = Form(default=None),
) -> FullProductAnalysisOut:
    """
    API Contract 6.3, extended with an optional multipart `barcode`
    field: lets a client that already attempted POST /scan/barcode (and
    got `labelScanRequired`) resubmit the same barcode alongside the
    label image, so the backend can combine both sources into one
    canonical, persisted product instead of a synthetic `img_...` one.

    `barcode` omitted, blank, or a literal placeholder (e.g. "null",
    "N/A") -> behaves EXACTLY as before (`food_analysis.
    analyze_label_image`), fully backward compatible with existing
    clients. A supplied barcode is validated/canonicalized through the
    same `barcode_validation` module `/scan/barcode` uses; an invalid
    one returns the standard structured `VALIDATION_ERROR` response.
    """
    started = time.perf_counter()
    cleaned_barcode = clean_optional(barcode)
    # Early validation failures (bad content-type, oversized image) now
    # get a diagnostic record too -- previously these raised before
    # `diagnostic_base` was even built, so the diagnostics journal
    # silently had no coverage of them at all.
    diagnostic_base = _diagnostic_base(request, operation="scan_label_image", barcode=cleaned_barcode)
    diagnostic_base["stage"] = "content_type_validated"

    if image.content_type not in _ALLOWED_IMAGE_TYPES:
        _safe_record_scan_diagnostic(
            **diagnostic_base,
            outcome="failed",
            errorCode="VALIDATION_ERROR",
            durationMs=round((time.perf_counter() - started) * 1000, 2),
        )
        raise ValidationAppError(f"Unsupported image type: {image.content_type}")

    contents = await image.read()
    diagnostic_base["stage"] = "image_read"
    if len(contents) > settings.MAX_IMAGE_SIZE_BYTES:
        _safe_record_scan_diagnostic(
            **diagnostic_base,
            outcome="failed",
            errorCode="IMAGE_TOO_LARGE",
            durationMs=round((time.perf_counter() - started) * 1000, 2),
        )
        raise ImageTooLargeError(
            f"Image exceeds the {settings.MAX_IMAGE_SIZE_BYTES // (1024 * 1024)}MB limit."
        )

    # Assigned only once `food_analysis` actually returns; stays `None`
    # if it raises before ever returning -- referenced defensively
    # below, never assumed set.
    result: dict | None = None
    try:
        if cleaned_barcode:
            result = await food_analysis.analyze_label_image_with_barcode(
                db, user_id, contents, cleaned_barcode
            )
        else:
            result = await food_analysis.analyze_label_image(db, user_id, contents)
        diagnostic_base["stage"] = "analysis_complete"
        # Response construction AND full serialization happen INSIDE
        # this try block now, not after it: a "success" record must only
        # ever be written once the response actually exists and would
        # actually serialize (task: "record success only after response
        # construction/serialization succeeds") -- a `FullProductAnalysisOut`
        # validation OR computed-field/encoding error now correctly
        # falls into the generic `except Exception` below instead of
        # leaving a false "success" diagnostic line behind (see
        # `_finish_serializing`).
        out = _to_analysis_out(result)
        _finish_serializing(out)
        diagnostic_base["stage"] = "response_built"
    except ProductNotFoundError as exc:
        details = exc.details if isinstance(exc.details, dict) else {}
        ingredients = details.get("ingredients")
        _safe_record_scan_diagnostic(
            **diagnostic_base,
            dataSource=_observed_data_source(None, details),
            outcome="partial" if _is_partial_result(details) else "failed",
            errorCode=exc.code,
            nutritionRecognized=not bool(details.get("nutritionScanRequired", True)),
            ingredientsRecognized=not bool(details.get("ingredientsScanRequired", True)),
            recognizedIngredientCount=len(ingredients) if isinstance(ingredients, list) else 0,
            # Code-review fix: preserve an already-observed translation
            # attempt across a LATER `ProductNotFoundError` -- see
            # `diagnostic_metadata=`'s docstring in `app.core.exceptions`.
            **_translation_fields(exc.diagnostic_metadata or {}),
            **_label_extraction_fields(exc.diagnostic_metadata or {}),
            durationMs=round((time.perf_counter() - started) * 1000, 2),
        )
        raise
    except AppError as exc:
        _safe_record_scan_diagnostic(
            **diagnostic_base,
            outcome="failed",
            errorCode=exc.code,
            durationMs=round((time.perf_counter() - started) * 1000, 2),
        )
        raise
    except Exception:
        # Code-review fix: preserve translation work `food_analysis`
        # already observed and returned before a LATER response-
        # construction/serialization failure -- `result` is `None` only
        # when `food_analysis` itself never returned at all.
        _safe_record_scan_diagnostic(
            **diagnostic_base,
            outcome="failed",
            errorCode="INTERNAL_ERROR",
            **_translation_fields(result or {}),
            **_label_extraction_fields(result or {}),
            durationMs=round((time.perf_counter() - started) * 1000, 2),
        )
        raise

    product = result["product"]
    _safe_record_scan_diagnostic(
        **{**diagnostic_base, "barcode": product.barcode},
        dataSource=_observed_data_source(result, None),
        outcome="success",
        nutritionRecognized=product.has_verified_nutrition,
        ingredientsRecognized=product.has_verified_ingredients,
        nutritionBasis=product.nutrition_basis,
        **_translation_fields(result),
        **_label_extraction_fields(result),
        **_ingredient_language_counts(result["ingredients"]),
        durationMs=round((time.perf_counter() - started) * 1000, 2),
    )
    return out
