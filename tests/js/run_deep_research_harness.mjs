// Card 120: run the checked-research workflow script with fake helpers (no model, no web).
// Usage: node run_deep_research_harness.mjs <script.js> <scenario.json>
// Prints one JSON object: {result, calls: [{label, phase, model}], logs}.
// Scenario keys: args, angles (n), claimsPerSource, dieAfter (helpers that answer before
// every later helper returns null, as when the allowance runs out), spentPerCall, budgetTotal.
import { readFileSync } from "node:fs"

const [scriptPath, scenarioPath] = process.argv.slice(2)
const sc = JSON.parse(readFileSync(scenarioPath, "utf8"))
const src = readFileSync(scriptPath, "utf8").replace(/^export const meta\s*=/m, "const meta =")

const calls = []
const logs = []
let answered = 0
let spent = 0
const nAngles = sc.angles ?? 5
const agent = async (prompt, opts = {}) => {
  calls.push({ label: opts.label, phase: opts.phase || (opts.label === "scope" ? "Scope" : opts.label === "synthesize" ? "Synthesize" : ""), model: opts.model || null })
  if (sc.dieAfter != null && answered >= sc.dieAfter) return null
  answered++
  spent += sc.spentPerCall ?? 0
  const label = opts.label || ""
  if (label === "scope") {
    return { question: "q", summary: "s", angles: Array.from({ length: nAngles }, (_, i) => ({ label: "angle" + i, query: "query" + i, subquestion: "sub" + i })) }
  }
  if (label.startsWith("search:")) {
    const a = label.slice(7)
    return { results: Array.from({ length: 6 }, (_, j) => ({ url: "https://site" + a + ".org/p" + j, title: a + " paper " + j, relevance: j < 4 ? "high" : "medium" })) }
  }
  if (label.startsWith("fetch:")) {
    const m = prompt.match(/\*\*URL:\*\* (\S+)/)
    const n = sc.claimsPerSource ?? 5
    // every claim central + primary: the tie that let angles 1-2 take all 25 slots in wf_8611bd4f-248
    return { sourceQuality: "primary", claims: Array.from({ length: n }, (_, k) => ({ claim: "claim " + k + " from " + m[1], quote: "quote " + k, importance: "central" })) }
  }
  if (/^v\d+:/.test(label)) {
    return { refuted: false, evidence: "fine", confidence: "high" }
  }
  if (label === "synthesize") {
    return { summary: "synth", findings: [{ claim: "c", confidence: "high", sources: ["u"], evidence: "e" }], caveats: "none" }
  }
  throw new Error("unexpected label " + label)
}
const parallel = async thunks => Promise.all(thunks.map(t => Promise.resolve().then(t).catch(() => null)))
const pipeline = async (items, ...stages) => Promise.all(items.map(async (item, i) => {
  let v = item
  try {
    for (const s of stages) v = await s(v, item, i)
    return v
  } catch (e) { return null }
}))
const phase = () => {}
const log = m => logs.push(String(m))
const budget = {
  total: sc.budgetTotal ?? null,
  spent: () => spent,
  remaining: () => (sc.budgetTotal ? Math.max(0, sc.budgetTotal - spent) : Infinity),
}
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const run = new AsyncFunction("agent", "parallel", "pipeline", "phase", "log", "args", "budget", src)
const result = await run(agent, parallel, pipeline, phase, log, sc.args, budget)
process.stdout.write(JSON.stringify({ result, calls, logs }))
