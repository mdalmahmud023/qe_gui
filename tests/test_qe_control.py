import unittest
from pathlib import Path

from ase import Atoms

from qe_studio.services import qe_control
from qe_studio.services.structure_formula import attach_preferred_formula


class QEControlTests(unittest.TestCase):
    def test_default_qe_prefix_uses_sanitized_formula(self) -> None:
        atoms = Atoms("Fe2O3", positions=[[0, 0, 0]] * 5, cell=[5, 5, 5], pbc=True)

        self.assertEqual(qe_control.default_qe_prefix(atoms), "fe2o3")

    def test_default_qe_prefix_prefers_attached_display_formula(self) -> None:
        atoms = Atoms("CaCdF6Na2", positions=[[0, 0, 0]] * 10, cell=[5, 5, 5], pbc=True)
        attach_preferred_formula(atoms, "Na2CaCdF6")

        self.assertEqual(qe_control.default_qe_prefix(atoms), "na2cacdf6")

    def test_sanitize_qe_prefix_truncates_to_max_length(self) -> None:
        raw = "Project " + ("FeO_" * 40)

        sanitized = qe_control.sanitize_qe_prefix(raw, max_length=64)

        self.assertLessEqual(len(sanitized), 64)
        self.assertNotIn(" ", sanitized)
        self.assertEqual(sanitized, sanitized.lower())

    def test_normalize_qe_outdir_defaults_to_tmp(self) -> None:
        self.assertEqual(qe_control.normalize_qe_outdir(None), "./tmp")
        self.assertEqual(qe_control.normalize_qe_outdir(""), "./tmp")

    def test_resolve_qe_outdir_uses_run_directory_for_relative_paths(self) -> None:
        run_dir = Path("/tmp/example")

        self.assertEqual(qe_control.resolve_qe_outdir(run_dir, "./tmp"), run_dir / "tmp")
        self.assertEqual(qe_control.resolve_qe_outdir(run_dir, "scratch"), run_dir / "scratch")

    def test_parse_qe_control_from_input_reads_prefix_and_outdir(self) -> None:
        parsed = qe_control.parse_qe_control_from_input(
            "&CONTROL\n"
            "  prefix = 'feo'\n"
            "  outdir = './tmp'\n"
            "/\n"
        )

        self.assertEqual(parsed["prefix"], "feo")
        self.assertEqual(parsed["outdir"], "./tmp")


if __name__ == "__main__":
    unittest.main()
