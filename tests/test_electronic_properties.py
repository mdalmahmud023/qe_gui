import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import electronic_properties as ep


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class ElectronicPropertiesTests(unittest.TestCase):
    def test_find_available_analysis_files_metadata_only_detects_xml_and_pw_out(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir)
            (run_dir / "pw.in").write_text("&CONTROL\n/\n")
            (run_dir / "pw.out").write_text("the Fermi energy is    1.2345 ev\n")
            save_dir = run_dir / "pwscf.save"
            save_dir.mkdir()
            (save_dir / "data-file-schema.xml").write_text(
                (FIXTURES_DIR / "qe_trimmed_al001_pbc.xml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )

            analysis_files = ep.find_available_analysis_files(run_dir)
            fermi_metadata = ep.extract_qe_fermi_metadata(analysis_files)

            self.assertTrue(analysis_files.has_any_artifact)
            self.assertTrue(analysis_files.is_metadata_only)
            self.assertIsNotNone(analysis_files.xml_metadata_file)
            self.assertIsNotNone(analysis_files.pw_output_file)
            self.assertIsNotNone(fermi_metadata)
            assert fermi_metadata is not None
            self.assertEqual(fermi_metadata.source_type, "xml")
            self.assertAlmostEqual(fermi_metadata.energy_ev, -2.0409, places=3)

    def test_extract_qe_fermi_metadata_falls_back_to_pw_out(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir)
            (run_dir / "pw.out").write_text("the Fermi energy is    -2.0409 ev\n", encoding="utf-8")

            analysis_files = ep.find_available_analysis_files(run_dir)
            fermi_metadata = ep.extract_qe_fermi_metadata(analysis_files)

            self.assertIsNotNone(fermi_metadata)
            assert fermi_metadata is not None
            self.assertEqual(fermi_metadata.source_type, "pw.out")
            self.assertAlmostEqual(fermi_metadata.energy_ev, -2.0409, places=4)

    def test_parse_qe_dos_two_column_uses_header_fermi(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            dos_file = Path(tmp_dir) / "sample.dos"
            dos_file.write_text(
                "# Fermi energy = 5.5000 eV\n"
                "-1.0 0.5\n"
                "0.0 1.0\n"
                "1.0 0.4\n",
                encoding="utf-8",
            )

            dos_data = ep.parse_qe_dos(dos_file)

            self.assertEqual(dos_data.energies_ev, [-1.0, 0.0, 1.0])
            self.assertEqual(dos_data.dos_total, [0.5, 1.0, 0.4])
            self.assertAlmostEqual(dos_data.fermi_energy_ev, 5.5, places=4)
            self.assertFalse(dos_data.spin_polarized)

    def test_parse_qe_dos_three_column(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            dos_file = Path(tmp_dir) / "total_dos.dat"
            dos_file.write_text(
                "-1.0 0.5 0.1\n"
                "0.0 1.0 0.6\n"
                "1.0 0.4 1.0\n",
                encoding="utf-8",
            )

            dos_data = ep.parse_qe_dos(dos_file)

            self.assertEqual(dos_data.dos_total, [0.5, 1.0, 0.4])
            self.assertIsNone(dos_data.fermi_energy_ev)

    def test_parse_qe_dos_accepts_explicit_spin_header(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            dos_file = Path(tmp_dir) / "spin_total.dos"
            dos_file.write_text(
                "# Energy DOS_up DOS_down IntDOS\n"
                "-1.0 0.1 0.2 0.0\n"
                "0.0 0.3 0.4 0.0\n",
                encoding="utf-8",
            )

            dos_data = ep.parse_qe_dos(dos_file)

            self.assertTrue(dos_data.spin_polarized)
            self.assertEqual(dos_data.dos_total, [0.30000000000000004, 0.7])

    def test_parse_qe_dos_rejects_ambiguous_four_column(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            dos_file = Path(tmp_dir) / "ambiguous.dos"
            dos_file.write_text(
                "-1.0 0.1 0.2 0.0\n"
                "0.0 0.3 0.4 0.0\n",
                encoding="utf-8",
            )

            with self.assertRaises(ep.AnalysisParseError):
                ep.parse_qe_dos(dos_file)

    def test_parse_qe_band_structure_parses_real_fixture_and_tick_positions(self) -> None:
        band_file = FIXTURES_DIR / "qe_real_pbe.bands.dat.gnu"
        band_input_file = FIXTURES_DIR / "qe_real_pbe.bands.in"

        band_data = ep.parse_qe_band_structure(band_file, band_input_file=band_input_file)

        self.assertEqual(len(band_data.distances), 41)
        self.assertEqual(len(band_data.eigenvalues_ev), 41)
        self.assertEqual(len(band_data.eigenvalues_ev[0]), 4)
        self.assertEqual(
            [round(value, 4) for value in band_data.kpoint_tick_positions],
            [0.0, 1.0, 1.3536, 1.9659, 2.8320],
        )
        self.assertEqual(band_data.kpoint_tick_labels, [])

    def test_parse_qe_band_structure_rejects_inconsistent_blocks(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            band_file = Path(tmp_dir) / "broken.filband.gnu"
            band_file.write_text(
                "0.0 1.0\n"
                "1.0 2.0\n"
                "\n"
                "0.0 3.0\n",
                encoding="utf-8",
            )

            with self.assertRaises(ep.AnalysisParseError):
                ep.parse_qe_band_structure(band_file)

    def test_parse_band_input_ticks_with_labels(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            band_input_file = Path(tmp_dir) / "labeled.bands.in"
            band_input_file.write_text(
                "K_POINTS crystal_b\n"
                "3\n"
                "0.0 0.0 0.0 2 ! G\n"
                "0.5 0.0 0.0 2 ! X\n"
                "0.5 0.5 0.0 1 ! M\n",
                encoding="utf-8",
            )

            positions, labels = ep.parse_band_input_ticks(band_input_file, [0.0, 0.5, 1.0, 1.5, 2.0])

            self.assertEqual(positions, [0.0, 1.0, 2.0])
            self.assertEqual(labels, ["G", "X", "M"])

    def test_make_dos_figure_has_zero_reference_when_aligned(self) -> None:
        dos_data = ep.DOSData(
            energies_ev=[-1.0, 0.0, 1.0],
            dos_total=[0.2, 1.0, 0.3],
            source_file=Path("sample.dos"),
            source_type="qe_dos",
            fermi_energy_ev=0.5,
        )

        figure = ep.make_dos_figure(dos_data, align_to_fermi=True)

        self.assertEqual(len(figure.data), 1)
        self.assertEqual(len(figure.layout.shapes), 1)
        self.assertEqual(figure.layout.shapes[0]["x0"], 0.0)

    def test_make_band_figure_has_reference_and_separators(self) -> None:
        band_data = ep.BandData(
            distances=[0.0, 1.0, 2.0],
            eigenvalues_ev=[
                [-1.0, 1.0],
                [-0.5, 1.5],
                [0.0, 2.0],
            ],
            source_file=Path("sample.filband.gnu"),
            source_type="qe_filband_gnu",
            kpoint_tick_positions=[0.0, 1.0, 2.0],
            kpoint_tick_labels=["G", "X", "L"],
            fermi_energy_ev=0.25,
        )

        figure = ep.make_band_figure(band_data, align_to_fermi=True)

        self.assertEqual(len(figure.data), 2)
        self.assertEqual(len(figure.layout.shapes), 2)
        self.assertEqual(list(figure.layout.xaxis.tickvals), [0.0, 1.0, 2.0])
        self.assertEqual(list(figure.layout.xaxis.ticktext), ["G", "X", "L"])

    def test_extract_qe_fermi_metadata_handles_split_spin_xml_without_single_reference(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir)
            save_dir = run_dir / "pwscf.save"
            save_dir.mkdir()
            (save_dir / "data-file-schema.xml").write_text(
                "<?xml version='1.0' encoding='UTF-8'?>\n"
                "<qes:espresso xmlns:qes='http://www.quantum-espresso.org/ns/qes/qes-1.0'>\n"
                "  <output>\n"
                "    <band_structure>\n"
                "      <two_fermi_energies>0.100000 0.200000</two_fermi_energies>\n"
                "    </band_structure>\n"
                "  </output>\n"
                "</qes:espresso>\n",
                encoding="utf-8",
            )

            analysis_files = ep.find_available_analysis_files(run_dir)
            metadata = ep.extract_qe_fermi_metadata(analysis_files)

            self.assertIsNotNone(metadata)
            assert metadata is not None
            self.assertEqual(metadata.source_type, "xml")
            self.assertIsNone(metadata.energy_ev)
            self.assertIn("distinct spin-dependent", metadata.message or "")

    def test_find_available_analysis_files_prefers_band_input_with_kpoints_over_pp_input(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir)
            (run_dir / "filband.gnu").write_text("0.0 1.0\n1.0 2.0\n\n0.0 3.0\n1.0 4.0\n", encoding="utf-8")
            (run_dir / "bands_pp.in").write_text("&BANDS\n/\n", encoding="utf-8")
            (run_dir / "bands_pw.in").write_text(
                "K_POINTS crystal_b\n"
                "2\n"
                "0.0 0.0 0.0 2 ! G\n"
                "0.5 0.0 0.0 1 ! X\n",
                encoding="utf-8",
            )

            analysis_files = ep.find_available_analysis_files(run_dir)

            self.assertEqual(analysis_files.band_input_file, run_dir / "bands_pw.in")


if __name__ == "__main__":
    unittest.main()
