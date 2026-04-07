import unittest

from ase import Atoms

from qe_studio.services import structure_viewer as sv


class StructureViewerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.atoms = Atoms(
            "NaClH",
            positions=[[0.0, 0.0, 0.0], [2.0, 2.0, 2.0], [1.0, 1.0, 1.0]],
            cell=[5.0, 5.0, 5.0],
            pbc=True,
        )

    def test_normalize_visible_species_defaults_to_all_species(self) -> None:
        selection = sv.normalize_visible_species(self.atoms, selected_species=None)
        self.assertEqual(selection, ("Cl", "H", "Na"))

    def test_build_display_atoms_filters_selected_species(self) -> None:
        display_atoms = sv.build_display_atoms(
            self.atoms,
            visible_species=("Na", "Cl"),
            supercell_repeat=(1, 1, 1),
        )

        self.assertIsNotNone(display_atoms)
        self.assertEqual(display_atoms.get_chemical_symbols(), ["Na", "Cl"])

    def test_build_display_atoms_returns_none_when_no_species_are_visible(self) -> None:
        display_atoms = sv.build_display_atoms(
            self.atoms,
            visible_species=(),
            supercell_repeat=(1, 1, 1),
        )

        self.assertIsNone(display_atoms)

    def test_build_display_atoms_applies_supercell_repeat(self) -> None:
        display_atoms = sv.build_display_atoms(
            self.atoms,
            visible_species=("Na", "Cl", "H"),
            supercell_repeat=(2, 1, 1),
        )

        self.assertIsNotNone(display_atoms)
        self.assertEqual(len(display_atoms), len(self.atoms) * 2)

    def test_get_effective_show_bonds_depends_on_style(self) -> None:
        self.assertTrue(sv.get_effective_show_bonds(sv.STYLE_MODE_STICK, False))
        self.assertFalse(sv.get_effective_show_bonds(sv.STYLE_MODE_SPACEFILL, True))
        self.assertFalse(sv.get_effective_show_bonds(sv.STYLE_MODE_BALL_AND_STICK, False))

    def test_build_structure_viewer_html_includes_labels_unit_cell_and_axes(self) -> None:
        html = sv.build_structure_viewer_html(
            self.atoms,
            settings=sv.ViewerSettings(
                label_mode=sv.LABEL_MODE_ELEMENT_INDEX,
                style_mode=sv.STYLE_MODE_BALL_AND_STICK,
                show_bonds=True,
                show_unit_cell=True,
                show_axes=True,
                visible_species=("Cl", "H", "Na"),
                supercell_repeat=(1, 1, 1),
            ),
        )

        self.assertIn("addUnitCell", html)
        self.assertIn("addLabel", html)
        self.assertIn("Na1", html)
        self.assertIn("addLine", html)

    def test_format_supercell_repeat_uses_compact_notation(self) -> None:
        self.assertEqual(sv.format_supercell_repeat((2, 1, 3)), "2x1x3")


if __name__ == "__main__":
    unittest.main()
