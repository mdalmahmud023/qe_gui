from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


CONVERGENCE_SCHEMA_VERSION = 1
CUTOFF_STUDY_MANIFEST_FILENAME = "cutoff_study_manifest.json"
CUTOFF_STUDY_SUMMARY_FILENAME = "cutoff_study_summary.json"
CUTOFF_POINT_OUTPUT_FILENAME = "pw.out"
RATIO_RULE_CUSTOM = "custom"
CUTOFF_RATIO_RULE_OPTIONS = ("4x", "6x", "8x", "12x", RATIO_RULE_CUSTOM)
_FLOAT_TOLERANCE = 1.0e-9


class ConvergenceWorkflowType(str, Enum):
    """Supported scientific convergence workflows."""

    CUTOFF = "cutoff"


class EcutrhoMode(str, Enum):
    """How point-specific ``ecutrho`` values are derived for a cutoff sweep."""

    METADATA_DUAL = "metadata_dual"
    RATIO_RULE = "ratio_rule"
    MANUAL_ABSOLUTE = "manual_absolute"
    MANUAL_RATIO = "manual_ratio"


@dataclass(frozen=True)
class CutoffSweepSpec:
    """Scientific specification for one plane-wave cutoff convergence sweep."""

    start_ecutwfc_ry: float
    stop_ecutwfc_ry: float
    step_ry: float
    tolerance_mev_per_atom: float
    ecutrho_mode: str
    ratio_rule: str = "8x"
    custom_ratio: float | None = None
    manual_ecutrho_ry: float | None = None
    manual_ratio: float | None = None

    @property
    def normalized_ecutrho_mode(self) -> EcutrhoMode:
        return self.ecutrho_mode if isinstance(self.ecutrho_mode, EcutrhoMode) else EcutrhoMode(str(self.ecutrho_mode))

    def validate(self) -> None:
        if self.start_ecutwfc_ry <= 0:
            raise ValueError("Cutoff sweep start ecutwfc must be positive.")
        if self.stop_ecutwfc_ry <= 0:
            raise ValueError("Cutoff sweep stop ecutwfc must be positive.")
        if self.stop_ecutwfc_ry + _FLOAT_TOLERANCE < self.start_ecutwfc_ry:
            raise ValueError("Cutoff sweep stop ecutwfc must be greater than or equal to the start value.")
        if self.step_ry <= 0:
            raise ValueError("Cutoff sweep step size must be positive.")
        if self.tolerance_mev_per_atom <= 0:
            raise ValueError("Cutoff convergence tolerance must be positive.")

        mode = self.normalized_ecutrho_mode
        if self.ratio_rule not in CUTOFF_RATIO_RULE_OPTIONS:
            raise ValueError(
                "Cutoff ratio rule must be one of "
                f"{', '.join(CUTOFF_RATIO_RULE_OPTIONS)}, got {self.ratio_rule!r}."
            )
        if mode == EcutrhoMode.RATIO_RULE and self.selected_ratio_rule_value() <= 0:
            raise ValueError("The selected ecutrho ratio rule must resolve to a positive ratio.")
        if mode == EcutrhoMode.MANUAL_RATIO:
            if self.manual_ratio is None or self.manual_ratio <= 0:
                raise ValueError("Manual ecutrho ratio mode requires a positive manual ratio.")
        if mode == EcutrhoMode.MANUAL_ABSOLUTE:
            if self.manual_ecutrho_ry is None or self.manual_ecutrho_ry <= 0:
                raise ValueError("Manual absolute ecutrho mode requires a positive ecutrho value.")

        self.ecutwfc_values()

    def ecutwfc_values(self) -> list[float]:
        """Return the sweep grid and require that the last point lands on ``stop`` exactly."""
        values: list[float] = []
        step_index = 0
        while True:
            candidate = self.start_ecutwfc_ry + step_index * self.step_ry
            if candidate > self.stop_ecutwfc_ry + _FLOAT_TOLERANCE:
                break
            if abs(candidate - self.stop_ecutwfc_ry) <= _FLOAT_TOLERANCE:
                values.append(float(self.stop_ecutwfc_ry))
                break
            values.append(float(round(candidate, 10)))
            step_index += 1
            if step_index > 10000:
                raise ValueError("Cutoff sweep produced too many points; check the sweep bounds.")

        if not values or abs(values[-1] - self.stop_ecutwfc_ry) > _FLOAT_TOLERANCE:
            raise ValueError("Cutoff sweep must land exactly on the stop ecutwfc value.")
        return values

    def selected_ratio_rule_value(self) -> float:
        """Return the numeric ratio implied by the ratio-rule controls."""
        if self.ratio_rule == RATIO_RULE_CUSTOM:
            if self.custom_ratio is None:
                raise ValueError("Custom cutoff ratio selected without a custom ratio value.")
            return float(self.custom_ratio)
        return float(str(self.ratio_rule).rstrip("xX"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "start_ecutwfc_ry": self.start_ecutwfc_ry,
            "stop_ecutwfc_ry": self.stop_ecutwfc_ry,
            "step_ry": self.step_ry,
            "tolerance_mev_per_atom": self.tolerance_mev_per_atom,
            "ecutrho_mode": self.normalized_ecutrho_mode.value,
            "ratio_rule": self.ratio_rule,
            "custom_ratio": self.custom_ratio,
            "manual_ecutrho_ry": self.manual_ecutrho_ry,
            "manual_ratio": self.manual_ratio,
            "ecutwfc_values_ry": self.ecutwfc_values(),
        }


@dataclass(frozen=True)
class CutoffStudyPoint:
    """One generated input point inside a cutoff convergence study."""

    index: int
    ecutwfc_ry: float
    ecutrho_ry: float
    run_dir: Path
    input_paths: dict[str, Path] = field(default_factory=dict)
    metadata_path: Path | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "ecutwfc_ry": self.ecutwfc_ry,
            "ecutrho_ry": self.ecutrho_ry,
            "run_dir": str(self.run_dir),
            "input_paths": {key: str(value) for key, value in self.input_paths.items()},
            "metadata_path": str(self.metadata_path) if self.metadata_path is not None else None,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CutoffStudyPoint":
        return cls(
            index=int(payload["index"]),
            ecutwfc_ry=float(payload["ecutwfc_ry"]),
            ecutrho_ry=float(payload["ecutrho_ry"]),
            run_dir=Path(payload["run_dir"]),
            input_paths={key: Path(value) for key, value in dict(payload.get("input_paths", {})).items()},
            metadata_path=Path(payload["metadata_path"]) if payload.get("metadata_path") else None,
        )


@dataclass(frozen=True)
class PointResult:
    """Parsed scientific result for one cutoff-study point."""

    status: str
    total_energy_ry: float | None = None
    energy_per_atom_ev: float | None = None
    delta_mev_per_atom: float | None = None
    warnings: tuple[str, ...] = ()
    output_path: Path | None = None
    job_done: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "total_energy_ry": self.total_energy_ry,
            "energy_per_atom_ev": self.energy_per_atom_ev,
            "delta_mev_per_atom": self.delta_mev_per_atom,
            "warnings": list(self.warnings),
            "output_path": str(self.output_path) if self.output_path is not None else None,
            "job_done": self.job_done,
        }


@dataclass(frozen=True)
class CutoffRecommendation:
    """Recommendation payload for one fully or partially parsed cutoff study."""

    status: str
    recommended_ecutwfc_ry: float | None = None
    recommended_ecutrho_ry: float | None = None
    message: str = ""
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "recommended_ecutwfc_ry": self.recommended_ecutwfc_ry,
            "recommended_ecutrho_ry": self.recommended_ecutrho_ry,
            "message": self.message,
            "warnings": list(self.warnings),
        }
