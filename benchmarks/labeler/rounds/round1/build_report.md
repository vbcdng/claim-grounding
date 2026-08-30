# Round-1 input rows — build report

This file describes `rows.jsonl` in the same folder: the 19 rows the labeling panel will judge in round 1, each carrying its claim, the paragraph around it, and the full text of its cited source. The list itself was frozen on 2026-08-07 in `docs/TASK15_LOOP.md`; this build only attaches the source texts.

| Row | Pile | Source file | Source length (characters) | Extraction quality |
|---|---|---|---|---|
| retreat:b008 | retreat_contested | sastry2024.pdf | 285,469 | ok |
| retreat:b014 | retreat_contested | agentic_dev.txt | 143,808 | ok |
| retreat:b039 | retreat_contested | data_reuse.txt | 35,815 | ok |
| retreat:b047 | retreat_contested | hukantaival2016.pdf | 1,036,530 | ok |
| retreat:b071 | retreat_contested | karger2023.pdf | 1,294,440 | ok |
| retreat:b081 | retreat_contested | draghi2024.pdf | 240,465 | ok |
| retreat:b086 | retreat_contested | Cesium_adsorption_desorption_behavior_of_clay_minerals_considering_actual_contamination_conditions_i_a973183290.pdf | 30,358 | ok |
| retreat:b089 | retreat_contested | walker2022.txt | 122,066 | ok |
| retreat:b094 | retreat_contested | lohn2026.pdf | 56,016 | ok |
| retreat:b117 | retreat_contested | kulveit2025.pdf | 101,170 | ok |
| retreat:b120 | retreat_contested | rodrguez2025.pdf | 63,929 | ok |
| retreat:b121 | retreat_contested | good1965.pdf | 156,968 | ok |
| retreat:b128 | retreat_contested | rosenberg2024.txt | 244,823 | ok |
| retreat:b132 | retreat_contested | bakker2026.pdf | 118,544 | ok |
| retreat:b137 | retreat_contested | veg_price.txt | 52,681 | ok |
| pilot100:cidev0060 | fair_misfire | cidev0060.txt | 57,711 | ok |
| pilot100:cidev0063 | fair_misfire | cidev0063.txt | 38,933 | ok |
| pilot100:cidev0072 | fair_misfire | cidev0072.txt | 7,441 | ok |
| pilot100:cidev0080 | fair_misfire | cidev0080.txt | 37,050 | ok |

No extraction problems found: every source came out long enough and readable.

Total source text across all 19 rows: 4,124,217 characters (roughly 1,031,054 tokens). Each panel model reads each row's source once, so one full panel pass over all rows sends roughly 1,031,054 tokens to each model.
