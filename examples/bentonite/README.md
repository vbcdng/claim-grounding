# Bentonite example — two sources are intentionally missing

Two of this example's nine sources are Springer subscription articles whose
extracted full text may not be redistributed, so the public repo ships
without them:

- `Effect_of_the_surface_hydration_..._451becad04.txt`
  (DOI 10.1007/s10450-020-00263-y)
- `Adsorption_properties_of_cesium_by_natural_Na-bentonite_and_Ca-bentonite_91a1c20b01.txt`
  (DOI 10.1007/s10967-024-09627-y)

If they are absent from `sources/`, `verify_my_text.py` — including
`--estimate` — prints two `source file missing` preflight warnings. That is
expected, not breakage: the run completes and marks the claims citing those
two sources as unverifiable against them instead of guessing.

To reproduce the full nine-source run, fetch the two articles through your
own (institutional or purchased) access, save their extracted text under the
exact file names above in `sources/`, and re-run.

For a complete, works-out-of-the-box example use
`examples/chimpanzee_validation` — that is the one the README quickstart
walks through.
