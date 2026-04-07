from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from structure_sources import (
    StructureSourceResult,
    build_cubic_perovskite,
    build_ordered_double_perovskite,
    build_rocksalt,
)


FIELD_KIND_TEXT = "text"
FIELD_KIND_FLOAT = "float"


@dataclass(frozen=True)
class PrototypeFieldDefinition:
    key: str
    label: str
    input_kind: str
    default: Any
    help_text: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    step: float | None = None


@dataclass(frozen=True)
class PrototypeFamilyDefinition:
    key: str
    label: str
    source_label: str
    formula_template: str
    short_description: str
    core_fields: tuple[PrototypeFieldDefinition, ...]
    advanced_fields: tuple[PrototypeFieldDefinition, ...]
    builder: Callable[..., StructureSourceResult]

    @property
    def all_fields(self) -> tuple[PrototypeFieldDefinition, ...]:
        return self.core_fields + self.advanced_fields


ORDERED_DOUBLE_PEROVSKITE_FAMILY = PrototypeFamilyDefinition(
    key="ordered_double_perovskite_a2bbx6",
    label="Ordered Double Perovskite",
    source_label="Ordered Double Perovskite (A2BB'X6)",
    formula_template="A2BB'X6",
    short_description="Build an ordered cubic double perovskite / elpasolite source cell for `A2BB'X6`.",
    core_fields=(
        PrototypeFieldDefinition("a_site", "A site", FIELD_KIND_TEXT, "Na"),
        PrototypeFieldDefinition("x_site", "X site", FIELD_KIND_TEXT, "H"),
        PrototypeFieldDefinition("b_site", "B site", FIELD_KIND_TEXT, "Ca"),
        PrototypeFieldDefinition("b_prime_site", "B' site", FIELD_KIND_TEXT, "Cd"),
        PrototypeFieldDefinition(
            "lattice_parameter",
            "Cubic lattice parameter a (A)",
            FIELD_KIND_FLOAT,
            8.0,
            min_value=0.0,
            step=0.1,
        ),
    ),
    advanced_fields=(
        PrototypeFieldDefinition(
            "x_parameter",
            "Anion Wyckoff x parameter",
            FIELD_KIND_FLOAT,
            0.25,
            help_text="The ideal cubic double-perovskite value is 0.25.",
            min_value=0.0,
            max_value=0.5,
            step=0.01,
        ),
    ),
    builder=build_ordered_double_perovskite,
)

CUBIC_PEROVSKITE_FAMILY = PrototypeFamilyDefinition(
    key="cubic_perovskite_abx3",
    label="Cubic Perovskite",
    source_label="Cubic Perovskite (ABX3)",
    formula_template="ABX3",
    short_description="Build an ideal cubic perovskite starting structure for `ABX3`.",
    core_fields=(
        PrototypeFieldDefinition("a_site", "A site", FIELD_KIND_TEXT, "Cs"),
        PrototypeFieldDefinition("b_site", "B site", FIELD_KIND_TEXT, "Pb"),
        PrototypeFieldDefinition("x_site", "X site", FIELD_KIND_TEXT, "I"),
        PrototypeFieldDefinition(
            "lattice_parameter",
            "Cubic lattice parameter a (A)",
            FIELD_KIND_FLOAT,
            6.2,
            min_value=0.0,
            step=0.1,
        ),
    ),
    advanced_fields=(),
    builder=build_cubic_perovskite,
)

ROCKSALT_FAMILY = PrototypeFamilyDefinition(
    key="rocksalt_ab",
    label="Rocksalt",
    source_label="Rocksalt (AB)",
    formula_template="AB",
    short_description="Build a cubic rocksalt reference structure for `AB`.",
    core_fields=(
        PrototypeFieldDefinition("a_site", "A site", FIELD_KIND_TEXT, "Na"),
        PrototypeFieldDefinition("b_site", "B site", FIELD_KIND_TEXT, "Cl"),
        PrototypeFieldDefinition(
            "lattice_parameter",
            "Cubic lattice parameter a (A)",
            FIELD_KIND_FLOAT,
            5.6,
            min_value=0.0,
            step=0.1,
        ),
    ),
    advanced_fields=(),
    builder=build_rocksalt,
)

PROTOTYPE_FAMILIES: tuple[PrototypeFamilyDefinition, ...] = (
    ORDERED_DOUBLE_PEROVSKITE_FAMILY,
    CUBIC_PEROVSKITE_FAMILY,
    ROCKSALT_FAMILY,
)

DEFAULT_PROTOTYPE_FAMILY_KEY = ORDERED_DOUBLE_PEROVSKITE_FAMILY.key

_PROTOTYPE_FAMILY_MAP = {family.key: family for family in PROTOTYPE_FAMILIES}


def get_prototype_family(family_key: str) -> PrototypeFamilyDefinition:
    family = _PROTOTYPE_FAMILY_MAP.get(family_key)
    if family is None:
        raise ValueError(f"Unsupported prototype family: {family_key!r}")
    return family


def get_prototype_family_keys() -> tuple[str, ...]:
    return tuple(family.key for family in PROTOTYPE_FAMILIES)


def build_prototype_family(family_key: str, values: dict[str, Any]) -> StructureSourceResult:
    family = get_prototype_family(family_key)
    builder_kwargs: dict[str, Any] = {}
    for field in family.all_fields:
        raw_value = values.get(field.key, field.default)
        if field.input_kind == FIELD_KIND_FLOAT:
            builder_kwargs[field.key] = float(raw_value)
        else:
            builder_kwargs[field.key] = raw_value
    return family.builder(**builder_kwargs)
