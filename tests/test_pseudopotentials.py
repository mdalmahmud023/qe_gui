import json
import tarfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from ase import Atoms

from qe_studio.services import pseudopotentials as pseudo


class PseudopotentialHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.atoms = Atoms(
            "SiO2",
            positions=[
                (0.0, 0.0, 0.0),
                (1.2, 1.2, 1.2),
                (2.0, 2.0, 2.0),
            ],
            cell=[5.0, 5.0, 5.0],
            pbc=True,
        )
        self.manifest = {
            "O": {
                "filename": "O.test.UPF",
                "md5": "o-md5",
                "pseudopotential": "PAW",
                "cutoff_wfc": 50.0,
                "cutoff_rho": 400.0,
            },
            "Si": {
                "filename": "Si.test.UPF",
                "md5": "si-md5",
                "pseudopotential": "PAW",
                "cutoff_wfc": 45.0,
                "cutoff_rho": 360.0,
            },
        }

    def test_get_unique_elements_returns_sorted_symbols(self) -> None:
        self.assertEqual(pseudo.get_unique_elements(self.atoms), ["O", "Si"])

    def test_sssp_family_helpers_build_expected_names(self) -> None:
        self.assertEqual(
            pseudo.get_sssp_family_key("1.3.0", "PBE", "efficiency"),
            "SSSP_1.3.0_PBE_efficiency",
        )
        self.assertEqual(
            pseudo.get_sssp_manifest_filename("1.3.0", "PBEsol", "precision"),
            "SSSP_1.3.0_PBEsol_precision.json",
        )
        self.assertEqual(
            pseudo.get_sssp_archive_filename("1.3.0", "PBE", "precision"),
            "SSSP_1.3.0_PBE_precision.tar.gz",
        )
        self.assertIn("rcyfm-68h65", pseudo.get_sssp_record_base_url("1.3.0"))

    def test_resolve_manifest_entries_requires_all_elements(self) -> None:
        with self.assertRaises(pseudo.PseudopotentialResolutionError) as ctx:
            pseudo.resolve_manifest_entries({"Si": self.manifest["Si"]}, ["O", "Si"])
        self.assertIn("elements not found in manifest: O", str(ctx.exception))

    def test_find_extracted_upf_returns_matching_file(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            extract_dir = Path(tmp_dir)
            nested_dir = extract_dir / "nested"
            nested_dir.mkdir()
            target_file = nested_dir / "Si.test.UPF"
            target_file.write_text("fake upf")

            self.assertEqual(pseudo.find_extracted_upf(extract_dir, "Si.test.UPF"), target_file)
            self.assertIsNone(pseudo.find_extracted_upf(extract_dir, "O.test.UPF"))

    def test_structure_signatures_can_compare_element_sets(self) -> None:
        first = pseudo.get_structure_signature(self.atoms)
        second = pseudo.get_structure_signature(
            Atoms(
                "OSi",
                positions=[(0.0, 0.0, 0.0), (1.0, 1.0, 1.0)],
                cell=[5.0, 5.0, 5.0],
                pbc=True,
            )
        )
        third = pseudo.get_structure_signature(
            Atoms(
                "AlO",
                positions=[(0.0, 0.0, 0.0), (1.0, 1.0, 1.0)],
                cell=[5.0, 5.0, 5.0],
                pbc=True,
            )
        )

        self.assertTrue(pseudo.has_same_element_set(first, second))
        self.assertFalse(pseudo.has_same_element_set(first, third))
        self.assertNotEqual(first, second)

    def test_resolve_sssp_pseudopotentials_writes_cache_and_resolved_map(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            cache_root = Path(tmp_dir)
            with mock.patch(
                "qe_studio.services.pseudopotentials.download_file",
                side_effect=self._fake_download_factory(self.manifest),
            ):
                result = pseudo.resolve_sssp_pseudopotentials(
                    self.atoms,
                    version="1.3.0",
                    functional="PBE",
                    mode="efficiency",
                    use_cache=True,
                    cache_root=cache_root,
                )

            family_dir = cache_root / "1.3.0" / "PBE" / "efficiency"
            resolved_map_path = family_dir / "resolved_map.json"

            self.assertTrue((family_dir / "manifest.json").exists())
            self.assertTrue((family_dir / "archive.tar.gz").exists())
            self.assertTrue((family_dir / "extracted").exists())
            self.assertTrue((family_dir / ".extract_complete").exists())
            self.assertTrue(resolved_map_path.exists())
            self.assertEqual(
                result["ase_pseudopotentials"],
                {"O": "O.test.UPF", "Si": "Si.test.UPF"},
            )
            self.assertTrue(Path(result["pseudo_dir"]).is_dir())

            resolved_map = json.loads(resolved_map_path.read_text())
            self.assertEqual(resolved_map["settings"]["functional"], "PBE")
            self.assertEqual(resolved_map["structure_signature"]["elements"], ["O", "Si"])

    def test_resolve_sssp_pseudopotentials_repairs_corrupt_manifest(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            cache_root = Path(tmp_dir)
            family_dir = cache_root / "1.3.0" / "PBE" / "efficiency"
            family_dir.mkdir(parents=True)
            (family_dir / "manifest.json").write_text("{broken json")

            with mock.patch(
                "qe_studio.services.pseudopotentials.download_file",
                side_effect=self._fake_download_factory(self.manifest),
            ):
                result = pseudo.resolve_sssp_pseudopotentials(
                    self.atoms,
                    version="1.3.0",
                    functional="PBE",
                    mode="efficiency",
                    use_cache=True,
                    cache_root=cache_root,
                )

            self.assertTrue(result["warnings"])
            self.assertIn("Cached manifest was invalid", result["warnings"][0])

    def test_resolve_sssp_pseudopotentials_repairs_missing_extracted_file(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            cache_root = Path(tmp_dir)
            download_side_effect = self._fake_download_factory(self.manifest)

            with mock.patch("qe_studio.services.pseudopotentials.download_file", side_effect=download_side_effect) as mocked_download:
                first_result = pseudo.resolve_sssp_pseudopotentials(
                    self.atoms,
                    version="1.3.0",
                    functional="PBE",
                    mode="efficiency",
                    use_cache=True,
                    cache_root=cache_root,
                )
                missing_file = Path(first_result["pseudo_dir"]) / "Si.test.UPF"
                missing_file.unlink()

                second_result = pseudo.resolve_sssp_pseudopotentials(
                    self.atoms,
                    version="1.3.0",
                    functional="PBE",
                    mode="efficiency",
                    use_cache=True,
                    cache_root=cache_root,
                )

            self.assertGreaterEqual(mocked_download.call_count, 3)
            self.assertTrue((Path(second_result["pseudo_dir"]) / "Si.test.UPF").exists())
            self.assertTrue(second_result["warnings"])
            self.assertIn("missing after using the cached archive", second_result["warnings"][0])

    def test_resolve_fully_relativistic_pseudopotentials_downloads_sg15_fr_files(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            cache_root = Path(tmp_dir)
            atoms = Atoms(
                "AuFe",
                positions=[(0.0, 0.0, 0.0), (1.2, 1.2, 1.2)],
                cell=[5.0, 5.0, 5.0],
                pbc=True,
            )

            with mock.patch(
                "qe_studio.services.pseudopotentials.download_file",
                side_effect=self._fake_fully_relativistic_download_factory(),
            ):
                result = pseudo.resolve_fully_relativistic_pseudopotentials(
                    atoms,
                    functional="PBE",
                    use_cache=True,
                    cache_root=cache_root,
                )

            family_dir = cache_root / "sg15_oncv" / "PBE" / "fully_relativistic"
            self.assertTrue((family_dir / "index.html").exists())
            self.assertTrue((family_dir / "extracted" / "Au_ONCV_PBE_FR-1.0.upf").exists())
            self.assertTrue((family_dir / "extracted" / "Fe_ONCV_PBE_FR-1.1.upf").exists())
            self.assertEqual(
                result["ase_pseudopotentials"],
                {"Au": "Au_ONCV_PBE_FR-1.0.upf", "Fe": "Fe_ONCV_PBE_FR-1.1.upf"},
            )
            self.assertTrue(result["settings"]["fully_relativistic"])
            self.assertEqual(result["settings"]["source"], "sg15_oncv_fr")
            self.assertTrue(result["warnings"])
            self.assertIn("heuristic cutoffs", result["warnings"][0])

    def test_resolve_fully_relativistic_pseudopotentials_requires_pbe(self) -> None:
        with self.assertRaises(pseudo.PseudopotentialResolutionError) as ctx:
            pseudo.resolve_fully_relativistic_pseudopotentials(
                self.atoms,
                functional="PBEsol",
                use_cache=True,
            )
        self.assertIn("PBE", str(ctx.exception))

    def test_resolve_fully_relativistic_index_entries_picks_latest_fr_filename(self) -> None:
        result = pseudo.resolve_fully_relativistic_index_entries(
            [
                "Fe_ONCV_PBE-1.2.upf",
                "Fe_ONCV_PBE_FR-1.0.upf",
                "Fe_ONCV_PBE_FR-1.1.upf",
                "O_ONCV_PBE_FR-1.0.upf",
            ],
            ["Fe", "O"],
            functional="PBE",
        )

        self.assertEqual(result["Fe"]["filename"], "Fe_ONCV_PBE_FR-1.1.upf")
        self.assertEqual(result["O"]["filename"], "O_ONCV_PBE_FR-1.0.upf")

    def test_inspect_soc_compatibility_accepts_fully_relativistic_upf(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            upf_path = Path(tmp_dir) / "Pt_fr.UPF"
            upf_path.write_text('<UPF><PP_HEADER has_so="T" relativistic="full" /></UPF>')

            report = pseudo.inspect_soc_compatibility(
                {"Pt": {"filename": upf_path.name, "local_path": str(upf_path)}}
            )

            self.assertTrue(report.all_compatible)
            self.assertEqual(len(report.records), 1)
            self.assertTrue(report.records[0].compatible)

    def test_inspect_soc_compatibility_rejects_scalar_upf(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            upf_path = Path(tmp_dir) / "Si_scalar.UPF"
            upf_path.write_text('<UPF><PP_HEADER has_so="F" relativistic="scalar" /></UPF>')

            report = pseudo.inspect_soc_compatibility(
                {"Si": {"filename": upf_path.name, "local_path": str(upf_path)}}
            )

            self.assertFalse(report.all_compatible)
            self.assertEqual(len(report.incompatible_records), 1)
            self.assertIn("fully relativistic pseudopotentials", report.message)

    def test_inspect_soc_compatibility_accepts_legacy_fully_relativistic_upf(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            upf_path = Path(tmp_dir) / "Bi_legacy.UPF"
            upf_path.write_text(
                "<UPF><PP_HEADER></PP_HEADER><PP_INFO>"
                "The Pseudo was generated with a Fully-Relativistic Calculation"
                "</PP_INFO></UPF>"
            )

            report = pseudo.inspect_soc_compatibility(
                {"Bi": {"filename": upf_path.name, "local_path": str(upf_path)}}
            )

            self.assertTrue(report.all_compatible)
            self.assertIn("fully relativistic calculation", report.records[0].reason.lower())

    def test_inspect_soc_compatibility_rejects_legacy_scalar_relativistic_upf(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            upf_path = Path(tmp_dir) / "Ca_legacy.UPF"
            upf_path.write_text(
                "<UPF><PP_HEADER></PP_HEADER><PP_INFO>"
                "The Pseudo was generated with a Scalar-Relativistic Calculation"
                "</PP_INFO></UPF>"
            )

            report = pseudo.inspect_soc_compatibility(
                {"Ca": {"filename": upf_path.name, "local_path": str(upf_path)}}
            )

            self.assertFalse(report.all_compatible)
            self.assertIn("scalar-relativistic calculation", report.records[0].reason.lower())

    def test_inspect_soc_compatibility_rejects_missing_local_path(self) -> None:
        report = pseudo.inspect_soc_compatibility(
            {"Bi": {"filename": "Bi.UPF", "local_path": "missing/Bi.UPF"}}
        )

        self.assertFalse(report.all_compatible)
        self.assertIn("resolved UPF file is missing", report.message)

    def test_ensure_soc_compatible_pseudopotentials_rejects_mixed_set(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            fr_path = Path(tmp_dir) / "Bi_fr.UPF"
            scalar_path = Path(tmp_dir) / "O_scalar.UPF"
            fr_path.write_text('<UPF><PP_HEADER has_so="T" relativistic="full" /></UPF>')
            scalar_path.write_text('<UPF><PP_HEADER has_so="F" relativistic="scalar" /></UPF>')

            with self.assertRaises(pseudo.PseudopotentialCompatibilityError) as ctx:
                pseudo.ensure_soc_compatible_pseudopotentials(
                    {
                        "Bi": {"filename": fr_path.name, "local_path": str(fr_path)},
                        "O": {"filename": scalar_path.name, "local_path": str(scalar_path)},
                    }
                )

            self.assertIn("O", str(ctx.exception))
            self.assertIn("fully relativistic pseudopotentials", str(ctx.exception))

    def _fake_download_factory(self, manifest: dict) -> callable:
        def fake_download(url: str, destination: Path, **_kwargs) -> Path:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if url.endswith(".json?download=1"):
                destination.write_text(json.dumps(manifest))
                return destination
            if url.endswith(".tar.gz?download=1"):
                self._write_archive(destination, [entry["filename"] for entry in manifest.values()])
                return destination
            raise AssertionError(f"Unexpected download URL: {url}")

        return fake_download

    def _fake_fully_relativistic_download_factory(self) -> callable:
        index_html = """
        <html><body>
        <a href="Au_ONCV_PBE_FR-1.0.upf">Au_ONCV_PBE_FR-1.0.upf</a>
        <a href="Fe_ONCV_PBE_FR-1.0.upf">Fe_ONCV_PBE_FR-1.0.upf</a>
        <a href="Fe_ONCV_PBE_FR-1.1.upf">Fe_ONCV_PBE_FR-1.1.upf</a>
        </body></html>
        """

        def fake_download(url: str, destination: Path, **_kwargs) -> Path:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if url == pseudo.SG15_ONCV_UPF_INDEX_URL:
                destination.write_text(index_html)
                return destination
            if url.endswith("Au_ONCV_PBE_FR-1.0.upf"):
                destination.write_text('<UPF><PP_HEADER has_so="T" relativistic="full" /></UPF>')
                return destination
            if url.endswith("Fe_ONCV_PBE_FR-1.1.upf"):
                destination.write_text('<UPF><PP_HEADER has_so="T" relativistic="full" /></UPF>')
                return destination
            raise AssertionError(f"Unexpected download URL: {url}")

        return fake_download

    def _write_archive(self, destination: Path, filenames: list[str]) -> None:
        with tarfile.open(destination, "w:gz") as archive:
            for filename in filenames:
                source_path = destination.parent / f"source-{filename}"
                source_path.write_text(f"UPF {filename}")
                archive.add(source_path, arcname=filename)
                source_path.unlink()


if __name__ == "__main__":
    unittest.main()
