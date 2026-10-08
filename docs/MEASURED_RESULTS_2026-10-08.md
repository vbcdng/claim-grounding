# How good is the claim checker? Measured results, October 2026

*Results as of 8 October 2026.*

## In short

- The claim checker reads a text and its sources. For each sentence with a citation, it tells you if the source really supports the sentence.
- In our largest test, the tool warned the reader about almost every sentence that people had marked as not supported. It never gave a plain "supported" to a sentence that had no support at all.
- When the tool makes an error, it is usually too strict, not too easy. It then asks you to check a sentence that is in fact correct.
- When we run the tool again on the same texts, it gives almost the same answers.
- We do not know yet if the answers change when we use a different language model, or when we change the words of its instructions. We are testing this now.

## What the tool does

Many texts cite sources. The claim checker checks if each source really says what the text says.

An example. A text says: "Patients with limited English stayed longer in hospital [1]." The tool opens source [1]. It finds the parts of the source that are about hospital stays. Then a language model reads the sentence and these parts. The model gives a verdict:

- "supported", if the source says that these patients stayed longer;
- "not supported", if the source does not say this.

A sentence without a citation gets the label "own claim". This is a sentence of the writer, for example a conclusion. The tool does not check it against a source.

The tool also shows warnings. A warning does not change the verdict. It tells the reader where to look more closely. Some examples:

- **"Partial support?"** The source supports the claim, but one part of the claim is in none of the cited sources.
- **"Not proven as written."** The source supports most of the claim, but the tool did not find a source sentence for each part of it.
- **"Figure not in source."** The claim contains a number that the tool did not find in the source.
- **"Citation needed?"** A sentence without a citation states a fact that should have a source.
- **The citation supports only a part.** Sometimes a citation supports only a method or a term in the sentence, and the rest of the sentence is the writer's own text. The tool shows these sentences in a separate group.
- **"Proof may exist?"** The tool said "not supported", but a second check found text in the source that may support the claim.

The tool shows each verdict next to the sentence, together with the sentences from the source that it used. So you can see why it gave the verdict, and you can check it yourself.

## Words used in this page

- **Claim.** A sentence in a text that cites a source.
- **Verdict.** The answer of the tool for one claim, for example "supported" or "not supported".
- **Judge.** The language model that gives the verdict. Today the judge is Gemma 4 from Google. It is free to use.
- **Correct answer.** Before a test, someone read each claim and its source and wrote down the correct verdict. In the public test sets, people did this. In our own test texts, language models did it, and the author checked some of their answers. In the test, we compare the verdict of the tool with this correct answer.
- **False support.** The most dangerous error. The source does not support the claim, but the tool says "supported". A reader then trusts a claim that has no support.
- **False alarm.** The opposite error. The source supports the claim, but the tool says "not supported". This error is less dangerous: the reader checks the claim and finds that it is correct.

## How correct are the correct answers?

In a test, we compare the tool with the answers of people. But people also make errors. So no tool can agree with the people on 100% of the sentences, even if the tool is always right.

**Other researchers found this too.** A study from 2021 (Northcutt and others) checked ten well-known test sets in machine learning. On average, about 3 in 100 of the answers in these sets were wrong.

**What this means for the results below.** When the tool does not agree with the people, the tool is sometimes right. We count every disagreement as an error of the tool anyway, so the results below do not make the tool look better than it is.

## Results for the current version of the tool

The current judge has been in use since 30 August 2026. These results are for the tool with this judge.

### Test 1: Wikipedia sentences

**What the test is.** WiCE is a public set of sentences from Wikipedia, made by other researchers. For each sentence, people read the cited source and gave one of three labels: "supported", "partially supported" or "not supported". "Partially supported" means that the source supports only some of what the sentence says.

**What we did.** In September 2026, we gave the tool 512 sentences from this set. We had never used these sentences to change the tool. For 186 of the 512 sentences, people had decided that the source does not support them.

**Why we count only two labels.** The people who made the set used three labels, but our tool gives only two verdicts: "supported" and "not supported". A reader needs to know one thing: can I trust this sentence as it is written? If a source supports only a part of a sentence, the sentence as written is not supported. So we count "partially supported" as "not supported".

An invented example. A sentence says: "The zoo opened in 1918 and is the largest zoo in the state." The source says that the zoo opened in 1918, but it says nothing about its size. People marked this sentence "partially supported". For a reader, the second half has no support, so the correct verdict for us is "not supported".

**The warning counts too.** Sometimes the tool says "supported", but adds the warning "partial support?" or "not proven as written". This tells the reader that a part of the sentence may have no support. So for each sentence, the tool gave one of three answers: "supported" with no warning, "supported" with a warning, or "not supported".

**Result 1: sentences that people marked "not supported" or "partially supported".** There were 401 such sentences (247 in the official test part and the 154 extra ones):

- The tool said "not supported" for 342 of them.
- It said "supported", but with a warning, for 44 of them.
- It said "supported" with no warning for 15 of them. All 15 were "partially supported" sentences. These are the dangerous errors: the reader gets no sign that a part of the sentence has no support.

For the 186 sentences that people marked "not supported" in full, the tool never said "supported" without a warning. For 4 of them, it said "supported" with a warning. In July 2026, an earlier version said "supported" for 3 of these sentences.

**Result 2: sentences that people marked "supported".** There were 111 such sentences, all in the official test part:

- The tool said "supported" with no warning for 66 of them.
- It said "supported", but with a warning, for 24 of them.
- It said "not supported" for 21 of them. These are false alarms: the reader checks the sentence and finds that it is correct.

**The agreement in percent.** On the official test part of 358 sentences, the answer of the tool agreed with the people on 298 sentences (83%). Here we count "supported, with a warning" as a doubt, because the warning tells the reader to check the sentence. If we count only the verdict and ignore the warning, the agreement is 282 of 358 (79%).

**What this means.** The tool rarely trusts a sentence that it should not trust: only 15 of 401 times, and never for a sentence with no support at all. But it often doubts a sentence that is correct: 45 of 111 times, as a "not supported" verdict or as a warning. So the tool is strict. It gives the reader more sentences to check, but it rarely lets an unsupported sentence pass without a sign.

This strictness fits well with text that an AI model wrote. You do not have to check each flagged sentence yourself. You can give the list of flagged sentences back to the AI model and ask it to rewrite them or to find a better source. Then you check the new text again. You repeat this until almost every sentence is "supported" with no warning. A strict checker costs you only one more round of rewriting. A lenient checker would let a wrong sentence stay in the text.

### Test 2: six texts we test before every change

**What the test is.** We have six texts with known correct answers. The six texts are different from each other:

- a long report about the future of artificial intelligence, with 81 sentences (44 of them with a citation) and 61 sources such as news articles, reports and research papers;
- a research paper from materials science;
- a short scientific comment about animal behaviour, with one source;
- three essays that an AI model wrote: one about forecasting, one about medieval history, and one about old folk customs.

Before we accept any change to the tool, we run the tool on all six texts. A change must not make any correct verdict wrong.

**Result.** On 5 October 2026, no correct verdict became wrong. Four claims in these texts get a wrong verdict, and they got it before as well. We keep these four claims in the test and show them in every result.

### Test 3: the number check

**What the check does.** This check uses no language model. An example: a claim says "37% of patients stayed longer [1]". The check looks for "37%" in source [1]. If it does not find the number, it shows a warning: "figure not in source".

**Result.** We looked at 133 earlier runs of the tool. They had 247 supported claims with numbers. The check gave no warning on any of them. So the check rarely gives a false warning. This result does not show how many wrong numbers the check finds.

### Two optional second opinions

The tool can ask a second, small model to read the claim and the source again. This small model runs on a normal computer and is free. Its answer is only a warning for the reader. It never changes the verdict.

- **A second opinion on "supported" verdicts (Granite Guardian, off by default).** People checked 122 "supported" verdicts. 28 of them were wrong. The second opinion gave a warning on 25 of these 28. It also gave a warning on 33 of the 94 verdicts that were correct.
- **A second opinion on "not supported" verdicts (HHEM).** This check is on by default in our working version, but it is not yet in this public repository. People checked 114 "not supported" verdicts. 22 of them were wrong. The second opinion gave a warning on 17 of these 22. It also gave a warning on 30 of the 92 verdicts that were correct.

So each second opinion finds most of the wrong verdicts. But it also gives a warning on about one in three correct verdicts.

## Results for earlier versions of the tool

We got these results before 30 August 2026. After that date, the judge and many parts of the tool changed. So these results are for an earlier version. We have not done these tests again yet.

**Citations in scientific papers (July 2026).** Citation-Integrity is a public set of sentences from scientific papers, made by other researchers. People marked each citation as correct or faulty. We gave the tool 100 sentences. With Gemma as the judge, the tool gave the same answer as the people for 71 of the 100. We tested five different judge setups, and this was the best result. The other four got 70, 70, 69 and 65. But this setup also accepted 3 faulty citations. No other setup accepted more, and one other setup accepted the same number. In this test, Gemma was a paid copy from another company, not the free Gemma from Google.

**50 claims from scientific papers (August 2026).** We had never used these claims to change the tool. The tool gave the correct verdict for 34 of the 50 (68%). There were 16 errors: 15 false alarms and 1 false support.

**236 difficult claims, read again by strong models (July 2026).** A strong language model (Claude Fable) read each claim and its source again. A second strong model (Claude Opus) checked its work. When they did not agree with the tool, the tool was usually too strict: too strict on 67 claims, and too easy on 6 claims.

**Three texts with checked answers (July 2026).** For these texts, language models read each cited source and decided the correct verdicts. We used the first text to adjust the tool. We did not use the other two. On the first text, the tool gave one false support in 44 claims. On the second text, all 12 verdicts were the same as the checked answers. On the third text, the verdicts were the same as in an earlier run.

## Where the tool makes errors

We collected the wrong verdicts from all our tests and sorted them by cause. A language model did this sorting. A person did not check each row. For 78 wrong verdicts, the cause was clear:

| Cause | Wrong verdicts |
|---|---|
| One small word carries the meaning, and the judge did not compare it: a number, a quantity word such as "most", a direction word such as "rose", or a name | 32 |
| The sentence is complicated: it has several claims, or it mixes the writer's own words with the cited fact | 24 |
| An error in the code or the display of the tool | 7 |
| The proof was in the source, but the tool did not give it to the judge | 5 |
| The citation supports only one item in a list, but the tool asked for proof of the whole list | 5 |
| Other causes | 5 |

All errors in the first group come from tests where we changed one word in a correct claim on purpose. An example: the claim says "rose", the source says "fell", and the judge says "supported". The sentences in these tests are simple. The judge reads the correct source sentence, but it does not compare the changed word.

The second group is the main cause in real texts. In the rows from real texts, 20 of the 42 wrong verdicts come from complicated sentences. An example: "The drug lowered blood pressure in older patients, which shows that it is safe for everyone." The source supports the first part. The second part is the opinion of the writer.

### What we did about the first group

We built a separate check for each kind of small word. The results are different for each kind:

- **Numbers.** A simple program, with no language model, looks for each number of a supported claim in the cited source. If it does not find the number, it shows a warning. This check is on by default. In our tests where we changed a number on purpose, it found wrong numbers that the judge had accepted. It gave no false warning on 247 earlier claims with numbers. But it does not find a wrong number that appears somewhere else in the same source. A stricter version compares the number with the proof sentence only. It found 2 more changed numbers that the simple version missed. This stricter version is built, but it is off by default.
- **Quantity words.** We added a step that compares words such as "some", "most" and "all". It corrected one real error in our six test texts: a source that supports a claim for "some" countries no longer counts as proof for "most" countries. We also tested it on 1,435 made-up pairs of a claim and a source sentence.
- **Direction words.** A check asks the judge one more question: does the source give the same direction ("rose" or "fell", "causes" or "is caused by")? In a test, it found one real error, but it also gave one false warning, and one more warning came from an error in our code. So this check is off until we correct that error.
- **Names.** We do not have a check for names yet.

We have not yet run all our tests again with these checks switched on. We will do this after the work on the second group is finished (see below). That work changes how the whole tool checks a sentence, so a test run before it would soon be out of date.

### What we do about the second group

For complicated sentences, we chose a different method. Before the check, the tool splits each sentence into short sentences in AIDA form. AIDA means that each short sentence is atomic (it says one thing), independent (it makes sense alone), declarative (it states a fact) and absolute (it states the claim plainly; how certain the claim is, for example "may" or "probably", is recorded separately). The tool then checks each short sentence against the source. The opinion of the writer becomes a separate short sentence, so the tool does not ask the source to prove it.

Take the example sentence from above again: "The drug lowered blood pressure in older patients, which shows that it is safe for everyone." The tool makes two short sentences from it:

- "The drug lowered blood pressure in older patients." The tool checks this sentence against the source.
- "The drug is safe for everyone." The tool marks this sentence as the writer's own conclusion.

We are now working on this method.

## Does the tool give the same answer every time?

**When nothing changes.** We run the six texts again before every change to the tool. So we have many repeated runs. In August 2026, we compared runs made 15 hours apart. Where the code of the tool was the same, both runs sent the same number of questions to the judge, with the same amount of text. In the longest text, 79 of 81 verdicts were the same. The 2 different verdicts were on claims that a change in the code between the runs affected. So when the version, the judge and the instructions stay the same, the tool gives almost the same verdicts. A few claims sometimes get a different verdict in a new run. We check these claims in every test.

One optional step gave different verdicts in different runs. We switched this step off on 1 September 2026.

**When something changes that should not matter.** A good checker gives the same verdict when:

- a different language model is the judge;
- the instructions to the judge use different words with the same meaning;
- the parts of the source come in a different order.

Our repeated tests do not change these three things. So we do not know yet if they change the verdicts. We have started a study that tests these three changes on the six texts. We will publish how many verdicts change, together with the results above.

## Free Gemma and paid Gemma

The same judge model, Gemma 4, is available in two ways:

- **Free**, directly from Google. It has a limit on how many questions you can ask per minute, so a large test takes many hours.
- **Paid**, from other companies that run a copy of the same model. A test is fast. One run of our six test texts costs about 0.60 to 1 US dollar (prices of August 2026).

Some paid companies run a **compressed copy** of the model. A compressed copy stores the model with less exact numbers. It is cheaper to run, but it can give slightly different answers.

In August 2026, we ran the same 100 scientific sentences (from Citation-Integrity) several times with each kind of Gemma. This was an earlier version of the tool. The results:

| Kind of Gemma | Correct verdicts | Same answer to the same question? |
|---|---|---|
| Free, from Google (two runs) | 62 of 100, both times | Yes, always |
| Paid, full model | 67 of 100 | The same verdict all 30 times in a small test with one question |
| Paid, seller not chosen (two runs; most likely a compressed copy) | 70 and 69 of 100 | No: within one run, about 4 in 10 repeated questions got a different answer text |

What this means:

- **In this test, the free Gemma from Google gave exactly the same answer every time.** This is why we use it for all our tests: when a result changes, we know that the cause is a change in the tool, not chance.
- **The paid full model was a little better.** It gave a different verdict on 5 of the 100 sentences, and all 5 of its verdicts were the correct ones. But 5 sentences is a small number, so this difference may be smaller or larger on other texts.
- **The compressed copy got the highest number, but it is not better.** It did not find more faulty citations than the free Gemma. It only objected less often. Also, it often gave different answers to the same question, so its results cannot be repeated.

All the results for the current version on this page use the free Gemma from Google. If you use the tool with a paid full model, the results can be a little better. We advise against compressed copies.

## What we do not know yet

- The results of the current version on the scientific citations and on the 50 scientific claims. We must do these tests again.
- How much the three changes above change the verdicts. The study has started.
- If a new method gives better verdicts. In this method, the tool first splits a long sentence into short sentences. Each short sentence must make sense alone. Then the tool checks each short sentence against the source. We are testing this method now.
