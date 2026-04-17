import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ase import Atoms
import streamlit as st

from qe_studio.convergence import (
    CutoffStudyPoint,
    CutoffSweepSpec,
    EcutrhoMode,
    build_cutoff_study,
    build_cutoff_sweep_warnings,
    inspect_metadata_dual_support,
    load_cutoff_study_manifest,
    refresh_cutoff_study,
    recommend_cutoffs,
    resolve_point_ecutrho,
)
from qe_studio.convergence.result_parser import parse_cutoff_point_output
from qe_studio.pages import ui_setup
from qe_studio.services.input_generator import make_default_context


def _silicon_atoms() -> Atoms:
    return Atoms(
        "Si2",
        scaled_positions=[[0.0, 0.0, 0.0], [0.25, 0.25, 0.25]],
        cell=[[0.0, 2.715, 2.715], [2.715, 0.0, 2.715], [2.715, 2.715, 0.0]],
        pbc=True,
    )


def _resolved_entries() -> dict[str, dict[str, object]]:
    return {
        "Si": {
            "filename": "Si.UPF",
            "md5": "si-md5",
            "pseudopotential": "PAW",
            "cutoff_wfc": 40.0,
            "cutoff_rho": 320.0,
        }
    }


def _write_successful_pw_output(path: Path, total_energy_ry: float) -> None:
    path.write_text(
        "\n".join(
            [
                "Self-consistent Calculation",
                f"!    total energy              =   {total_energy_ry:.10f} Ry",
                "     estimated scf accuracy    <       1.0E-10 Ry",
                "JOB DONE.",
                "",
            ]
        ),
        encoding="utf-8",
    )


class CutoffSweepSpecTests(unittest.TestCase):
    def test_ecutwfc_values_require_exact_stop_alignment(self) -> None:
        spec = CutoffSweepSpec(
            start_ecutwfc_ry=30.0,
            stop_ecutwfc_ry=55.0,
            step_ry=10.0,
            tolerance_mev_per_atom=1.0,
            ecutrho_mode=EcutrhoMode.RATIO_RULE.value,
            ratio_rule="8x",
        )

        with self.assertRaisesRegex(ValueError, "land exactly on the stop"):
            spec.ecutwfc_values()

    def test_resolve_point_ecutrho_supports_all_modes(self) -> None:
        metadata_spec = CutoffSweepSpec(
            start_ecutwfc_ry=40.0,
            stop_ecutwfc_ry=60.0,
            step_ry=10.0,
            tolerance_mev_per_atom=1.0,
            ecutrho_mode=EcutrhoMode.METADATA_DUAL.value,
            ratio_rule="8x",
        )
        ratio_spec = CutoffSweepSpec(
            start_ecutwfc_ry=40.0,
            stop_ecutwfc_ry=60.0,
            step_ry=10.0,
            tolerance_mev_per_atom=1.0,
            ecutrho_mode=EcutrhoMode.RATIO_RULE.value,
            ratio_rule="6x",
        )
        absolute_spec = CutoffSweepSpec(
            start_ecutwfc_ry=40.0,
            stop_ecutwfc_ry=60.0,
            step_ry=10.0,
            tolerance_mev_per_atom=1.0,
            ecutrho_mode=EcutrhoMode.MANUAL_ABSOLUTE.value,
            ratio_rule="8x",
            manual_ecutrho_ry=500.0,
        )
        manual_ratio_spec = CutoffSweepSpec(
            start_ecutwfc_ry=40.0,
            stop_ecutwfc_ry=60.0,
            step_ry=10.0,
            tolerance_mev_per_atom=1.0,
            ecutrho_mode=EcutrhoMode.MANUAL_RATIO.value,
            ratio_rule="8x",
            manual_ratio=9.0,
        )

        self.assertEqual(resolve_point_ecutrho(metadata_spec, ecutwfc_ry=50.0, resolved_entries=_resolved_entries()), 400.0)
        self.assertEqual(resolve_point_ecutrho(ratio_spec, ecutwfc_ry=50.0, resolved_entries=_resolved_entries()), 300.0)
        self.assertEqual(resolve_point_ecutrho(absolute_spec, ecutwfc_ry=50.0, resolved_entries=_resolved_entries()), 500.0)
        self.assertEqual(resolve_point_ecutrho(manual_ratio_spec, ecutwfc_ry=50.0, resolved_entries=_resolved_entries()), 450.0)

    def test_narrow_sweep_and_incomplete_metadata_emit_warnings(self) -> None:
        metadata_info = inspect_metadata_dual_support(
            {
                "Si": {"cutoff_wfc": 40.0, "cutoff_rho": 320.0},
                "O": {"cutoff_wfc": 50.0},
            }
        )
        warnings = build_cutoff_sweep_warnings(
            CutoffSweepSpec(
                start_ecutwfc_ry=40.0,
                stop_ecutwfc_ry=50.0,
                step_ry=10.0,
                tolerance_mev_per_atom=1.0,
                ecutrho_mode=EcutrhoMode.RATIO_RULE.value,
                ratio_rule="8x",
            ),
            point_count=2,
            metadata_dual_info=metadata_info,
        )

        self.assertTrue(any("fewer than four" in warning for warning in warnings))
        self.assertTrue(any("Fewer than three" in warning for warning in warnings))
        self.assertTrue(any("incomplete" in warning.lower() for warning in warnings))


class CutoffPointParserTests(unittest.TestCase):
    def test_parser_uses_last_total_energy_and_requires_job_done(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "pw.out"
            output_path.write_text(
                "\n".join(
                    [
                        "!    total energy              =   -10.0000000000 Ry",
                        "!    total energy              =   -10.2500000000 Ry",
                        "JOB DONE.",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            result = parse_cutoff_point_output(tmp_dir, atom_count=2)

        self.assertEqual(result.status, "success")
        self.assertAlmostEqual(result.total_energy_ry or 0.0, -10.25, places=6)
        self.assertTrue(result.job_done)

    def test_parser_reports_missing_output(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            result = parse_cutoff_point_output(tmp_dir, atom_count=2)

        self.assertEqual(result.status, "missing_output")

    def test_parser_reports_missing_job_done(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "pw.out"
            output_path.write_text("!    total energy              =   -10.0000000000 Ry\n", encoding="utf-8")

            result = parse_cutoff_point_output(tmp_dir, atom_count=2)

        self.assertEqual(result.status, "missing_job_done")

    def test_parser_reports_missing_total_energy(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "pw.out"
            output_path.write_text("JOB DONE.\n", encoding="utf-8")

            result = parse_cutoff_point_output(tmp_dir, atom_count=2)

        self.assertEqual(result.status, "missing_total_energy")

    def test_parser_treats_malformed_energy_as_missing_total_energy(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "pw.out"
            output_path.write_text(
                "!    total energy              =   not-a-number Ry\nJOB DONE.\n",
                encoding="utf-8",
            )

            result = parse_cutoff_point_output(tmp_dir, atom_count=2)

        self.assertEqual(result.status, "missing_total_energy")


class CutoffRecommendationTests(unittest.TestCase):
    def test_recommendation_chooses_first_point_within_tolerance_of_highest_tested_cutoff(self) -> None:
        study_points = [
            CutoffStudyPoint(index=1, ecutwfc_ry=40.0, ecutrho_ry=320.0, run_dir=Path("p1")),
            CutoffStudyPoint(index=2, ecutwfc_ry=50.0, ecutrho_ry=400.0, run_dir=Path("p2")),
            CutoffStudyPoint(index=3, ecutwfc_ry=60.0, ecutrho_ry=480.0, run_dir=Path("p3")),
            CutoffStudyPoint(index=4, ecutwfc_ry=70.0, ecutrho_ry=560.0, run_dir=Path("p4")),
        ]
        point_results = [
            parse_result(-10.0000),
            parse_result(-10.00020),
            parse_result(-10.00024),
            parse_result(-10.00026),
        ]

        recommendation = recommend_cutoffs(study_points, point_results, tolerance_mev_per_atom=1.0)

        self.assertEqual(recommendation.status, "complete")
        self.assertEqual(recommendation.recommended_ecutwfc_ry, 50.0)
        self.assertEqual(recommendation.recommended_ecutrho_ry, 400.0)
        self.assertIn("Recommended production cutoff", recommendation.message)

    def test_recommendation_accepts_first_earlier_point_that_matches_highest_tested_cutoff(self) -> None:
        study_points = [
            CutoffStudyPoint(index=1, ecutwfc_ry=40.0, ecutrho_ry=320.0, run_dir=Path("p1")),
            CutoffStudyPoint(index=2, ecutwfc_ry=50.0, ecutrho_ry=400.0, run_dir=Path("p2")),
            CutoffStudyPoint(index=3, ecutwfc_ry=60.0, ecutrho_ry=480.0, run_dir=Path("p3")),
        ]
        point_results = [
            parse_result(-10.0000),
            parse_result(-10.0020),
            parse_result(-10.0025),
        ]

        recommendation = recommend_cutoffs(study_points, point_results, tolerance_mev_per_atom=1.0)

        self.assertEqual(recommendation.status, "complete")
        self.assertEqual(recommendation.recommended_ecutwfc_ry, 60.0)
        self.assertEqual(recommendation.recommended_ecutrho_ry, 480.0)

    def test_recommendation_matches_highest_tested_cutoff_reference_rule(self) -> None:
        study_points = [
            CutoffStudyPoint(index=1, ecutwfc_ry=40.0, ecutrho_ry=320.0, run_dir=Path("p1")),
            CutoffStudyPoint(index=2, ecutwfc_ry=50.0, ecutrho_ry=400.0, run_dir=Path("p2")),
            CutoffStudyPoint(index=3, ecutwfc_ry=60.0, ecutrho_ry=480.0, run_dir=Path("p3")),
            CutoffStudyPoint(index=4, ecutwfc_ry=70.0, ecutrho_ry=560.0, run_dir=Path("p4")),
            CutoffStudyPoint(index=5, ecutwfc_ry=80.0, ecutrho_ry=640.0, run_dir=Path("p5")),
        ]
        point_results = [
            parse_cutoff_point_output_result("success", total_energy_ry=-393.69833652, energy_per_atom_ev=-535.65387497),
            parse_cutoff_point_output_result("success", total_energy_ry=-393.70539628, energy_per_atom_ev=-535.66348027),
            parse_cutoff_point_output_result("success", total_energy_ry=-393.70788457, energy_per_atom_ev=-535.66686576),
            parse_cutoff_point_output_result("success", total_energy_ry=-393.70823082, energy_per_atom_ev=-535.66733685),
            parse_cutoff_point_output_result("success", total_energy_ry=-393.70878931, energy_per_atom_ev=-535.66809672),
        ]

        recommendation = recommend_cutoffs(study_points, point_results, tolerance_mev_per_atom=1.0)

        self.assertEqual(recommendation.status, "complete")
        self.assertEqual(recommendation.recommended_ecutwfc_ry, 70.0)
        self.assertEqual(recommendation.recommended_ecutrho_ry, 560.0)

    def test_recommendation_marks_missing_point_as_incomplete(self) -> None:
        study_points = [
            CutoffStudyPoint(index=1, ecutwfc_ry=40.0, ecutrho_ry=320.0, run_dir=Path("p1")),
            CutoffStudyPoint(index=2, ecutwfc_ry=50.0, ecutrho_ry=400.0, run_dir=Path("p2")),
            CutoffStudyPoint(index=3, ecutwfc_ry=60.0, ecutrho_ry=480.0, run_dir=Path("p3")),
        ]
        point_results = [
            parse_result(-10.0000),
            parse_result(-10.0005),
            parse_result(None, status="missing_output"),
        ]

        recommendation = recommend_cutoffs(study_points, point_results, tolerance_mev_per_atom=1.0)

        self.assertEqual(recommendation.status, "incomplete")

    def test_recommendation_falls_back_to_highest_tested_cutoff_when_needed(self) -> None:
        study_points = [
            CutoffStudyPoint(index=1, ecutwfc_ry=40.0, ecutrho_ry=320.0, run_dir=Path("p1")),
            CutoffStudyPoint(index=2, ecutwfc_ry=50.0, ecutrho_ry=400.0, run_dir=Path("p2")),
            CutoffStudyPoint(index=3, ecutwfc_ry=60.0, ecutrho_ry=480.0, run_dir=Path("p3")),
            CutoffStudyPoint(index=4, ecutwfc_ry=70.0, ecutrho_ry=560.0, run_dir=Path("p4")),
        ]
        point_results = [
            parse_result(-10.0000),
            parse_result(-10.0030),
            parse_result(-10.0060),
            parse_result(-10.0090),
        ]

        recommendation = recommend_cutoffs(study_points, point_results, tolerance_mev_per_atom=1.0)

        self.assertEqual(recommendation.status, "complete")
        self.assertEqual(recommendation.recommended_ecutwfc_ry, 70.0)
        self.assertEqual(recommendation.recommended_ecutrho_ry, 560.0)


class CutoffStudyIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        st.session_state.clear()

    def tearDown(self) -> None:
        st.session_state.clear()

    def test_build_and_refresh_cutoff_study_writes_manifest_summary_and_point_metadata(self) -> None:
        atoms = _silicon_atoms()
        base_ctx = make_default_context(atoms, resolved_entries=_resolved_entries())
        spec = CutoffSweepSpec(
            start_ecutwfc_ry=40.0,
            stop_ecutwfc_ry=60.0,
            step_ry=10.0,
            tolerance_mev_per_atom=1.0,
            ecutrho_mode=EcutrhoMode.METADATA_DUAL.value,
            ratio_rule="8x",
        )

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            study_result = build_cutoff_study(
                atoms,
                base_ctx,
                {"Si": "Si.UPF"},
                tmp_path / "pseudos",
                sweep_spec=spec,
                pseudo_settings={
                    "functional": "PBE",
                    "mode": "efficiency",
                    "version": "1.3.0",
                    "fully_relativistic": False,
                    "source": "sssp",
                },
                source_metadata={"source_kind": "manual", "source_label": "Si"},
                structure_signature={"hash": "si-study"},
                structure_mode="source",
                studies_root=tmp_path / "convergence",
            )

            study_dir = Path(study_result["study_dir"])
            manifest = load_cutoff_study_manifest(study_dir)
            initial_summary = json.loads((study_dir / "cutoff_study_summary.json").read_text(encoding="utf-8"))

            self.assertEqual(manifest["workflow_type"], "cutoff")
            self.assertEqual(len(manifest["points"]), 3)
            self.assertEqual(initial_summary["study_status"], "incomplete")

            point_dirs = [Path(point["run_dir"]) for point in manifest["points"]]
            for point_dir, total_energy in zip(point_dirs, (-10.0000, -10.00004, -10.00006), strict=False):
                self.assertTrue((point_dir / "pw.in").is_file())
                self.assertTrue((point_dir / "espresso.pwi").is_file())
                self.assertTrue((point_dir / "qe_input_metadata.json").is_file())
                _write_successful_pw_output(point_dir / "pw.out", total_energy)

            refreshed_summary = refresh_cutoff_study(study_dir)

            self.assertEqual(refreshed_summary["study_status"], "complete")
            self.assertEqual(refreshed_summary["recommendation"]["recommended_ecutwfc_ry"], 40.0)
            self.assertEqual(refreshed_summary["recommendation"]["recommended_ecutrho_ry"], 320.0)
            self.assertIn("Recommended production cutoff", refreshed_summary["recommendation"]["message"])

    def test_standard_generation_still_works_with_cutoff_workflow_code_present(self) -> None:
        atoms = _silicon_atoms()

        with TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir)
            pseudo_dir = run_dir / "pseudos"
            pseudo_dir.mkdir()

            result = ui_setup._generate_and_persist_qe_input(
                atoms,
                {
                    "calculation": "scf",
                    "prefix": "si",
                    "outdir": "./tmp",
                    "ecutwfc": 40.0,
                    "ecutrho": 320.0,
                    "occupations": "fixed",
                    "smearing": "gaussian",
                    "degauss": 0.0,
                    "k_points_grid": [4, 4, 4],
                    "conv_thr": 1.0e-6,
                    "mixing_beta": 0.7,
                    "nspin": 1,
                    "starting_magnetization": {},
                    "noncolin": False,
                    "lspinorb": False,
                    "dftu_enabled": False,
                    "hubbard_u": {},
                    "vdw_method": ui_setup.VdwMethod.NONE.value,
                },
                {"Si": "Si.UPF"},
                str(pseudo_dir),
                run_dir=run_dir,
                current_pseudo_settings={
                    "functional": "PBE",
                    "mode": "efficiency",
                    "version": "1.3.0",
                    "fully_relativistic": False,
                },
                resolved_entries=_resolved_entries(),
                source_metadata={"source_kind": "manual"},
                structure_signature={"hash": "si-standard"},
            )

            self.assertTrue(result.success)
            self.assertTrue((run_dir / "pw.in").is_file())
            self.assertTrue((run_dir / "qe_input_metadata.json").is_file())


def parse_result(total_energy_ry: float | None, *, status: str = "success"):
    """Build a simple recommendation test payload using the production parser model."""
    if status != "success":
        return parse_cutoff_point_output_result(status)
    assert total_energy_ry is not None
    atom_count = 2
    energy_per_atom_ev = (total_energy_ry * 13.605693122994) / atom_count
    return parse_cutoff_point_output_result(
        "success",
        total_energy_ry=total_energy_ry,
        energy_per_atom_ev=energy_per_atom_ev,
    )


def parse_cutoff_point_output_result(
    status: str,
    *,
    total_energy_ry: float | None = None,
    energy_per_atom_ev: float | None = None,
):
    from qe_studio.convergence.models import PointResult

    return PointResult(
        status=status,
        total_energy_ry=total_energy_ry,
        energy_per_atom_ev=energy_per_atom_ev,
        delta_mev_per_atom=None,
        warnings=(),
        output_path=None,
        job_done=(status == "success"),
    )


if __name__ == "__main__":
    unittest.main()
