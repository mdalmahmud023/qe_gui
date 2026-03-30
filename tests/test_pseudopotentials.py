import json
import tarfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from ase import Atoms

import pseudopotentials as pseudo


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
                "pseudopotentials.download_file",
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
                "pseudopotentials.download_file",
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

            with mock.patch("pseudopotentials.download_file", side_effect=download_side_effect) as mocked_download:
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

    def _write_archive(self, destination: Path, filenames: list[str]) -> None:
        with tarfile.open(destination, "w:gz") as archive:
            for filename in filenames:
                source_path = destination.parent / f"source-{filename}"
                source_path.write_text(f"UPF {filename}")
                archive.add(source_path, arcname=filename)
                source_path.unlink()


if __name__ == "__main__":
    unittest.main()
