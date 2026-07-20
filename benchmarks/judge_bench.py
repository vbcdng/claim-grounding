# Judgment-quality benchmark from the paper1 hand audit (ground truth:
# benchmarks/paper1_ground_truth.json; run history lives in the dev repo).
# Usage: venv/bin/python3 benchmarks/judge_bench.py <a|b|c|MODEL>   (LIVE API: ~$0.005-0.02/run)
#   a = flash-lite, prompt file as-is | b = + entailment addon | c = gemini-2.5-flash + addon
#   MODEL = any litellm string (e.g. deepseek/deepseek-v4-flash) with the production prompt;
#           the key is read from config/<provider>_api_key.txt if present, else the
#           provider's env var (DEEPSEEK_API_KEY, OPENAI_API_KEY, ...).
# NOTE: variants b/c re-add the addon; the addon text is now MERGED into the prompt files,
# so variant 'a' reflects the current production configuration.
"""Judgment benchmark from the paper1 audit ground truth. Judgment-only calls (~700
tokens each) — no extraction, no decomposition. Cost per config: ~ $0.005."""
import sys, json, os
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from modules.papertrail.llm_client import LLMClient
from modules.papertrail.matcher import _parse_support

# (id, expected_supported, claim, passage) — passages are the auditor-verified
# supporting quotes (expected True) or the best available passage for claims whose
# key assertion is genuinely absent (expected False).
CASES = [
 ("t28", True,
  "It is also improving over time: each successive model generation has been rated more persuasive than the last, though gains from sheer scale alone appear to be levelling off.",
  "From anthropic2024: We also observed a general scaling trend across model generations: each successive model generation is rated to be more persuasive than the previous.\n\nFrom hackenburg2025: First, we find evidence that model persuasiveness is characterized by sharply diminishing returns, such that current frontier models are only slightly more persuasive than models smaller in size by an order of magnitude or more."),
 ("t49", True,
  "And the collapse can be quick. When Venezuela's oil revenues fell by more than nine-tenths after 2014, undernourishment rose from about 3 percent to over 27 percent of the population within seven years, without war or blockade.",
  "I show evidence supporting the hypothesis that the key driver of the decrease in caloric intake was the decline of more than nine-tenths in oil revenues, which sparked an economic contraction and forced the economy to undertake massive cuts in imports of food and agricultural inputs. The indicator shows a staggering increase, with undernourishment rates rising from 3.2 percent of the population in 2012 to 27.4% in 2019."),
 ("t10", True,
  "The United Nations Development Programme describes the broader risk as a possible \"next great divergence\" in economic performance between countries rather than only within them.",
  "From the UNDP report 'The Next Great Divergence: Why AI May Widen Inequality Between Countries' (United Nations Development Programme, 2025): THE NEXT GREAT DIVERGENCE Why AI May Widen Inequality Between Countries. As existing gaps and centrifugal forces combine to drive nations apart, AI may spark a Next Great Divergence. FROM Inequality within countries TO Inequality between countries."),
 ("t35", True,
  "Such concentration could arrive by either of two routes. It might come suddenly, through recursive self-improvement — an \"intelligence explosion\" in which machine intelligence rapidly outpaces human ability.",
  "Since the design of machines is one of these intellectual activities, an ultra-intelligent machine could design even better machines; there would then unquestionably be an 'intelligence explosion,' and the intelligence of man would be left far behind."),
 ("t76", True,
  "A related objection runs the other way: that abundance, not scarcity, makes the map moot. If AI drives the cost of necessities like energy, food, housing and medicine towards zero, as optimists such as Altman argue, then losing the economic race would not matter much.",
  "From 'Moore's Law for Everything' by Sam Altman (2021): AI will lower the cost of goods and services, because labor is the driving cost at many levels of the supply chain. Imagine a world where, for decades, everything - housing, education, food, clothing, etc. - became half as expensive every two years. The price of many kinds of labor (which drives the costs of goods and services) will fall toward zero once sufficiently powerful AI 'joins the workforce.'"),
 ("t27", True,
  "The same non-coercive leverage extends from physical assets to the political environment in which they are governed. In controlled experiments, a frontier model given only minimal personal information was more persuasive than human debaters in about two-thirds of decided exchanges, raising the odds of shifting a person's stated position by roughly 80 percent.",
  "In debate pairs where AI and humans were not equally persuasive, GPT-4 with personalization was more persuasive 64.4% of the time (81.2% relative increase in odds of higher post-debate agreement; 95% confidence interval [+26.0%, +160.7%], P < 0.01; N = 900). Intuitively, this means that 64.4% of the time, personalized LLM debaters were more persuasive than humans, given that they were not equally persuasive."),
 ("t68", True,
  "The analysis assumes that physical location, materials, and built capacity remain scarce, so that controlling them matters. Korinek and Suh's growth scenarios make output depend on exactly such fixed factors, and Erdil and Besiroglu treat land, energy and capital as the bottlenecks that persist even when intelligence is abundant.",
  "From korinek2023: In time, however, land becomes a binding constraint. The share of output received as land rents approaches 1, and the absolute wage falls to 0.\n\nFrom erdil2023: However, high confidence in this outcome is unwarranted, given current uncertainties about the intensity of regulatory responses to AI, potential production bottlenecks from hard-to-quickly-accumulate inputs such as land, energy, and capital, the economic value of superhuman abilities, and the rate at which AI automation could occur."),
 # expected FALSE — overstatements that must stay unsupported
 ("t23f", False,
  "European banks were refused access to the model; regulators asked for access to evaluate it and were turned down.",
  "European financial institutions currently lack access to the model. Regulators are calling for access to evaluate systemic implications, and officials have suggested formal requests may follow."),
 ("t30f", False,
  "The campaign's actual effect on votes was never established.",
  "The December 2024 annulment followed allegations of a coordinated campaign on TikTok and Telegram. Alongside any impacts from digital manipulation, Georgescu's victory also reflects genuine voter dissatisfaction."),
 ("t37f", False,
  "States funded by resource rents rather than taxes are less accountable to their citizens and invest less in them.",
  "When governments derive sufficient revenues from the sale of oil, they are likely to tax their populations less heavily or not at all, and the public in turn will be less likely to demand accountability from - and representation in - their government. With virtually no taxes, citizens are far less demanding in terms of political participation."),
 ("tinyf", False,
  "The programme eliminated sick days entirely among those who completed it.",
  "Participants who completed the programme recorded 23 percent fewer sick days than the control group over the study period."),
]

ENTAILMENT_ADDON = """
Paraphrase and entailment count as support: the passage does NOT need the claim's exact wording. If the passage's content, restated plainly, asserts the same fact (e.g. "sharply diminishing returns from scale" supports "gains from scale are levelling off"; "64.4% of the time" supports "about two-thirds"; a report titled and themed "X" supports "the report describes X"), answer true. Only answer false when a substantive fact, number, or qualifier of the claim is genuinely absent or contradicted.
"""



def key_for(model):
    """config/<provider>_api_key.txt if it exists, else None (LLMClient falls back
    to the provider's env var). Gemini keeps its historical filename."""
    provider = model.split("/")[0]
    fname = "google_api_key.txt" if provider == "gemini" else f"{provider}_api_key.txt"
    path = os.path.join(REPO, "config", fname)
    return path if os.path.exists(path) else None


# candidate fix for the t27 blind spot (stable 0/4 false): the claim's opening
# thesis sentence gets treated as an unsupported citable assertion
FRAMING_ADDON = """
When a claim OPENS with the writer's own thesis, bridge, or interpretation ("The same leverage extends to...", "The mechanism is not speculative...") and then gives specific evidence - an experiment, a statistic, a study result - that opening sentence is the writer's voice: do not require the passage to state it, and judge the specific evidence instead. This exemption is ONLY for interpretive framing. It never applies to sentences asserting concrete events, actions, or outcomes (an order was given, access was refused, something was shut down, a study was conducted) - every such event sentence must itself be supported by the passage.
"""


def run(model, prompt_path, addon=""):
    with open(prompt_path) as f:
        template = f.read()
    if addon:
        template = template.replace("Return ONLY a JSON object", addon.strip() + "\n\nReturn ONLY a JSON object")
    llm = LLMClient(model=model, api_key=key_for(model))
    results = {}
    for cid, expected, claim, passage in CASES:
        raw = llm.call(template.replace("{CLAIM}", claim).replace("{PASSAGE}", passage),
                       temperature=0.0, max_output_tokens=2048)
        got, reason = _parse_support(raw)
        results[cid] = (expected, got, reason[:70])
    ok = sum(1 for e, g, _ in results.values() if e == g)
    print(f"\n### {model} {'+entailment-addon' if addon else '(current prompt)'}: {ok}/{len(CASES)}")
    for cid, (e, g, r) in results.items():
        mark = 'OK ' if e == g else 'MISS'
        print(f"  {mark} {cid}: expected={e} got={g} | {r}")

P = os.path.join(REPO, 'config/prompts/pt_combined_judgment_prompt.txt')
import sys
which = sys.argv[1]
if which == 'a': run("gemini/gemini-2.5-flash-lite", P)
elif which == 'b': run("gemini/gemini-2.5-flash-lite", P, ENTAILMENT_ADDON)
elif which == 'c': run("gemini/gemini-2.5-flash", P, ENTAILMENT_ADDON)
elif which == 'd': run("gemini/gemini-2.5-flash-lite", P, FRAMING_ADDON)
else: run(which, P)  # any litellm model string, production prompt as-is
