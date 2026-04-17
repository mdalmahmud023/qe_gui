from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from .models import PointResult
from ..services.qe_control import sanitize_qe_prefix


def attach_successive_energy_deltas(point_results: Sequence[PointResult]) -> list[PointResult]:
    """Return point results with successive energy-per-atom deltas populated."""
    updated_results: list[PointResult] = []
    previous_success_energy: float | None = None
    for result in point_results:
        if result.status == "success" and result.energy_per_atom_ev is not None and previous_success_energy is not None:
            delta = abs(result.energy_per_atom_ev - previous_success_energy) * 1000.0
            updated_results.append(dataclasses.replace(result, delta_mev_per_atom=delta))
            previous_success_energy = result.energy_per_atom_ev
        else:
            updated_results.append(result)
            if result.status == "success" and result.energy_per_atom_ev is not None:
                previous_success_energy = result.energy_per_atom_ev
            else:
                previous_success_energy = None
    return updated_results


def deduplicate_messages(messages: Sequence[str]) -> list[str]:
    """Return messages without duplicates while preserving order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for message in messages:
        if not message or message in seen:
            continue
        seen.add(message)
        ordered.append(message)
    return ordered


def make_unique_study_dir(studies_root: Path | str, *, prefix: str, generated_at: str) -> Path:
    """Create and return a unique directory for a study run."""
    root = Path(studies_root)
    timestamp = datetime.fromisoformat(generated_at).strftime("%Y%m%dT%H%M%SZ")
    base_name = f"{sanitize_qe_prefix(prefix)}_{timestamp}"
    candidate = root / base_name
    suffix = 2
    while candidate.exists():
        candidate = root / f"{base_name}_{suffix}"
        suffix += 1
    return candidate
