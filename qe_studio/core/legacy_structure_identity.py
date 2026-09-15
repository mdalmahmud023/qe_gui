"""Legacy geometry comparison, kept stable for existing provenance."""
import hashlib
import json
from typing import Any
from ..services.structure_formula import get_preferred_formula

STRUCTURE_SIGNATURE_VERSION = 1
STRUCTURE_SIGNATURE_DIGITS = 8
STRUCTURE_SIGNATURE_TOLERANCE = 1.0e-8

def get_unique_elements(atoms) -> list[str]:
    """Return unique chemical symbols in deterministic sorted order."""
    return sorted(set(atoms.get_chemical_symbols()))


def _normalize_decimal(value: float) -> float:
    normalized = float(value)
    if abs(normalized) < STRUCTURE_SIGNATURE_TOLERANCE:
        normalized = 0.0
    return round(normalized, STRUCTURE_SIGNATURE_DIGITS)


def _normalize_fractional_coordinate(value: float) -> float:
    normalized = float(value) % 1.0
    if abs(normalized) < STRUCTURE_SIGNATURE_TOLERANCE or abs(normalized - 1.0) < STRUCTURE_SIGNATURE_TOLERANCE:
        normalized = 0.0
    return round(normalized, STRUCTURE_SIGNATURE_DIGITS)


def _build_structure_signature_payload(atoms) -> dict[str, Any]:
    cell_matrix = [
        [_normalize_decimal(component) for component in row]
        for row in atoms.cell.array.tolist()
    ]
    wrapped_scaled_positions = atoms.get_scaled_positions(wrap=True)
    site_rows = [
        [
            symbol,
            _normalize_fractional_coordinate(position[0]),
            _normalize_fractional_coordinate(position[1]),
            _normalize_fractional_coordinate(position[2]),
        ]
        for symbol, position in zip(atoms.get_chemical_symbols(), wrapped_scaled_positions, strict=False)
    ]
    site_rows.sort(key=lambda row: (row[0], row[1], row[2], row[3]))
    return {
        "version": STRUCTURE_SIGNATURE_VERSION,
        "atom_count": len(atoms),
        "cell": cell_matrix,
        "sites": site_rows,
    }


def get_structure_signature(atoms) -> dict[str, Any]:
    """Return a deterministic structure signature for provenance and invalidation."""
    payload = _build_structure_signature_payload(atoms)
    serialized_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return {
        "version": STRUCTURE_SIGNATURE_VERSION,
        "hash": hashlib.sha256(serialized_payload.encode("utf-8")).hexdigest(),
        "elements": get_unique_elements(atoms),
        "formula": get_preferred_formula(atoms),
        "atom_count": len(atoms),
    }


def structure_signatures_match(
    first_signature: dict[str, Any] | None,
    second_signature: dict[str, Any] | None,
) -> bool:
    """Return whether two structure signatures represent the same active structure."""
    if not first_signature or not second_signature:
        return False
    return (
        int(first_signature.get("version", -1)) == int(second_signature.get("version", -2))
        and str(first_signature.get("hash", "")) == str(second_signature.get("hash", "__missing__"))
    )


def has_same_element_set(
    first_signature: dict[str, Any] | None,
    second_signature: dict[str, Any] | None,
) -> bool:
    """Return whether two structure signatures contain the same element set."""
    if not first_signature or not second_signature:
        return False
    return list(first_signature.get("elements", [])) == list(second_signature.get("elements", []))


