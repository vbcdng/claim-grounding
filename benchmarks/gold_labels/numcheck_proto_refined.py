import re, pathlib
SRC = pathlib.Path(__file__).resolve().parents[2] / "data/eggs/sources"

def present(fig, text):
    """Conservative: match the figure only in its SPECIFIC form.
    - integers/counts: allow comma/space/thin-space thousands separators
    - decimals (ratios): exact, dot or middot
    - percents: '<n>%' with optional space
    Never expand a ratio to a bare integer (that caused the false match)."""
    variants = []
    if re.fullmatch(r"\d{1,3}(,\d{3})+", fig):          # 29,615 -> allow sep variants
        digits = fig.replace(",", "")
        variants.append(re.escape(digits[:-3]) + r"[,\s  ]?" + re.escape(digits[-3:]))
    elif re.fullmatch(r"\d+%", fig):                      # 42%
        n = fig[:-1]; variants.append(re.escape(n) + r"\s*%")
    elif re.fullmatch(r"\d+\.\d+", fig):                 # 1.42 ratio (dot or middot), exact
        variants.append(re.escape(fig).replace(r"\.", r"[.·]"))
    else:
        variants.append(r"(?<!\d)" + re.escape(fig) + r"(?!\d)")
    for v in variants:
        if re.search(v, text):
            return True, v
    return False, None

TESTS = [
    ("t17 real RR 0.99","rong2013.txt","0.99","PRESENT"),
    ("t22 real n=29,615","zhong2019.txt","29,615","PRESENT"),
    ("t22 real HR 1.17","zhong2019.txt","1.17","PRESENT"),
    ("t29 real RR 1.42","shin2013.txt","1.42","PRESENT"),
    ("t30 real RR 1.54","rong2013.txt","1.54","PRESENT"),
    ("t29 real 42%","shin2013.txt","42%","PRESENT"),
    ("t29 MUT RR 1.22","shin2013.txt","1.22","ABSENT(flag)"),
    ("t30 MUT RR 2.54","rong2013.txt","2.54","ABSENT(flag)"),
    ("t22 MUT HR 1.70","zhong2019.txt","1.70","ABSENT(flag)"),
    ("t29 MUT 22%","shin2013.txt","22%","ABSENT(flag)"),
]
print(f"{'test':20} {'fig':8} {'expect':16} {'result':8} match")
ok=0
for label,src,fig,expect in TESTS:
    p=SRC/src
    found,via=present(fig,p.read_text(errors='ignore'))
    res="PRESENT" if found else "ABSENT"
    good=(found and expect=="PRESENT") or (not found and expect.startswith("ABSENT"))
    ok+=good
    print(f"{label:20} {fig:8} {expect:16} {res:8} {'OK' if good else 'XX'}")
print(f"\n{ok}/{len(TESTS)} behaved as the refined design predicts")
