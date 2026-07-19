# Input format contract

This tool consumes three things. Any producer (manual authoring, a future synthesis
project, another writing tool) just has to emit this shape.

## 1. The text — `my_text.md` (.md or .txt)
Plain prose. Mark each claim with one or more citation markers **after** the
sentence(s) that claim supports:

    Nasal irrigation reduces viral shedding. [[smith2020]]
    AGI may reshape labor markets and productivity. [[jones]] [[lee]]

- Marker syntax: `[[key]]`; key charset is `[A-Za-z0-9_-]`.
- Consecutive markers group onto the preceding sentence(s).
- A sentence with no marker gets the `own` verdict (the author's uncited claim —
  thesis, argument, transition; shown indigo, nothing is checked). A small
  default-on pass tags own claims structural / opinion / fact, and "fact" gets
  a dismissible "citation needed?" nudge.

## 2. References — how keys map to source files
Resolved in this precedence:
1. `--references <file>`
2. a sibling `my_text.md.refs.txt` next to the text file  ← default
3. a trailing `[References]` block inside the text

Format is one `key = filename` per line; lines starting with `#` are comments:

    smith2020 = smith_2020_nasal_sprays.pdf
    jones     = jones_agi_labor.txt

## 3. Sources — the `--sources <dir>` folder
Contains every file named in the references. `.pdf` (parsed with PyPDF2) and `.txt`
are supported.

## Output
`<output-dir>/analysis.json` (verdicts + evidence + coverage) and
`<output-dir>/viewer.html` (self-contained, opens with no server — see README).
