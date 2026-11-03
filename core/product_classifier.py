from __future__ import annotations

"""Deterministic residential product classification for resale TMA/CMA analysis.

The classification is intentionally based on product/property type text, never on the
CRMLS ``Property Attached YN`` boolean.  That boolean is retained only as QA evidence
because it is inconsistently entered in source MLS data.
"""

from typing import Any

CLASSIFIER_VERSION = "product_subtype_v3"
STANDARD_TMA_CATEGORIES = ("Attached", "Detached Condo", "SFD")
EXCLUDED_TMA_CATEGORIES = ("Multi-Family", "Other")


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        # Avoid importing pandas in this tiny shared module.
        if value != value:  # NaN
            return ""
    except Exception:
        pass
    return " ".join(str(value).strip().casefold().replace("_", " ").split())


def classify_tma_product(
    property_subtype: Any,
    *,
    source_property_type: Any = None,
    explicit_type: Any = None,
) -> str:
    """Return the analytical TMA product category.

    The most specific product/subtype text is authoritative. ``Property Attached YN``
    is deliberately absent from this API and cannot influence classification.

    ``Detached Condo`` is used only when an actual product/type label explicitly says
    detached/site condominium. A generic ``Condominium`` remains ``Attached`` even if
    an unreliable source boolean says otherwise.

    Duplex/triplex/fourplex/multi-family records are retained in the warehouse as
    ``Multi-Family`` but are excluded from the standard Attached/Detached Condo/SFD TMA
    unless a future workflow explicitly opts into that category.
    """
    subtype = _text(property_subtype)
    ptype = _text(source_property_type)

    detached_condo_tokens = (
        "detached condominium",
        "detached condo",
        "site condominium",
        "site condo",
        "condominium detached",
        "condo detached",
    )
    multifamily_tokens = (
        "duplex", "triplex", "quadruplex", "fourplex", "4 plex", "4-plex",
        "multi family", "multi-family", "multifamily", "2-4 unit", "2 to 4 unit",
        "threeplex", "two family", "three family", "four family",
    )
    sfr_tokens = (
        "single family residence", "single-family residence", "single family detached",
        "single-family detached", "single family", "sfr",
    )

    def classify_text(text: str) -> str | None:
        if not text:
            return None
        if any(token in text for token in detached_condo_tokens):
            return "Detached Condo"
        if any(token in text for token in multifamily_tokens):
            return "Multi-Family"
        if "townhouse" in text or "townhome" in text:
            return "Attached"
        if "condominium" in text or "condo" in text:
            return "Attached"
        if any(token in text for token in sfr_tokens):
            return "SFD"
        return None

    # Subtype is normally the most specific CRMLS product field, so a recognized
    # subtype wins even if a broader/older property-type field disagrees.
    subtype_class = classify_text(subtype)
    if subtype_class is not None:
        # A broader product field may refine a generic condo into an explicitly
        # named Detached/Site Condo, but cannot otherwise override the subtype.
        if subtype_class == "Attached" and ("condo" in subtype or "condominium" in subtype):
            ptype_class = classify_text(ptype)
            if ptype_class == "Detached Condo":
                return "Detached Condo"
        return subtype_class

    ptype_class = classify_text(ptype)
    if ptype_class is not None:
        return ptype_class

    # Compatibility fallback for already-normalized analytical values when source
    # product fields are genuinely unavailable. This never overrides recognized source
    # taxonomy.
    explicit = _text(explicit_type)
    if explicit in {"attached"}:
        return "Attached"
    if explicit in {"detached condo", "detached condominium"}:
        return "Detached Condo"
    if explicit in {"sfd", "detached", "single family detached", "single-family detached"}:
        return "SFD"
    if explicit in {"multi-family", "multifamily"}:
        return "Multi-Family"

    return "Other"


def broad_property_bucket(property_subtype: Any, *, source_property_type: Any = None) -> str:
    """Return the selector-level category without using Attached Y/N."""
    subtype = _text(property_subtype)
    ptype = _text(source_property_type)
    text = subtype or ptype
    tma = classify_tma_product(property_subtype, source_property_type=source_property_type)
    if tma == "Detached Condo":
        return "Condo"
    if tma == "Attached":
        if "townhouse" in text or "townhome" in text:
            return "Townhouse"
        if "condo" in text:
            return "Condo"
        return "Attached"
    if tma == "SFD":
        return "Single Family"
    if tma == "Multi-Family":
        return "Multi-Family"
    if "manufactured" in text or "mobile home" in text:
        return "Manufactured"
    if "apartment" in text:
        return "Apartment"
    if "land" in text or "vacant lot" in text:
        return "Land"
    return str(property_subtype or source_property_type or "Other").strip() or "Other"



def selected_tma_categories(property_types: Any) -> set[str]:
    """Map analyst-facing TMA selector values to analytical product categories.

    This keeps report/export summaries aligned with the exact property-type scope
    selected for each TMA. Condo intentionally allows both ordinary Attached
    condominium product and the explicit Detached Condo category; Townhouse maps
    to Attached and Single Family maps to SFD. Unsupported selector values do not
    opt a standard TMA category into the report.
    """
    try:
        values = list(property_types or [])
    except Exception:
        values = [property_types] if property_types else []
    out: set[str] = set()
    for value in values:
        text = _text(value)
        if not text:
            continue
        if "single family" in text or text in {"sfd", "single-family"}:
            out.add("SFD")
        if "townhouse" in text or "townhome" in text or text == "attached":
            out.add("Attached")
        if "condo" in text or "condominium" in text:
            out.add("Attached")
            out.add("Detached Condo")
    return out

def attached_flag_conflict(tma_category: str, attached_yn: Any) -> dict[str, Any] | None:
    """Return QA details when the source attached boolean conflicts with product taxonomy.

    The boolean is diagnostic only and never changes the classification.
    """
    text = _text(attached_yn)
    if text in {"true", "t", "yes", "y", "1", "attached"}:
        flag = True
    elif text in {"false", "f", "no", "n", "0", "detached"}:
        flag = False
    else:
        return None

    expected = None
    if tma_category == "Attached":
        expected = True
    elif tma_category in {"SFD", "Detached Condo"}:
        expected = False
    if expected is None or flag == expected:
        return None
    return {
        "reason": "product_taxonomy_vs_attached_flag_conflict",
        "classifier_version": CLASSIFIER_VERSION,
        "tma_category": tma_category,
        "source_attached_yn": flag,
        "policy": "product_subtype_is_authoritative_attached_flag_is_qa_only",
    }
