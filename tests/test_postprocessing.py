import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ase import Atoms

from qe_studio.services import electronic_properties as ep
from qe_studio.services import postprocessing as pp
from qe_studio.services.input_generator import VdwMethod, build_calculation_context_from_request
from qe_studio.services.reproducibility import build_input_reproducibility_metadata

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class PostprocessingTests(unittest.TestCase):
    def test_resolve_qe_run_context_prefers_pw_in_and_defaults_prefix_outdir(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir)
            (run_dir / "pw.in").write_text(
                "&CONTROL\n"
                "   calculation = 'relax'\n"
                "/\n"
                "K_POINTS automatic\n"
                "4 4 4 0 0 0\n",
                encoding="utf-8",
            )
            (run_dir / "espresso.pwi").write_text(
                "&CONTROL\n"
                "   calculation = 'scf'\n"
                "   prefix = 'from_internal'\n"
                "/\n",
                encoding="utf-8",
            )

            context = pp.resolve_qe_run_context(run_dir)

            self.assertEqual(context.source_input_file, run_dir / "pw.in")
            self.assertEqual(context.calculation, "relax")
            self.assertEqual(context.prefix, pp.DEFAULT_PREFIX)
            self.assertEqual(context.resolved_outdir, run_dir / "tmp")
            self.assertEqual(context.effective_outdir, pp.DEFAULT_OUTDIR_VALUE)

    def test_find_qe_save_dir_uses_explicit_outdir(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir)
            outdir = run_dir / "scratch"
            save_dir = outdir / "sample.save"
            save_dir.mkdir(parents=True)

            resolved = pp.find_qe_save_dir(run_dir, "sample", "scratch")

            self.assertEqual(resolved, save_dir)

    def test_validate_dos_prerequisites_warns_for_non_nscf_source(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir)
            (run_dir / "pw.in").write_text(
                "&CONTROL\n"
                "   calculation = 'scf'\n"
                "   prefix = 'qe_studio'\n"
                "   outdir = './tmp'\n"
                "/\n"
                "K_POINTS automatic\n"
                "4 4 4 0 0 0\n",
                encoding="utf-8",
            )
            save_dir = run_dir / "tmp" / "qe_studio.save"
            save_dir.mkdir(parents=True)
            (save_dir / "data-file-schema.xml").write_text("<root />", encoding="utf-8")

            check = pp.validate_dos_prerequisites(pp.resolve_qe_run_context(run_dir))

            self.assertTrue(check.ok)
            self.assertTrue(any("not `nscf`" in warning for warning in check.warnings))

    def test_validate_bands_prerequisites_blocks_non_scf_and_bad_path(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir)
            (run_dir / "pw.in").write_text(
                "&CONTROL\n"
                "   calculation = 'relax'\n"
                "   prefix = 'qe_studio'\n"
                "   outdir = './tmp'\n"
                "/\n",
                encoding="utf-8",
            )
            save_dir = run_dir / "tmp" / "qe_studio.save"
            save_dir.mkdir(parents=True)

            check = pp.validate_bands_prerequisites(
                pp.resolve_qe_run_context(run_dir),
                nbnd=8,
                band_path_text="0.0 0.0 0.0 ! G\n",
            )

            self.assertFalse(check.ok)
            self.assertTrue(any("SCF source" in blocker for blocker in check.blockers))
            self.assertTrue(any("exactly four numeric fields" in blocker for blocker in check.blockers))

    def test_validate_bands_prerequisites_warns_and_auto_adjusts_low_nbnd(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir)
            (run_dir / "pw.in").write_text(
                "&CONTROL\n"
                "   calculation = 'scf'\n"
                "   prefix = 'qe_studio'\n"
                "   outdir = './tmp'\n"
                "/\n",
                encoding="utf-8",
            )
            save_dir = run_dir / "tmp" / "qe_studio.save"
            save_dir.mkdir(parents=True)
            (save_dir / "data-file-schema.xml").write_text(
                (FIXTURES_DIR / "qe_trimmed_al001_pbc.xml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )

            context = pp.resolve_qe_run_context(run_dir)
            check = pp.validate_bands_prerequisites(
                context,
                nbnd=4,
                band_path_text="0.0 0.0 0.0 20 ! G\n0.5 0.0 0.0 1 ! X\n",
            )

            self.assertTrue(check.ok)
            self.assertTrue(any("will use `10` instead" in warning for warning in check.warnings))
            self.assertEqual(pp.get_effective_band_nbnd(context, 4), 10)

    def test_resolve_qe_run_context_detects_restart_wavefunctions_and_band_estimate(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir)
            (run_dir / "pw.in").write_text(
                "&CONTROL\n   calculation = 'scf'\n   prefix = 'qe_studio'\n   outdir = './tmp'\n/\n",
                encoding="utf-8",
            )
            save_dir = run_dir / "tmp" / "qe_studio.save"
            save_dir.mkdir(parents=True)
            (save_dir / "wfc1").write_text("binary-ish", encoding="utf-8")
            (save_dir / "data-file-schema.xml").write_text(
                (FIXTURES_DIR / "qe_trimmed_al001_pbc.xml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )

            context = pp.resolve_qe_run_context(run_dir)

            self.assertTrue(context.has_restart_wavefunctions)
            self.assertIsNotNone(context.band_count_estimate)
            assert context.band_count_estimate is not None
            self.assertEqual(context.band_count_estimate.minimum_nbnd, 6)
            self.assertEqual(context.band_count_estimate.recommended_nbnd, 10)

    def test_sanitize_leaf_filename_rejects_nested_paths(self) -> None:
        with self.assertRaises(ValueError):
            pp.sanitize_leaf_filename("nested/dos.dat", default_name="dos.dat")

    def test_build_dos_input_text_omits_smearing_fields_for_tetrahedra(self) -> None:
        text = pp.build_dos_input_text(
            prefix="pwscf",
            outdir=".",
            fildos="dos.dat",
            emin_ev=-8.0,
            emax_ev=8.0,
            deltae_ev=0.1,
            bz_sum="tetrahedra",
        )

        self.assertIn("bz_sum = 'tetrahedra'", text)
        self.assertNotIn("ngauss", text)
        self.assertNotIn("degauss", text)

    def test_transform_input_to_bands_pw_rewrites_required_fields(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir)
            (run_dir / "pw.in").write_text(
                "&CONTROL\n"
                "   calculation = 'scf'\n"
                "   prefix = 'qe_studio'\n"
                "   outdir = './tmp'\n"
                "/\n"
                "&SYSTEM\n"
                "   ecutwfc = 40.0\n"
                "/\n"
                "&ELECTRONS\n"
                "   conv_thr = 1.0d-8\n"
                "/\n"
                "K_POINTS automatic\n"
                "4 4 4 0 0 0\n"
                "ATOMIC_SPECIES\n"
                "Si 28.085 Si.UPF\n",
                encoding="utf-8",
            )
            context = pp.resolve_qe_run_context(run_dir)

            transformed = pp.transform_input_to_bands_pw(
                context,
                nbnd=12,
                coordinate_mode="crystal_b",
                band_path_text="0.0 0.0 0.0 20 ! G\n0.5 0.0 0.0 1 ! X\n",
            )

            self.assertIn("calculation = 'bands'", transformed)
            self.assertIn("prefix = 'qe_studio'", transformed)
            self.assertIn("outdir = './tmp'", transformed)
            self.assertIn("nbnd = 12", transformed)
            self.assertIn("startingpot = 'file'", transformed)
            self.assertIn("startingwfc = 'atomic+random'", transformed)
            self.assertIn("K_POINTS crystal_b", transformed)
            self.assertNotIn("K_POINTS automatic", transformed)

    def test_resolve_qe_run_context_uses_rendered_outdir_from_input(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir)
            (run_dir / "pw.in").write_text(
                "&CONTROL\n"
                "   calculation = 'scf'\n"
                "   prefix = 'qe_studio'\n"
                "   outdir = './tmp'\n"
                "/\n",
                encoding="utf-8",
            )
            save_dir = run_dir / "tmp" / "qe_studio.save"
            save_dir.mkdir(parents=True)

            context = pp.resolve_qe_run_context(run_dir)

            self.assertEqual(context.prefix, "qe_studio")
            self.assertEqual(context.effective_outdir, "./tmp")
            self.assertEqual(context.resolved_outdir, run_dir / "tmp")
            self.assertEqual(context.save_dir, save_dir)

    def test_outdir_resolution_is_consistent_across_metadata_and_discovery(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir)
            atoms = Atoms("Si", positions=[[0.0, 0.0, 0.0]], cell=[5.4, 5.4, 5.4], pbc=True)
            (run_dir / "pw.in").write_text(
                "&CONTROL\n"
                "   calculation = 'scf'\n"
                "   prefix = 'si_case'\n"
                "   outdir = './tmp'\n"
                "/\n",
                encoding="utf-8",
            )
            save_dir = run_dir / "tmp" / "si_case.save"
            save_dir.mkdir(parents=True)
            xml_path = save_dir / "data-file-schema.xml"
            xml_path.write_text(
                (FIXTURES_DIR / "qe_trimmed_al001_pbc.xml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )

            context = pp.resolve_qe_run_context(run_dir)
            analysis_files = ep.find_available_analysis_files(run_dir)
            generation_ctx = build_calculation_context_from_request(
                atoms,
                {
                    "calculation": "scf",
                    "prefix": "si_case",
                    "outdir": "./tmp",
                    "ecutwfc": 40.0,
                    "ecutrho": 160.0,
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
                    "vdw_method": VdwMethod.NONE.value,
                },
                resolved_entries={"Si": {"filename": "Si.UPF", "cutoff_wfc": 40.0, "cutoff_rho": 160.0}},
            )
            metadata = build_input_reproducibility_metadata(
                atoms,
                generation_ctx,
                requested_workflow="scf",
                pseudo_settings={"functional": "PBE", "mode": "efficiency", "version": "1.3.0"},
                source_metadata={"source_kind": "manual"},
                structure_signature={"hash": "si-case"},
                structure_mode="source",
                validation_issues=[],
                run_dir=run_dir,
            )

            self.assertEqual(context.effective_outdir, "./tmp")
            self.assertEqual(context.resolved_outdir, run_dir / "tmp")
            self.assertEqual(context.save_dir, save_dir)
            self.assertEqual(analysis_files.xml_metadata_file, xml_path)
            self.assertEqual(metadata["control"]["rendered_outdir"], "./tmp")
            self.assertEqual(Path(metadata["control"]["resolved_outdir"]), run_dir / "tmp")

    def test_validate_dos_output_file_requires_parseable_numeric_rows(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            dos_file = Path(tmp_dir) / "dos.dat"
            dos_file.write_text("not numeric\n", encoding="utf-8")

            with self.assertRaises(Exception):
                pp.validate_dos_output_file(dos_file)

            dos_file.write_text("-1.0 0.2\n0.0 0.5\n", encoding="utf-8")
            pp.validate_dos_output_file(dos_file)

    def test_validate_band_output_file_requires_nonempty_parseable_file(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            band_file = Path(tmp_dir) / "filband.gnu"
            band_file.write_text("", encoding="utf-8")

            with self.assertRaises(ValueError):
                pp.validate_band_output_file(band_file)

            band_file.write_text("broken\n", encoding="utf-8")
            with self.assertRaises(Exception):
                pp.validate_band_output_file(band_file)

            band_file.write_text(
                "0.0 1.0\n"
                "1.0 2.0\n"
                "\n"
                "0.0 3.0\n"
                "1.0 4.0\n",
                encoding="utf-8",
            )
            pp.validate_band_output_file(band_file)


if __name__ == "__main__":
    unittest.main()
