"""
Tests for ``qe_studio.services.cutoff_presets`` and the
``select_cutoffs()`` function in ``qe_studio.services.input_generator``.

Test organisation
-----------------
TestCutoffPreset
    Enum values and table completeness.

TestCutoffDecision
    CutoffDecision dataclass invariants and as_dict() serialisation.

TestSelectCutoffs
    Core selection logic — metadata path, heuristic fallback, preset scaling,
    multi-element max, per-element attribution, and edge cases.

TestPresetOrdering
    Strict ordering guarantee: quick ≤ standard ≤ publication cutoffs.

TestSelectCutoffsIntegration
    Integration with make_default_context() — preset kwarg, conv_thr,
    cutoff_decision attachment, and source labelling.
"""

from __future__ import annotations

import pytest
from ase.build import bulk

from qe_studio.services.cutoff_presets import (
    HEURISTIC_ECUTRHO_RY,
    HEURISTIC_ECUTWFC_RY,
    PRESET_CONV_THR,
    PRESET_DESCRIPTIONS,
    PRESET_MULTIPLIERS,
    CutoffDecision,
    CutoffPreset,
)
from qe_studio.services.input_generator import (
    CalculationContext,
    make_default_context,
    select_cutoffs,
)


# ---------------------------------------------------------------------------
# SSSP-style manifest fixture helpers
# ---------------------------------------------------------------------------


def _sssp_entry(wfc: float, rho: float, pp: str = "PAW", filename: str = "X.upf") -> dict:
    """Return a minimal SSSP manifest entry with correct field names."""
    return {"cutoff_wfc": wfc, "cutoff_rho": rho, "pseudopotential": pp, "filename": filename}


def _sssp_entry_wfc_only(wfc: float) -> dict:
    """Return an SSSP manifest entry missing cutoff_rho."""
    return {"cutoff_wfc": wfc, "pseudopotential": "NC", "filename": "X.upf"}


def _make_bulk_si():
    return bulk("Si", "diamond", a=5.43, cubic=False)


def _make_bulk_fe():
    return bulk("Fe", "bcc", a=2.87)


# ---------------------------------------------------------------------------
# TestCutoffPreset
# ---------------------------------------------------------------------------


class TestCutoffPreset:
    def test_enum_values(self):
        assert CutoffPreset.QUICK.value == "quick"
        assert CutoffPreset.STANDARD.value == "standard"
        assert CutoffPreset.PUBLICATION.value == "publication"

    def test_all_presets_in_multipliers_table(self):
        for p in CutoffPreset:
            assert p in PRESET_MULTIPLIERS, f"Preset {p} missing from PRESET_MULTIPLIERS"

    def test_all_presets_in_conv_thr_table(self):
        for p in CutoffPreset:
            assert p in PRESET_CONV_THR, f"Preset {p} missing from PRESET_CONV_THR"

    def test_all_presets_in_descriptions_table(self):
        for p in CutoffPreset:
            assert p in PRESET_DESCRIPTIONS, f"Preset {p} missing from PRESET_DESCRIPTIONS"

    def test_multiplier_ordering(self):
        """quick ≤ standard ≤ publication multipliers."""
        assert PRESET_MULTIPLIERS[CutoffPreset.QUICK] <= PRESET_MULTIPLIERS[CutoffPreset.STANDARD]
        assert PRESET_MULTIPLIERS[CutoffPreset.STANDARD] <= PRESET_MULTIPLIERS[CutoffPreset.PUBLICATION]

    def test_standard_multiplier_is_unity(self):
        """Standard preset applies no scaling — exactly 1.00×."""
        assert PRESET_MULTIPLIERS[CutoffPreset.STANDARD] == 1.0

    def test_publication_conv_thr_tighter(self):
        """Publication preset has a stricter convergence threshold than standard."""
        assert PRESET_CONV_THR[CutoffPreset.PUBLICATION] < PRESET_CONV_THR[CutoffPreset.STANDARD]

    def test_descriptions_non_empty(self):
        for p in CutoffPreset:
            desc = PRESET_DESCRIPTIONS[p]
            assert isinstance(desc, str) and len(desc) > 0


# ---------------------------------------------------------------------------
# TestCutoffDecision
# ---------------------------------------------------------------------------


class TestCutoffDecision:
    def _make_decision(self, **kwargs) -> CutoffDecision:
        defaults = dict(
            ecutwfc_ry=40.0,
            ecutrho_ry=160.0,
            preset=CutoffPreset.STANDARD,
            source="heuristic_fallback",
            method="test method",
            per_element={},
            warnings=[],
        )
        defaults.update(kwargs)
        return CutoffDecision(**defaults)

    def test_is_frozen(self):
        decision = self._make_decision()
        with pytest.raises((AttributeError, TypeError)):
            decision.ecutwfc_ry = 999.0  # type: ignore[misc]

    def test_as_dict_contains_required_keys(self):
        decision = self._make_decision(source="sssp_metadata")
        d = decision.as_dict()
        for key in ("ecutwfc_ry", "ecutrho_ry", "preset", "source", "method", "per_element", "warnings"):
            assert key in d, f"Key {key!r} missing from as_dict()"

    def test_as_dict_preset_is_string(self):
        decision = self._make_decision(preset=CutoffPreset.QUICK)
        d = decision.as_dict()
        assert d["preset"] == "quick"
        assert isinstance(d["preset"], str)

    def test_as_dict_is_json_serialisable(self):
        import json
        decision = self._make_decision(
            per_element={"Si": {"cutoff_wfc_ry": 30.0, "limiting": True}},
            warnings=["test warning"],
        )
        # Should not raise
        serialised = json.dumps(decision.as_dict())
        reloaded = json.loads(serialised)
        assert reloaded["ecutwfc_ry"] == 40.0

    def test_ecutrho_not_less_than_ecutwfc(self):
        """Construction with ecutrho < ecutwfc is allowed (validated upstream)."""
        decision = self._make_decision(ecutwfc_ry=60.0, ecutrho_ry=480.0)
        assert decision.ecutrho_ry >= decision.ecutwfc_ry

    def test_default_per_element_and_warnings_are_empty(self):
        decision = CutoffDecision(
            ecutwfc_ry=40.0,
            ecutrho_ry=160.0,
            preset=CutoffPreset.STANDARD,
            source="heuristic_fallback",
            method="test",
        )
        assert decision.per_element == {}
        assert decision.warnings == []


# ---------------------------------------------------------------------------
# TestSelectCutoffs
# ---------------------------------------------------------------------------


class TestSelectCutoffs:

    # --- Empty / missing entries → heuristic fallback ---

    def test_empty_entries_returns_heuristic_fallback(self):
        decision = select_cutoffs({})
        assert decision.source == "heuristic_fallback"
        assert decision.ecutwfc_ry == HEURISTIC_ECUTWFC_RY
        assert decision.ecutrho_ry == HEURISTIC_ECUTRHO_RY

    def test_empty_entries_per_element_is_empty(self):
        decision = select_cutoffs({})
        assert decision.per_element == {}

    def test_empty_entries_warnings_is_empty(self):
        # No warnings for a cleanly empty dict — caller doesn't have metadata
        decision = select_cutoffs({})
        assert decision.warnings == []

    def test_all_missing_cutoff_wfc_triggers_heuristic_with_warning(self):
        entries = {"Si": {"pseudopotential": "PAW", "filename": "Si.upf"}}
        decision = select_cutoffs(entries)
        assert decision.source == "heuristic_fallback"
        assert decision.warnings  # should contain a warning

    # --- Single-element metadata path ---

    def test_single_element_reads_cutoff_wfc(self):
        entries = {"Si": _sssp_entry(wfc=30.0, rho=240.0)}
        decision = select_cutoffs(entries, preset=CutoffPreset.STANDARD)
        assert decision.source == "sssp_metadata"
        assert decision.ecutwfc_ry >= 30.0

    def test_single_element_reads_cutoff_rho_directly(self):
        """cutoff_rho is read verbatim — no 4× assumption is made."""
        entries = {"Si": _sssp_entry(wfc=30.0, rho=240.0)}
        decision = select_cutoffs(entries, preset=CutoffPreset.STANDARD)
        # ecutrho must honour the manifest value (≥ 240 Ry), not 4×30 = 120
        assert decision.ecutrho_ry >= 240.0

    def test_cutoff_rho_is_not_assumed_four_times_ecutwfc(self):
        """
        Explicitly verify the dual-ratio fix: old code assumed 4×, but SSSP
        may specify any ratio (e.g. 8× for ultrasoft).
        """
        entries = {"Fe": _sssp_entry(wfc=60.0, rho=480.0)}  # dual = 8
        decision = select_cutoffs(entries, preset=CutoffPreset.STANDARD)
        # If old 4× assumption were used, ecutrho would be ~240 Ry, not ~480
        assert decision.ecutrho_ry >= 480.0 * PRESET_MULTIPLIERS[CutoffPreset.STANDARD]

    # --- Multi-element max selection ---

    def test_multi_element_takes_max_wfc(self):
        entries = {
            "Si": _sssp_entry(wfc=30.0, rho=240.0),
            "O":  _sssp_entry(wfc=75.0, rho=600.0),
        }
        decision = select_cutoffs(entries, preset=CutoffPreset.STANDARD)
        # O dominates
        assert decision.ecutwfc_ry >= 75.0

    def test_multi_element_takes_max_rho(self):
        entries = {
            "Si": _sssp_entry(wfc=30.0, rho=240.0),
            "O":  _sssp_entry(wfc=75.0, rho=600.0),
        }
        decision = select_cutoffs(entries, preset=CutoffPreset.STANDARD)
        assert decision.ecutrho_ry >= 600.0

    def test_wfc_and_rho_can_be_limited_by_different_elements(self):
        """
        Edge case: the element with the largest wfc may not have the largest rho.
        """
        entries = {
            "A": _sssp_entry(wfc=80.0, rho=320.0),   # highest wfc, lower rho
            "B": _sssp_entry(wfc=50.0, rho=500.0),   # lower wfc, highest rho
        }
        decision = select_cutoffs(entries, preset=CutoffPreset.STANDARD)
        assert decision.ecutwfc_ry >= 80.0
        assert decision.ecutrho_ry >= 500.0

    # --- Per-element attribution ---

    def test_per_element_populated_for_metadata_path(self):
        entries = {
            "Si": _sssp_entry(wfc=30.0, rho=240.0, pp="PAW", filename="Si.UPF"),
        }
        decision = select_cutoffs(entries)
        assert "Si" in decision.per_element
        si = decision.per_element["Si"]
        assert si["cutoff_wfc_ry"] == 30.0
        assert si["cutoff_rho_ry"] == 240.0
        assert si["pseudopotential"] == "PAW"
        assert si["filename"] == "Si.UPF"

    def test_limiting_flag_set_on_max_element(self):
        entries = {
            "Si": _sssp_entry(wfc=30.0, rho=240.0),
            "O":  _sssp_entry(wfc=75.0, rho=600.0),
        }
        decision = select_cutoffs(entries)
        # O limits both wfc and rho
        assert decision.per_element["O"]["limiting"] is True

    def test_per_element_empty_for_heuristic_fallback(self):
        decision = select_cutoffs({})
        assert decision.per_element == {}

    # --- Missing cutoff_rho: warning + 4× fallback ---

    def test_missing_cutoff_rho_uses_4x_heuristic(self):
        entries = {"Si": _sssp_entry_wfc_only(wfc=30.0)}
        decision = select_cutoffs(entries)
        assert decision.ecutrho_ry >= 30.0 * 4.0
        assert any("cutoff_rho" in w for w in decision.warnings)

    # --- Invalid data ---

    def test_invalid_cutoff_wfc_string_skipped_with_warning(self):
        entries = {
            "Bad": {"cutoff_wfc": "not_a_number", "cutoff_rho": 240.0},
            "Si":  _sssp_entry(wfc=30.0, rho=240.0),
        }
        decision = select_cutoffs(entries)
        assert "Si" in decision.per_element
        assert "Bad" not in decision.per_element
        assert any("Bad" in w for w in decision.warnings)

    def test_zero_cutoff_wfc_skipped_with_warning(self):
        entries = {"X": {"cutoff_wfc": 0.0, "cutoff_rho": 0.0}}
        decision = select_cutoffs(entries)
        assert decision.source == "heuristic_fallback"
        assert decision.warnings

    def test_invalid_cutoff_rho_falls_back_to_4x(self):
        entries = {"Si": {"cutoff_wfc": 30.0, "cutoff_rho": "bad", "filename": "Si.UPF"}}
        decision = select_cutoffs(entries)
        assert decision.ecutrho_ry >= 30.0 * 4.0
        assert any("cutoff_rho" in w for w in decision.warnings)

    # --- Rounding ---

    def test_result_rounded_to_nearest_5_ry(self):
        entries = {"Si": _sssp_entry(wfc=31.0, rho=248.0)}
        decision = select_cutoffs(entries, preset=CutoffPreset.STANDARD)
        assert decision.ecutwfc_ry % 5.0 == 0.0
        assert decision.ecutrho_ry % 5.0 == 0.0

    # --- Default preset ---

    def test_default_preset_is_standard(self):
        decision = select_cutoffs({})
        assert decision.preset == CutoffPreset.STANDARD

    # --- Method string ---

    def test_method_contains_preset_name(self):
        for preset in CutoffPreset:
            decision = select_cutoffs({}, preset=preset)
            assert preset.value in decision.method

    # --- Return type ---

    def test_returns_cutoff_decision(self):
        decision = select_cutoffs({"Si": _sssp_entry(wfc=30.0, rho=240.0)})
        assert isinstance(decision, CutoffDecision)

    def test_floats_are_floats(self):
        decision = select_cutoffs({"Si": _sssp_entry(wfc=30.0, rho=240.0)})
        assert isinstance(decision.ecutwfc_ry, float)
        assert isinstance(decision.ecutrho_ry, float)


# ---------------------------------------------------------------------------
# TestPresetOrdering
# ---------------------------------------------------------------------------


class TestPresetOrdering:
    """
    Quality guarantee: the three presets must produce strictly (or non-strictly)
    ordered cutoffs.  quick ≤ standard ≤ publication for both ecutwfc and ecutrho.
    """

    def _cutoffs(self, entries: dict, preset: CutoffPreset) -> tuple[float, float]:
        d = select_cutoffs(entries, preset=preset)
        return d.ecutwfc_ry, d.ecutrho_ry

    def test_heuristic_fallback_ordering(self):
        wfc_q, rho_q = self._cutoffs({}, CutoffPreset.QUICK)
        wfc_s, rho_s = self._cutoffs({}, CutoffPreset.STANDARD)
        wfc_p, rho_p = self._cutoffs({}, CutoffPreset.PUBLICATION)
        assert wfc_q <= wfc_s <= wfc_p
        assert rho_q <= rho_s <= rho_p

    def test_metadata_driven_ordering(self):
        entries = {"Si": _sssp_entry(wfc=30.0, rho=240.0)}
        wfc_q, rho_q = self._cutoffs(entries, CutoffPreset.QUICK)
        wfc_s, rho_s = self._cutoffs(entries, CutoffPreset.STANDARD)
        wfc_p, rho_p = self._cutoffs(entries, CutoffPreset.PUBLICATION)
        assert wfc_q <= wfc_s <= wfc_p
        assert rho_q <= rho_s <= rho_p

    def test_publication_strictly_above_quick(self):
        entries = {"Si": _sssp_entry(wfc=30.0, rho=240.0)}
        wfc_q, _ = self._cutoffs(entries, CutoffPreset.QUICK)
        wfc_p, _ = self._cutoffs(entries, CutoffPreset.PUBLICATION)
        assert wfc_p > wfc_q

    def test_multispecies_ordering(self):
        entries = {
            "Si": _sssp_entry(wfc=30.0, rho=240.0),
            "O":  _sssp_entry(wfc=75.0, rho=600.0),
        }
        wfc_q, rho_q = self._cutoffs(entries, CutoffPreset.QUICK)
        wfc_s, rho_s = self._cutoffs(entries, CutoffPreset.STANDARD)
        wfc_p, rho_p = self._cutoffs(entries, CutoffPreset.PUBLICATION)
        assert wfc_q <= wfc_s <= wfc_p
        assert rho_q <= rho_s <= rho_p


# ---------------------------------------------------------------------------
# TestSelectCutoffsIntegration
# ---------------------------------------------------------------------------


class TestSelectCutoffsIntegration:
    """
    Integration tests for the preset kwarg in make_default_context() and for
    cutoff_decision being properly stored on CalculationContext.
    """

    def test_cutoff_decision_attached_to_context(self):
        si = _make_bulk_si()
        ctx = make_default_context(si)
        assert ctx.cutoff_decision is not None
        assert isinstance(ctx.cutoff_decision, CutoffDecision)

    def test_fallback_source_when_no_entries(self):
        si = _make_bulk_si()
        ctx = make_default_context(si, resolved_entries=None)
        assert ctx.cutoff_decision.source == "heuristic_fallback"

    def test_sssp_metadata_source_when_entries_provided(self):
        si = _make_bulk_si()
        entries = {"Si": _sssp_entry(wfc=30.0, rho=240.0)}
        ctx = make_default_context(si, resolved_entries=entries)
        assert ctx.cutoff_decision.source == "sssp_metadata"

    def test_standard_preset_is_default(self):
        si = _make_bulk_si()
        ctx = make_default_context(si)
        assert ctx.cutoff_decision.preset == CutoffPreset.STANDARD

    def test_quick_preset_lowers_cutoffs(self):
        si = _make_bulk_si()
        entries = {"Si": _sssp_entry(wfc=30.0, rho=240.0)}
        ctx_q = make_default_context(si, resolved_entries=entries, preset=CutoffPreset.QUICK)
        ctx_s = make_default_context(si, resolved_entries=entries, preset=CutoffPreset.STANDARD)
        assert ctx_q.numerical.ecutwfc <= ctx_s.numerical.ecutwfc
        assert ctx_q.numerical.ecutrho <= ctx_s.numerical.ecutrho

    def test_publication_preset_raises_cutoffs(self):
        si = _make_bulk_si()
        entries = {"Si": _sssp_entry(wfc=30.0, rho=240.0)}
        ctx_s = make_default_context(si, resolved_entries=entries, preset=CutoffPreset.STANDARD)
        ctx_p = make_default_context(si, resolved_entries=entries, preset=CutoffPreset.PUBLICATION)
        assert ctx_p.numerical.ecutwfc >= ctx_s.numerical.ecutwfc
        assert ctx_p.numerical.ecutrho >= ctx_s.numerical.ecutrho

    def test_publication_preset_tightens_conv_thr(self):
        si = _make_bulk_si()
        ctx = make_default_context(si, preset=CutoffPreset.PUBLICATION)
        assert ctx.numerical.conv_thr <= 1.0e-7  # publication uses 1e-8

    def test_standard_preset_conv_thr(self):
        si = _make_bulk_si()
        ctx = make_default_context(si, preset=CutoffPreset.STANDARD)
        assert ctx.numerical.conv_thr == PRESET_CONV_THR[CutoffPreset.STANDARD]

    def test_context_is_valid_for_all_presets(self):
        si = _make_bulk_si()
        entries = {"Si": _sssp_entry(wfc=30.0, rho=240.0)}
        for preset in CutoffPreset:
            ctx = make_default_context(si, resolved_entries=entries, preset=preset)
            ctx.validate()  # Should not raise

    def test_cutoff_decision_method_is_string(self):
        si = _make_bulk_si()
        ctx = make_default_context(si)
        assert isinstance(ctx.cutoff_decision.method, str)
        assert len(ctx.cutoff_decision.method) > 0

    def test_cutoff_decision_per_element_populated_with_real_entries(self):
        si = _make_bulk_si()
        entries = {"Si": _sssp_entry(wfc=30.0, rho=240.0)}
        ctx = make_default_context(si, resolved_entries=entries)
        assert "Si" in ctx.cutoff_decision.per_element

    def test_cutoff_decision_as_dict_serialisable(self):
        import json
        si = _make_bulk_si()
        entries = {"Si": _sssp_entry(wfc=30.0, rho=240.0)}
        ctx = make_default_context(si, resolved_entries=entries)
        # Should not raise
        payload = json.dumps(ctx.cutoff_decision.as_dict())
        assert len(payload) > 0

    def test_multi_element_context_decision_has_all_species(self):
        fe = _make_bulk_fe()
        # Fake entries for Fe (single-element for this test)
        entries = {"Fe": _sssp_entry(wfc=90.0, rho=720.0, pp="US")}
        ctx = make_default_context(fe, resolved_entries=entries)
        assert "Fe" in ctx.cutoff_decision.per_element

    def test_context_is_frozen(self):
        si = _make_bulk_si()
        ctx = make_default_context(si)
        with pytest.raises((AttributeError, TypeError)):
            ctx.calculation = "nscf"  # type: ignore[misc]
