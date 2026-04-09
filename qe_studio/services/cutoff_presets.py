"""
Pseudopotential-aware cutoff selection: quality presets and decision provenance.

Overview
--------
This module defines the three canonical quality presets used by
:func:`~qe_studio.services.input_generator.select_cutoffs` to derive
plane-wave energy cutoffs from SSSP pseudopotential metadata.

Quality Presets
---------------
+-------------+----------------+------------+----------------------------------------------+
| Preset      | ecutwfc factor | conv_thr   | Use-case                                     |
+=============+================+============+==============================================+
| quick       | 0.90×          | 1.0 × 10⁻⁶ | Fast screening; slightly under SSSP minimum. |
+-------------+----------------+------------+----------------------------------------------+
| standard    | 1.00×          | 1.0 × 10⁻⁶ | SSSP-recommended value; peer-review quality. |
+-------------+----------------+------------+----------------------------------------------+
| publication | 1.25×          | 1.0 × 10⁻⁸ | Extra convergence margin + tight SCF.        |
+-------------+----------------+------------+----------------------------------------------+

The same multiplier is applied to **both** ``ecutwfc`` and ``ecutrho``.  The
``ecutrho`` value from the SSSP manifest already encodes the dual ratio for
each pseudopotential (norm-conserving uses ~4×, ultrasoft/PAW uses ~8×), so
no additional assumption about the ratio is needed when metadata is available.

When metadata is absent the module falls back to conservative heuristic
defaults (40 Ry wavefunction / 160 Ry charge-density for *standard*; scaled
versions for the other presets) and labels the decision clearly as a heuristic.

Public API
----------
CutoffPreset
    Enum of the three quality tiers.
CutoffDecision
    Frozen dataclass capturing the complete cutoff decision with provenance.
PRESET_MULTIPLIERS
    ecutwfc/ecutrho multiplier per preset.
PRESET_CONV_THR
    Suggested SCF convergence threshold per preset.
PRESET_DESCRIPTIONS
    Human-readable one-liner per preset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# CutoffPreset
# ---------------------------------------------------------------------------


class CutoffPreset(str, Enum):
    """Quality tier for plane-wave energy cutoff selection.

    The tier controls two things:

    1. A **multiplier** applied to the SSSP-recommended ``ecutwfc`` and
       ``ecutrho`` values (see :data:`PRESET_MULTIPLIERS`).
    2. A **suggested SCF convergence threshold** (see :data:`PRESET_CONV_THR`).

    Members
    -------
    QUICK:
        Fast screening runs.  Uses 0.90× the recommended cutoffs.  Slightly
        below the SSSP convergence guarantee; acceptable for structure
        pre-screening or exploratory calculations.
    STANDARD:
        Peer-review quality.  Uses exactly the SSSP-recommended cutoffs
        (1.00× multiplier).  Suitable for production calculations that will
        be directly reported in publications.
    PUBLICATION:
        High-accuracy / convergence-study quality.  Uses 1.25× the
        recommended cutoffs and tightens :math:`\\Delta E_{\\text{SCF}}` to
        10⁻⁸ Ry.  Appropriate for phonons, elastic constants, and other
        properties that are sensitive to basis-set completeness.
    """

    QUICK = "quick"
    STANDARD = "standard"
    PUBLICATION = "publication"


# ---------------------------------------------------------------------------
# Preset parameter tables
# ---------------------------------------------------------------------------

#: ecutwfc (and ecutrho) multiplier relative to the SSSP recommended value.
#: Applied identically to both cutoffs so the metadata dual ratio is preserved.
PRESET_MULTIPLIERS: dict[CutoffPreset, float] = {
    CutoffPreset.QUICK: 0.90,
    CutoffPreset.STANDARD: 1.00,
    CutoffPreset.PUBLICATION: 1.25,
}

#: Suggested SCF energy convergence threshold (Ry) for each preset.
#: ``publication`` tightens this to 10⁻⁸ Ry to match the tighter basis.
PRESET_CONV_THR: dict[CutoffPreset, float] = {
    CutoffPreset.QUICK: 1.0e-6,
    CutoffPreset.STANDARD: 1.0e-6,
    CutoffPreset.PUBLICATION: 1.0e-8,
}

#: Human-readable one-liner describing each preset's intent.
PRESET_DESCRIPTIONS: dict[CutoffPreset, str] = {
    CutoffPreset.QUICK: (
        "Quick screening: 0.90× SSSP recommended cutoffs, "
        "conv_thr = 1e-6 Ry. "
        "Slightly below SSSP convergence guarantee."
    ),
    CutoffPreset.STANDARD: (
        "Standard quality: SSSP recommended cutoffs (1.00×), "
        "conv_thr = 1e-6 Ry. "
        "Suitable for peer-reviewed results."
    ),
    CutoffPreset.PUBLICATION: (
        "Publication quality: 1.25× SSSP recommended cutoffs, "
        "conv_thr = 1e-8 Ry. "
        "Extra convergence margin for phonons, elastic constants, etc."
    ),
}

# ---------------------------------------------------------------------------
# Fallback heuristic constants
# ---------------------------------------------------------------------------

#: Conservative heuristic wavefunction cutoff (Ry) used when SSSP metadata
#: is absent.  Chosen to be safe for most light-to-medium elements.
#: **This value is a heuristic — it is not derived from any pseudopotential.**
HEURISTIC_ECUTWFC_RY: float = 40.0

#: Conservative heuristic charge-density cutoff (Ry) used when SSSP metadata
#: is absent.  Set to 4× the heuristic wavefunction cutoff, which is the
#: minimum required by norm-conserving pseudopotentials in Quantum ESPRESSO.
#: **This value is a heuristic — it is not derived from any pseudopotential.**
HEURISTIC_ECUTRHO_RY: float = 160.0


# ---------------------------------------------------------------------------
# CutoffDecision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CutoffDecision:
    """Complete, auditable record of a cutoff selection decision.

    This dataclass captures **why** a particular ``ecutwfc`` / ``ecutrho``
    pair was chosen.  It is intended to be stored alongside the
    :class:`~qe_studio.services.input_generator.CalculationContext` so that
    every generated ``pw.in`` file can be traced back to its scientific basis.

    Parameters
    ----------
    ecutwfc_ry:
        Selected wavefunction kinetic energy cutoff in Ry.
    ecutrho_ry:
        Selected charge-density kinetic energy cutoff in Ry.
    preset:
        The :class:`CutoffPreset` tier that was applied.
    source:
        One of:

        ``"sssp_metadata"``
            Cutoffs were derived from SSSP manifest data
            (``cutoff_wfc`` / ``cutoff_rho`` fields).  This is the
            scientifically preferred path.

        ``"heuristic_fallback"``
            No usable SSSP metadata was available.  The values are
            conservative defaults **not** tied to any specific
            pseudopotential.  The ``warnings`` list will explain why.

    method:
        A concise human-readable sentence describing the selection logic.
        Intended for display in the UI and for inclusion in output metadata.
    per_element:
        Mapping of element symbol to per-element metadata used in the
        decision.  Each value is a dict containing at minimum:

        - ``"cutoff_wfc_ry"`` — element's recommended wavefunction cutoff
        - ``"cutoff_rho_ry"`` — element's recommended charge-density cutoff
        - ``"pseudopotential"`` — pseudopotential type string (e.g. ``"PAW"``)
        - ``"filename"`` — UPF filename
        - ``"limiting"`` — ``True`` if this element determined the final max

        Empty when ``source == "heuristic_fallback"``.
    warnings:
        List of non-fatal advisory messages produced during selection.
        Examples: elements with missing ``cutoff_rho``, elements whose
        cutoff data could not be parsed, etc.
    """

    ecutwfc_ry: float
    ecutrho_ry: float
    preset: CutoffPreset
    source: str  # "sssp_metadata" | "heuristic_fallback"
    method: str
    per_element: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict representation of this decision.

        Suitable for embedding in provenance records, ``resolved_map.json``,
        or UI display tables.
        """
        return {
            "ecutwfc_ry": self.ecutwfc_ry,
            "ecutrho_ry": self.ecutrho_ry,
            "preset": self.preset.value,
            "source": self.source,
            "method": self.method,
            "per_element": dict(self.per_element),
            "warnings": list(self.warnings),
        }
