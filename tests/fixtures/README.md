Fixtures in this directory are derived from Quantum ESPRESSO upstream example outputs.

- `qe_real_pbe.bands.dat.gnu`:
  Derived from `QEF/q-e` `PP/examples/exx_interpolated_bands_example/pbe_reference_bands/pbe.bands.dat.gnu`.
  This fixture keeps the first four band blocks unchanged to preserve the observed `filband.gnu`-style layout.
- `qe_real_pbe.bands.in`:
  Copied from the matching upstream `pbe.bands.in`.
- `qe_trimmed_al001_pbc.xml`:
  Trimmed valid XML derived from `QEF/q-e` `PW/examples/ESM_example/reference/Al001_pbc.xml`.
  Only the minimal structure needed for XML-first Fermi metadata tests is retained.
