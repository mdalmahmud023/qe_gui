import unittest
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from ase import Atoms
from ase.io import write

from qe_studio.services import structure_sources as sources


class StructureSourcesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bulk_atoms = Atoms(
            "Si2",
            scaled_positions=[[0.0, 0.0, 0.0], [0.25, 0.25, 0.25]],
            cell=[5.43, 5.43, 5.43],
            pbc=True,
        )

    def test_import_structure_file_reads_cif(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "Si.cif"
            write(path, self.bulk_atoms, format="cif")

            result = sources.import_structure_file(
                filename=path.name,
                file_bytes=path.read_bytes(),
                requested_format=sources.FILE_FORMAT_CIF,
            )

        self.assertEqual(result.source_kind, "file")
        self.assertEqual(result.source_format, "cif")
        self.assertEqual(len(result.atoms), len(self.bulk_atoms))

    def test_import_structure_file_reads_vasp(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "POSCAR"
            write(path, self.bulk_atoms, format="vasp")

            result = sources.import_structure_file(
                filename=path.name,
                file_bytes=path.read_bytes(),
                requested_format=sources.FILE_FORMAT_VASP,
            )

        self.assertEqual(result.source_format, "vasp")
        self.assertEqual(len(result.atoms), len(self.bulk_atoms))

    def test_import_structure_file_reads_xsf(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "Si.xsf"
            write(path, self.bulk_atoms, format="xsf")

            result = sources.import_structure_file(
                filename=path.name,
                file_bytes=path.read_bytes(),
                requested_format=sources.FILE_FORMAT_XSF,
            )

        self.assertEqual(result.source_format, "xsf")
        self.assertEqual(len(result.atoms), len(self.bulk_atoms))

    def test_import_structure_file_rejects_xyz_without_full_cell(self) -> None:
        xyz_text = "2\ncomment\nSi 0.0 0.0 0.0\nSi 1.0 1.0 1.0\n"
        with self.assertRaises(ValueError) as ctx:
            sources.import_structure_file(
                filename="molecule.xyz",
                file_bytes=xyz_text.encode("utf-8"),
                requested_format=sources.FILE_FORMAT_XYZ,
            )
        self.assertIn("full 3D lattice", str(ctx.exception))

    def test_build_manual_structure_wraps_positions_and_validates_formula(self) -> None:
        result = sources.build_manual_structure(
            a=5.0,
            b=5.0,
            c=5.0,
            alpha=90.0,
            beta=90.0,
            gamma=90.0,
            coordinate_mode=sources.MANUAL_COORDINATE_MODE_FRACTIONAL,
            atom_rows=[
                {"symbol": "Si", "x": 0.0, "y": 0.0, "z": 0.0},
                {"symbol": "Si", "x": 1.25, "y": -0.75, "z": 0.25},
            ],
            formula_text="Si2",
        )

        self.assertEqual(result.source_kind, "manual")
        self.assertEqual(len(result.atoms), 2)
        self.assertTrue(result.warnings)

    def test_build_manual_structure_rejects_duplicate_sites(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            sources.build_manual_structure(
                a=5.0,
                b=5.0,
                c=5.0,
                alpha=90.0,
                beta=90.0,
                gamma=90.0,
                coordinate_mode=sources.MANUAL_COORDINATE_MODE_FRACTIONAL,
                atom_rows=[
                    {"symbol": "Si", "x": 0.0, "y": 0.0, "z": 0.0},
                    {"symbol": "Si", "x": 1.0, "y": 0.0, "z": 0.0},
                ],
            )
        self.assertIn("Duplicate", str(ctx.exception))

    def test_build_ordered_double_perovskite_returns_expected_counts(self) -> None:
        result = sources.build_ordered_double_perovskite(
            a_site="Na",
            b_site="Ca",
            b_prime_site="Cd",
            x_site="F",
            lattice_parameter=9.5,
        )

        self.assertEqual(result.source_kind, "prototype")
        self.assertEqual(result.source_label, "Ordered Double Perovskite (A2BB'X6)")
        atom_counts = Counter(result.atoms.get_chemical_symbols())
        reduced_formula_units = atom_counts["Na"] // 2
        self.assertIn(len(result.atoms), {10, 40})
        self.assertGreaterEqual(reduced_formula_units, 1)
        self.assertEqual(atom_counts, Counter({"Na": 2 * reduced_formula_units, "Ca": reduced_formula_units, "Cd": reduced_formula_units, "F": 6 * reduced_formula_units}))
        self.assertEqual(result.source_details["prototype_family"], "Ordered Double Perovskite")
        self.assertEqual(result.source_details["formula_template"], "A2BB'X6")

    def test_build_cubic_perovskite_returns_expected_counts(self) -> None:
        result = sources.build_cubic_perovskite(
            a_site="Cs",
            b_site="Pb",
            x_site="I",
            lattice_parameter=6.2,
        )

        self.assertEqual(result.source_kind, "prototype")
        self.assertEqual(result.source_label, "Cubic Perovskite (ABX3)")
        self.assertEqual(Counter(result.atoms.get_chemical_symbols()), Counter({"Cs": 1, "Pb": 1, "I": 3}))
        self.assertEqual(result.source_details["prototype_family"], "Cubic Perovskite")
        self.assertEqual(result.source_details["formula_template"], "ABX3")

    def test_build_rocksalt_returns_expected_counts(self) -> None:
        result = sources.build_rocksalt(
            a_site="Na",
            b_site="Cl",
            lattice_parameter=5.6,
        )

        self.assertEqual(result.source_kind, "prototype")
        self.assertEqual(result.source_label, "Rocksalt (AB)")
        atom_counts = Counter(result.atoms.get_chemical_symbols())
        reduced_formula_units = atom_counts["Na"]
        self.assertGreaterEqual(reduced_formula_units, 1)
        self.assertEqual(atom_counts, Counter({"Na": reduced_formula_units, "Cl": reduced_formula_units}))
        self.assertEqual(result.source_details["prototype_family"], "Rocksalt")
        self.assertEqual(result.source_details["formula_template"], "AB")


if __name__ == "__main__":
    unittest.main()
