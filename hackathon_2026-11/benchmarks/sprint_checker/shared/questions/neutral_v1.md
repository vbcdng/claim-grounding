<!-- Card S1 (card 395): the neutral question of the plan page, asked to every step that writes text.
records.render() fills it: {{name}} is a field; a block {{#name}} ... {{/name}} appears only when the
field set holds that field and the piece has a value for it; {{works}} is every cited work, numbered.
This comment is removed before the prompt is built. Frozen with all question files on Saturday 22:00. -->
You check one statement against the work it cites.

STATEMENT:
{{piece}}
{{#earlier_sentence}}

THE SENTENCE BEFORE IT IN THE WRITER'S TEXT (only to show who and what is meant):
{{earlier_sentence}}
{{/earlier_sentence}}
{{#original_sentence}}

THE WRITER'S WHOLE SENTENCE (the statement was cut out of it; judge only the statement):
{{original_sentence}}
{{/original_sentence}}
{{#certainty}}

HOW SURE THE WRITER WAS: {{certainty}}
(stated = the writer said it as a fact. likely or possible = the writer said it may be so. The statement is supported when the cited work says it at least as strongly as the writer did.)
{{/certainty}}

{{works}}

QUESTIONS:
1. Is the statement supported by the cited work, not supported, or are you unsure?
2. Copy the exact sentence of the cited work that your answer rests on.
3. If not supported, which is it: the work does not say it, the work differs in a detail (name the detail), or the work says the opposite?

Reply with JSON only, in this shape:
{"answer": "supported" or "not supported" or "unsure", "quote": "<the exact sentence, copied>", "reason": "not in the work" or "differs in a detail" or "contradicted" or null, "detail": "<the differing detail, or empty>"}
