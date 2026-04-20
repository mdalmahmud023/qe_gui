from __future__ import annotations

from typing import Any, Iterable


PREFERRED_FORMULA_INFO_KEY = "qe_studio_preferred_formula"
PREFERRED_FORMULA_SOURCE_DETAIL_KEY = "_preferred_formula"


def format_formula_sequence(parts: Iterable[tuple[str, int]]) -> str:
    """Render one formula string using the supplied element order."""
    tokens: list[str] = []
    for symbol, count in parts:
        normalized_symbol = str(symbol).strip()
        normalized_count = int(count)
        if not normalized_symbol or normalized_count <= 0:
            continue
        tokens.append(normalized_symbol)
        if normalized_count != 1:
            tokens.append(str(normalized_count))
    return "".join(tokens)


def get_preferred_formula(atoms=None, *, source_details: dict[str, Any] | None = None) -> str | None:
    """Return the preferred structure formula for display and default naming."""
    if source_details:
        preferred = str(source_details.get(PREFERRED_FORMULA_SOURCE_DETAIL_KEY) or "").strip()
        if preferred:
            return preferred
        manual_formula = str(source_details.get("formula_text") or "").strip()
        if manual_formula:
            return manual_formula

    if atoms is None:
        return None

    info = getattr(atoms, "info", None) or {}
    preferred = str(info.get(PREFERRED_FORMULA_INFO_KEY) or "").strip()
    if preferred:
        return preferred

    try:
        formula = atoms.get_chemical_formula()
    except Exception:
        return None
    normalized_formula = str(formula).strip()
    return normalized_formula or None


def attach_preferred_formula(atoms, formula: str | None) -> None:
    """Persist one preferred formula string onto an ASE Atoms object."""
    normalized_formula = str(formula or "").strip()
    if atoms is None or not normalized_formula:
        return
    atoms.info[PREFERRED_FORMULA_INFO_KEY] = normalized_formula
