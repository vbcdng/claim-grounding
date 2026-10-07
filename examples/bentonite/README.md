# Bentonite example — five sources are intentionally missing

This example cites nine sources. The public repository ships only the
four whose own text carries an open Creative Commons licence (CC BY
allows anyone to share the text with credit; CC BY-NC allows the same
for non-commercial use):

- `Cesium_adsorption_desorption_behavior_of_clay_minerals_..._a973183290.pdf`
  (Scientific Reports 2016, CC BY 4.0)
- `Cesium_Sorption_and_Desorption_on_Glauconite__Bentonite__Zeolite_and_Diatomite_32d6151f27.pdf`
  (Minerals 2019, CC BY 4.0)
- `Comparison_of_Adsorption_Capacity_of_Natural_and_Acid-activated_Kaolinite_Clay_..._7e3a1a3ed3.pdf`
  (J. Korean Soc. Environ. Eng. 2024, CC BY-NC 4.0)
- `Performance_of_molybdenum_vanadate_loaded_on_bentonite_..._8d56fcd23b.pdf`
  (Environmental Science and Pollution Research 2023, CC BY 4.0)

Five are left out because their files show no licence that allows
republishing them:

- Two Springer subscription articles whose extracted full text may not be
  redistributed:
  - `Effect_of_the_surface_hydration_..._451becad04.txt`
    (DOI 10.1007/s10450-020-00263-y)
  - `Adsorption_properties_of_cesium_by_natural_Na-bentonite_and_Ca-bentonite_91a1c20b01.txt`
    (DOI 10.1007/s10967-024-09627-y)
- Three sources removed on 2026-10-07, because their files state no
  licence (the Chiang Mai text ends with an "All Rights Reserved" notice):
  - `Differential_sorption_behavior_of_Cesium_depending_on_humic_acid_content_in_clay_minerals_ec9d65184a.pdf`
    (Goldschmidt 2023 conference abstract, DOI 10.7185/gold2023.17275)
  - `Insight_into_Adsorption_of_Cesium_Ion_in_Aqueous_Solution_Based_on_Inorganic_Modified_Bentonite_0adc4e6c6d.pdf`
    (Polish Journal of Environmental Studies 2023, DOI 10.15244/pjoes/158763;
    the journal's website says its articles carry CC BY-NC 4.0, so this
    one may come back with a credit line later)
  - `Sorption_Studies_of_Cesium_on_Kunipia-F_Bentonite_and_Silicon_Dioxide_1cc79421db.txt`
    (Chiang Mai Journal of Science,
    https://epg.science.cmu.ac.th/ejournal/journal-detail.php?id=11683)

The refs file `my_text.md.refs.txt` still maps all nine keys, so the
text's citation markers keep resolving. When a file is absent from
`sources/`, the cost estimate that `verify_my_text.py` prints before it
starts (and `--estimate` on its own) shows one `source file missing`
warning for each missing file the text cites. (The `claude-code`
backend prints no estimate, so there you see no such warning.) That is
expected, not breakage. The run completes, and each claim that cites a
missing file is shown as not checked against that file, instead of
being guessed.

To reproduce the full nine-source run, fetch the five missing texts
through your own access, save them under the exact file names above in
`sources/`, and re-run.

For a complete, works-out-of-the-box example use
`examples/chimpanzee_validation` — that is the one the README quickstart
walks through.
