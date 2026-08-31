"""
`BarcodeProductProvider` — the abstraction every external barcode data
source implements, and the DTOs its adapters return.

This layer is deliberately kept separate from the domain (`app.models`)
and from the API contract (`app.schemas`): a `ProviderProductResult` is
"whatever one external source said, after parsing its own response
shape" — not yet validated, not yet merged with other sources, and never
returned to a client as-is. `app/services/barcode_discovery.py` is the
only caller that turns these into a persisted `Product`.
"""
import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.services.barcode_validation import BarcodeInfo


class ProviderError(Exception):
    """Base class for a provider-adapter failure. Callers (the
    orchestration service) MUST catch this — and every subclass — and
    move on to the next provider rather than letting it reach the
    client. Never include response bodies, headers, or query strings
    (which may carry an API key) in the exception message."""


class ProviderTimeoutError(ProviderError):
    pass


class ProviderRateLimitedError(ProviderError):
    """The provider responded with 429 (or an equivalent
    quota-exceeded signal). Distinguished from a generic HTTP error so
    it can be logged/handled differently later without a request body."""


class ProviderHttpError(ProviderError):
    """Any non-2xx, non-429 HTTP response."""


class ProviderMalformedResponseError(ProviderError):
    """2xx response whose body isn't the JSON shape this adapter
    expects (missing/invalid fields, not valid JSON at all, etc.)."""


@dataclass(frozen=True)
class NutritionFacts:
    """Per-100g nutrition figures, matching the subset of `ProductEntity`
    the Health Score Calculator actually consumes. Any field left `None`
    means "this source did not state it" — never guessed or defaulted
    here; defaulting only happens once, deliberately, in the
    orchestration/persistence layer, and always paired with an explicit
    "incomplete data" signal to the client (see `barcode_discovery.py`)."""

    sugar_grams: float | None = None
    sodium_mg: float | None = None
    saturated_fat_grams: float | None = None
    has_artificial_sweeteners: bool | None = None
    has_preservatives: bool | None = None
    nova_group: int | None = None


@dataclass(frozen=True)
class ProviderProductResult:
    """One provider's parsed, but not-yet-validated-or-merged, answer
    for a single barcode."""

    provider: str  # matches ProviderMetadata.name below
    external_id: str | None
    product_name: str | None
    brand: str | None
    category: str | None
    image_url: str | None
    raw_ingredient_text: str | None
    ingredients_tokens: list[str] = field(default_factory=list)
    nutrition: NutritionFacts = field(default_factory=NutritionFacts)
    allergens: list[str] = field(default_factory=list)
    dietary_flags: dict[str, bool] = field(default_factory=dict)  # e.g. {"is_vegan": True}
    language: str | None = None
    source_url: str | None = None
    external_last_modified: datetime | None = None
    # Small, curated, non-secret key/value pairs worth keeping for audit
    # (e.g. OFF's own `code`, category taxonomy id). Never the raw
    # upstream response body.
    raw_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_basic_identity(self) -> bool:
        """"Basic identity data" per the required discovery flow: at
        minimum a usable product name. Brand/category alone are not
        enough to consider a barcode "found"."""
        return bool(self.product_name and self.product_name.strip())


@dataclass(frozen=True)
class ProviderMetadata:
    name: str
    base_trust: float  # 0..1, see barcode_discovery.PROVIDER_BASE_TRUST


class BarcodeProductProvider(abc.ABC):
    """One external barcode data source. Implementations must never
    raise anything except a `ProviderError` subclass — any other
    exception is a bug in the adapter, not a legitimate "provider
    failed" signal, and is treated as an isolation-boundary violation by
    the orchestration service's tests."""

    metadata: ProviderMetadata

    @abc.abstractmethod
    async def fetch(self, barcode: BarcodeInfo) -> ProviderProductResult | None:
        """Returns `None` for a confirmed "this barcode is not in this
        source" (e.g. a clean 404) — that is not an error. Raises a
        `ProviderError` subclass for anything that prevented getting a
        real answer (timeout, network error, 5xx, rate limit, malformed
        body)."""
        raise NotImplementedError
