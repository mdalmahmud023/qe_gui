import unittest

from ase.build import bulk

from qe_studio.services import structure_tools as stt


class StructureToolsTests(unittest.TestCase):
    def test_make_primitive_atoms_reduces_conventional_silicon(self) -> None:
        silicon_conventional = bulk("Si", "diamond", a=5.43, cubic=True)

        result = stt.make_primitive_atoms(silicon_conventional)

        self.assertEqual(len(silicon_conventional), 8)
        self.assertEqual(len(result.atoms), 2)
        self.assertTrue(result.changed)
        self.assertIn("primitive cell", result.message.lower())

    def test_make_conventional_atoms_expands_primitive_silicon(self) -> None:
        silicon_primitive = bulk("Si", "diamond", a=5.43)

        result = stt.make_conventional_atoms(silicon_primitive)

        self.assertEqual(len(silicon_primitive), 2)
        self.assertEqual(len(result.atoms), 8)
        self.assertTrue(result.changed)
        self.assertAlmostEqual(result.atoms.get_volume(), silicon_primitive.get_volume() * 4.0)
        self.assertIn("conventional cell", result.message.lower())

    def test_generate_band_path_rows_returns_labeled_rows(self) -> None:
        silicon_primitive = bulk("Si", "diamond", a=5.43)

        result = stt.generate_band_path_rows(silicon_primitive, points_per_segment=20)

        self.assertTrue(result.rows_text.startswith("0.000000 0.000000 0.000000 20 ! G"))
        self.assertIn("! X", result.rows_text)
        self.assertEqual(result.points_per_segment, 20)
        self.assertGreaterEqual(len(result.special_point_labels), 2)


if __name__ == "__main__":
    unittest.main()
