import unittest
from collections import Counter

from qe_studio.services import prototype_catalog as catalog


class PrototypeCatalogTests(unittest.TestCase):
    def test_catalog_exposes_expected_family_order(self) -> None:
        self.assertEqual(
            catalog.get_prototype_family_keys(),
            (
                "ordered_double_perovskite_a2bbx6",
                "cubic_perovskite_abx3",
                "rocksalt_ab",
            ),
        )

    def test_build_prototype_family_dispatches_ordered_double_perovskite(self) -> None:
        result = catalog.build_prototype_family(
            "ordered_double_perovskite_a2bbx6",
            {
                "a_site": "Na",
                "b_site": "Ca",
                "b_prime_site": "Cd",
                "x_site": "H",
                "lattice_parameter": 8.0,
                "x_parameter": 0.25,
            },
        )

        counts = Counter(result.atoms.get_chemical_symbols())
        reduced_formula_units = counts["Na"] // 2
        self.assertEqual(result.source_label, "Ordered Double Perovskite (A2BB'X6)")
        self.assertEqual(counts, Counter({"Na": 2 * reduced_formula_units, "Ca": reduced_formula_units, "Cd": reduced_formula_units, "H": 6 * reduced_formula_units}))

    def test_build_prototype_family_uses_defaults_for_missing_optional_advanced_fields(self) -> None:
        result = catalog.build_prototype_family(
            "ordered_double_perovskite_a2bbx6",
            {
                "a_site": "Na",
                "b_site": "Ca",
                "b_prime_site": "Cd",
                "x_site": "H",
                "lattice_parameter": 8.0,
            },
        )

        self.assertEqual(result.source_details["x_parameter"], 0.25)


if __name__ == "__main__":
    unittest.main()
