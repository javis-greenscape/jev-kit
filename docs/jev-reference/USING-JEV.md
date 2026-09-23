# Using Jev, according to Jev's own documentation

Read on 2026-09-23 from the pages saved beside this file under `typesafe-docs/`. Every claim
here is quoted from one of them and cited by file. Nothing is from memory; where the docs do
not answer a question, this file says so rather than guessing.

The second half, [What jev-kit does differently](#what-jev-kit-does-differently), compares the
documented shape against `vendor/jev-ultrafast/jev_ultrafast/model.py`, `agent.py`,
`snapshot.js` and `browse/server.py`, and says for each mismatch whether it could explain the
failures the Wikipedia benchmark actually shows.

---

## 1. What Jev is

> "Jev is TypeSafe's flagship model and the first System One model."
> — `typesafe-docs/models.md`

> "It does not generate text, write code, or hold a conversation. It takes a state and a set
> of typed questions and returns structured answers your code can use directly."
> — `typesafe-docs/introduction_coding-agents.md`

> "Code handles deterministic work and owns the control flow. The model appears only where the
> system needs programmable common sense or needs to interpret unstructured data. Each AI task
> is kept atomic and constrained."
> — `typesafe-docs/concepts_how-to-build-with-system-one.md`

That is the whole design brief, and it is the first thing worth holding on to: Jev is not a
small agent. It is a classifier your control flow calls.

## 2. The request and response, field by field

### The request

> ```http
> POST https://api.typesafe.ai/v1/systemone
> Authorization: Bearer <API_KEY>
> Content-Type: application/json
> ```
> — `typesafe-docs/api.md`

Three top-level fields, all required:

| Field | Type | What the docs say |
|---|---|---|
| `state` | `string \| object \| array` | "The content to evaluate. A plain string for text, or structured data (object/array) for things like chat logs, records, or the current state of your application." (`api.md`) |
| `model` | `string` | "The model that handles the request. Use `\"jev-latest\"`, TypeSafe's flagship model." (`api.md`) |
| `questions` | `map<string, Question>` | "A map of typed Question objects. You choose each key; answers come back under the same keys." (`api.md`) |

**There is nothing else.** No system prompt, no conversation history, no temperature, no
per-request settings, no multi-action field, no tool definitions. The request schema in
`api.md` has exactly these three keys, and the only other documented endpoint is
`GET /v1/models` ("returns the names your account can send in the `model` field",
`typesafe-docs/models.md`).

So the answer to "what fields are we not using?" is: **at the top level, none. There are
none left to use.** Everything Jev can be told lives inside `state`, `instructions` and `criteria`.

> "Question IDs are for your code. They are not sent to the model."
> — `typesafe-docs/primitives.md`

> "The key is not sent to the underlying model and is not used in inference."
> — `typesafe-docs/api.md`

### The question types

All three share `type` and `instructions`; each adds its own `criteria` (`api.md`).

- **Choice**. "Picks one option from a set you define. Returns the chosen option and the full
  probability distribution." `criteria` is "A map of option to rubric description; use null
  when an option needs no extra detail. **You can have a maximum of 255 options per Choice.**"
  (`api.md`)
- **Score**. "Rates the state along a rubric you define." `criteria` is "An ordered array of
  level descriptions. A Score should have at least two levels; the API accepts up to 10."
  (`api.md`)
- **Noul**. "A yes/no question. Returns the probability the answer is yes." `criteria` is
  optional: "Optional descriptions of what a yes and a no mean." (`api.md`)

`instructions` and every `criteria` entry may be structured:

> "The `instructions` property can be a string, an object, or an array. You can break up a long
> question that has extra context, or data it needs to reference, into a structured object.
> Put the question in one field and the data in the others, and refer to the data fields by
> name in backticks."
> — `typesafe-docs/api.md`

### The response

> ```json
> {
>   "model": "jev-1.13.0",
>   "answers": { "is_urgent": { "type": "noul", "noul": 0.95 } },
>   "usage": { "input_tokens": 296, "output_tokens": 20 }
> }
> ```
> — `typesafe-docs/api.md`

A Choice answer carries `choice`, `probabilities` and `confidence`. `probabilities` is "Every
option mapped to its probability (floats that sum to 1)" and `confidence` is "How certain the
model is, derived from probabilities". A Score answer adds `legend`. Only `noul` comes back on
a Noul: "Choice and Score answers also carry a `confidence` … (Noul answers don't carry one.)"
Sources: `api.md` and `typesafe-docs/confidence.md`.

`confidence` is the field the docs spend a whole page on, and the one worth knowing we have:

> "`confidence` is a statistic computed from the probability distribution the answer already
> gives you. … The answer's `confidence` property collapses that shape into a single number
> from 0 to 1, so you can threshold on it without doing the math yourself."
> — `typesafe-docs/confidence.md`

> "**High confidence:** Act automatically. … **Medium confidence:** Proceed with caution. …
> **Low confidence:** Do not act. Route to a human, request clarification, or fall back to a
> different system. The model is telling you it does not have enough information or the
> question is not a good fit."
> — `typesafe-docs/confidence.md`

> "A confidence threshold is not one number. Different actions within the same system should be
> gated at different levels depending on the consequences of getting it wrong."
> — `typesafe-docs/confidence.md`

### Errors

> `401 Unauthorized` … `422 Unprocessable Entity` — "The request body failed validation — for
> example a missing required field or a malformed question." … `429 Too Many Requests` …
> `529 Overloaded`
> — `typesafe-docs/api.md`

> "When you receive a `429 Too Many Requests` or `529 Overloaded` response, retry the request
> with exponential backoff instead of retrying immediately."
> — `typesafe-docs/api.md`

## 3. How the docs say to word a question

This is the part with the most concrete guidance, and the part we match least.

> "Ask for a judgment a knowledgeable person makes in a second given the right context. 'Does
> this message convey urgency?' is a good question. 'Analyze this message and determine the
> best course of action' is not. That needs slow reasoning, and it is a signal to break the
> task into small questions and compose the answers in code."
> — `typesafe-docs/primitives.md`

> "`jev-1.13` answers the question you wrote, not the one you meant. Scoping words, negations,
> and implied conditions are read at face value. … **Instead:** state the exact condition in the
> `instructions`. Be specific. Put boundary cases in the criteria."
> — `typesafe-docs/model-jaggedness_jev-1.13.md`

> "Instructions carrying double negatives or complex indirection are answered less reliably. A
> question about a property of a property or something that requires multiple hops of reasoning
> costs accuracy. **Instead:** write your instructions as directly as possible. When possible,
> identify the relevant parts of state by name."
> — `typesafe-docs/model-jaggedness_jev-1.13.md`

> "When the `instructions` and the `criteria` ask for different things, `jev-1.13` might get
> confused. … **Instead:** treat the criteria as an extension of the instruction."
> — `typesafe-docs/model-jaggedness_jev-1.13.md`

And the list the jaggedness page closes on:

> "**As a reminder, avoid the following:** Asking the model something code can compute exactly.
> Hiding several judgments inside one question. System Two tasks: more layers of indirections.
> Giving it more context in `state` than the question needs. Jev suffers from context rot, so
> unrelated material in the `state` costs you accuracy."
> — `typesafe-docs/model-jaggedness_jev-1.13.md`

For Choice specifically:

> "Give the full list of options, and add an `other` or `none of the above` option when the list
> might not cover every input."
> — `typesafe-docs/primitives.md`

> "The option names and their descriptions are both sent to the model, so write descriptions
> that separate the options from each other."
> — `typesafe-docs/primitives_choice.md`

> "Start with a one-line description per option. When two options are similar and the model
> keeps confusing them, describe each one with an object instead of a string. Give it fields for
> what the option covers, what belongs to a neighboring option instead, and a few example
> inputs."
> — `typesafe-docs/primitives_choice.md`

## 4. How much state to send, and in what shape

> "Use an object for most requests so each part of the state has a descriptive name and its
> relationships remain clear."
> — `typesafe-docs/concepts_state.md`

> "Accuracy falls as the state grows with content unrelated to the decision. Unrelated detail
> acts as a distractor, and a large state makes it harder to tell which part of the input
> produced a wrong answer. **Instead:** retrieve and filter in code first, and send only the
> fields the question needs."
> — `typesafe-docs/model-jaggedness_jev-1.13.md`

> "Include only the context relevant to the current questions. This helps the model avoid
> distractions and context rot."
> — `typesafe-docs/concepts_how-to-build-with-system-one.md`

The hard limits:

> "Context length | 64k tokens per request; 32k tokens for `state` plus the longest question"
> — `typesafe-docs/models.md`

> "Jev ingests the `state` once and evaluates every question against it in parallel. The 64k
> budget covers the `state` plus all questions combined; the 32k budget applies to the `state`
> plus the single longest question."
> — `typesafe-docs/models.md`

**The docs do not prescribe an element-list format for browser automation.** There is no page
about web pages, no recommended row shape and no recommended table size. The nearest things
are the state-shape guidance above, and the line-id pattern in
`typesafe-docs/cookbooks_semantic_find.md`, where a document is offered to one Choice question
as numbered lines. That is the same idea as an indexed element table, and evidence that
indexing a long list into a Choice is a shape TypeSafe itself publishes.

## 5. Asking more than one question at a time

This is the pattern the docs push hardest, and it is the one thing the vendored agent already
does well.

> "Send every question that uses the same state in one request. You can mix question types
> freely. System One models evaluate every question in a request in parallel. Adding questions
> barely changes the response time and costs only the tokens for the extra questions, which are
> cheap. **Asking a question you might not need is close to free.**"
> — `typesafe-docs/primitives.md`

> "Ask every question your code might need, including ones whose answer only matters for some
> inputs, and let the code decide which answers to use. … The Parallel questions cookbook shows
> how batching 13 questions into one call is 11.5x cheaper and 9.6x faster than 13 separate
> calls, with no change in the answers."
> — `typesafe-docs/primitives.md`

> "Questions in the same request are independent: one answer does not become context for
> another question. If a later judgment depends on an earlier answer, make a second request in
> code."
> — `typesafe-docs/primitives.md`

## 6. Pairing Jev with another model

The docs describe the pairing only in the negative, and only about coding agents:

> "Jev is **not** a drop-in replacement for the LLM behind Claude Code, Cursor, opencode,
> Copilot … Instead, you can use your coding agent as usual to write code that uses Jev to make
> decisions."
> — `typesafe-docs/introduction_coding-agents.md`

> "There is no `model: \"jev-latest\"` setting that turns your coding agent into a Jev-powered
> agent, because the two systems solve different problems."
> — `typesafe-docs/introduction_coding-agents.md`

> "`jev-1.13` is not trained to generate text. While you can force it to by chaining choices,
> this will not work well and will be very slow. … **Instead:** when the answer space is
> bounded, turn extraction into a Choice over the options rather than asking for the value
> itself."
> — `typesafe-docs/model-jaggedness_jev-1.13.md`

> "Classify incoming requests and route each to the optimal handler: deterministic logic, a
> specialist LLM, or a human."
> — `typesafe-docs/patterns_intent-routing.md` (page summary)

So: **the docs never describe a planner model in front of Jev.** They describe the opposite
arrangement, where code or an LLM on the outside calls Jev for one narrow decision at a time.
They also warn against agent loops in general:

> "Keep deterministic work in code. It is reliable and cheap. Avoid agent `while` loops when a
> software workflow can express the same behavior."
> — `typesafe-docs/concepts_how-to-build-with-system-one.md`

A planner naming the next link and Jev executing it is a supported arrangement in the docs'
own terms: code owns the control flow and Jev makes one narrow decision. It is not a
recommended one, because nothing in the documentation recommends it at all. That reading is
why the planner in this repo was measured before it shipped, and ships as an opt-in. See
[Where the planner lives](#where-the-planner-lives).

## 7. Published latency, accuracy and the conditions behind them

**TypeSafe's own numbers.**

> "**Fast** — Most queries complete in about 100 ms. System One is fast enough for real-time
> request paths and user interfaces."
> — `typesafe-docs/concepts_how-to-build-with-system-one.md`

No workload, hardware or percentile is attached to that figure anywhere in the saved pages, so
it is a claim about typical requests and nothing more.

> "Price (per Btok / per Mtok) | \$42 / \$0.042" … "Charged per input token. Output tokens are
> free."
> — `typesafe-docs/models.md`

> "Rate limits | 250,000 tokens per second / 1,200 requests per minute" … "**Rate limits are
> adjusting dynamically.** We are serving a very large volume of demand, and the limits above
> can change without notice."
> — `typesafe-docs/models.md`

TypeSafe publishes **no accuracy number for Jev as a model.** The docs publish accuracy for
specific cookbook workloads instead. One example is "raise top-1 accuracy from 5% to 18% and
top-10 accuracy from 38% to 62%" for the re-ranking cookbook (`typesafe-docs/llms.txt` page
summary). There is no browser-agent benchmark.

> "English is the primary training language and where accuracy is currently best. Other
> languages, including CJK scripts, are handled but not equally well."
> — `typesafe-docs/models.md`

**Browser Use's numbers** (`vendor/jev-ultrafast/docs/performance.md`, which is byte-identical
to the upstream repo's copy as of 2026-09-23):

> "The recording contains **17 Jev requests**, **10 interactions plus one explicit WAIT**, and
> **two helper calls**. Median Jev latency was **178 ms**."

> "Three pairs are too few for a strong statistical claim (two-sided sign-test p = 0.25). This
> is a small controlled-input comparison, not a broad agent benchmark."

> "Wikipedia: open Gödel's incompleteness theorems | 2.798 s | Exact article URL"

Those conditions matter: one task, one profile, six alternating runs, initial navigation
excluded, `jev-1.13.0`, `inception/mercury-2.5` as the text helper with reasoning disabled.

## 8. Known limits, as the vendor lists them

`typesafe-docs/model-jaggedness_jev-1.13.md` names nine, reviewed 2026-09-17:

| # | Failure mode | The doc's own remedy |
|---|---|---|
| 1 | Literal reading | "Write the exact condition, criteria for each available options" |
| 2 | Math and numbers | "Keep the arithmetic in code" |
| 3 | Date and time comparison | "Extract components; compare in code" |
| 4 | Indirection | "Reduce hops; point to the relevant state" |
| 5 | Large state full of irrelevant detail | "Filter first; send only what the question needs" |
| 6 | Adversarial content | "Write precise prompts, and test edge cases before deploying" |
| 7 | Contradictory instructions and criteria | "Align the criteria and instruction" |
| 8 | Common-sense structural invariants | "Ask each decision one way; enforce identities in code" |
| 9 | Generation | "Use a generative model" |

Two of them bear directly on an element table:

> "`jev-1.13` does not count reliably. This covers characters in a word, occurrences of a term
> in a passage, and items in a long list. The model recognizes the shape of an answer rather
> than tallying, and the error grows with the size of the thing being counted."

> "`jev-1.13` will perform better on semantic representations than numeric. For example,
> questions about colors using hex values will underperform compared to those using the English
> names."

And one bears on treating a page as input:

> "State is data, and `jev-1.13` does not treat it as hostile by default. Content written to
> adversarially steer the model … can move the answer."

---

# What jev-kit does differently

Against `vendor/jev-ultrafast/jev_ultrafast/model.py` (`choose()`, `action_space()`),
`agent.py` (`Agent.command`), `jev_ultrafast/snapshot.js` and `browse/server.py`.

## What we already do the way the docs ask

- **One request, many questions.** `choose()` sends the operation head and every target head in
  a single call, which is exactly the speculative fan-out the docs push (`primitives.md`: "Asking a
  question you might not need is close to free"). `model.py` even says so: "Two decisions, one
  network round trip."
- **Structured instructions.** Both heads pass `instructions` as an object
  (`{"goal": ..., "rules": ...}`), which is the documented structured form.
- **Backoff on the documented statuses.** `post_json()` retries 429/529 (and 503) with
  exponential backoff, which is what `api.md` asks for.
- **`jev-latest` by default**, the alias `models.md` recommends.
- **Constrained answers validated in code.** `validate_choice()` refuses an answer naming an
  option we did not offer. The docs never ask for that; it is stricter than documented and
  should stay.

## The mismatches

### M1. A Choice could be offered more than 255 options *(fixed in this branch)*

> "You can have a maximum of 255 options per Choice." — `api.md`

`snapshot.js` caps the element table at `LIMIT=250`, which keeps CLICK and TYPE_TEXT inside
the ceiling. But `action_space()` emits **one SELECT target per observed dropdown option**, so
a single long country picker is already at the limit and two are past it. The result is a 422 and
`RuntimeError("Model provider returned HTTP 422; no action executed.")`, and the run dies.

Fixed on this branch: `model.cap_targets()` trims every head to 255 and reports what it
dropped. Tests in `vendor/jev-ultrafast/tests/test_agent.py`.

**Does it explain our Wikipedia failures?** No. Those pages have no `<select>`. This is a
latent crash, not the cause of a wrong click.

### M2. Choice options were named by number, not by meaning *(fixed in this branch)*

> "The option names and their descriptions are both sent to the model, so write descriptions
> that separate the options from each other." — `primitives_choice.md`

> "`jev-1.13` will perform better on semantic representations than numeric."
> — `model-jaggedness_jev-1.13.md`

Our target heads are keyed by the element index as a bare string:

```python
questions[operation.lower() + "_target"] = {
    "type": "choice",
    "criteria": {index: {"element": f"[{index}] {a['label']}", ...} for index, a in candidates.items()},
}
```

Every option **name** Jev sees is `"1"`, `"2"`, … `"137"`. Those names separate nothing; the
meaning is only in the description. On a Wikipedia article that is a 250-way Choice between
250 numbers, and the docs' two warnings (numeric representations, and "items in a long list")
both land on it.

**Does it explain our failures?** It is the most plausible single explanation for all four.
The image-caption click, the table-of-contents click, and the Feynman miss on the laureates
list are all "picked a neighbouring row". **Not fixed here.** Changing the option names changes
the `probabilities` keys, `validate_choice`, and the index→action mapping, and it needs an A/B
sweep to justify, not an assertion. Reported, not built.

**Now:** each option is named `role: label` (`link: Bicycle wheel`), a repeated name gets
` (2)`, ` (3)` in table order, names are capped at 80 characters, and they map back to the
element in code (`model.element_names`, `target_names`). The state's element table carries the
same names instead of indices.

### M3. The state carried 6,000 characters of body text on every decision *(fixed in this branch)*

> "Accuracy falls as the state grows with content unrelated to the decision. Unrelated detail
> acts as a distractor." … "Jev suffers from context rot, so unrelated material in the `state`
> costs you accuracy." — `model-jaggedness_jev-1.13.md`

`choose()` sends `state.page.text`, which `snapshot.js` fills with up to 6,000 characters of
visible text, on **every** decision, alongside up to 250 element rows and ten recent actions.
For "which of these elements do I click next", the article prose is exactly the unrelated
material the doc warns about. Note the project has already made this trade for the *text
helper*: `field_context()` defaults to a `"trimmed"` shape and its docstring says dropping
"~6000 chars of page text is most of why this shape is fast". The same switch was never applied
to Jev's own state.

**Does it explain our failures?** Plausibly, and it is cheap to test. **Not fixed here**: it is
a measurement, not a defect. Page text is what lets DONE be judged at all, so removing it
blindly would trade one failure mode for another. Reported.

**Now:** `JEV_PAGE_TEXT_CHARS`, default 1,500. The docs give no number, and 0, 1,500 and
6,000 tied on a measured subset; see SPIKE-NOTES.md, "Page text in Jev's state".

### M4. One question hid eight judgments *(fixed in this branch)*

> "Ask for a judgment a knowledgeable person makes in a second given the right context.
> … 'Analyze this message and determine the best course of action' is not." — `primitives.md`

> "Hiding several judgments inside one question." — the jaggedness page's list of things to
> avoid

`questions.NEXT_ACTION` is an eleven-line rulebook. It covers autocomplete, date pickers,
filter state, checkbox state, search submission, when WAIT is allowed, what DONE requires and
what BLOCKED means. That is a System Two prompt handed to a System One model. It is also
passed *twice*: once as the operation head's rules, and again inside every target head's
rules, where most of it is about choosing an operation the target head is not choosing.

**Does it explain our failures?** Partly. "Giving up instead of scrolling" is a DONE/BLOCKED
decision made inside that rulebook. Splitting it (a Noul for "the task's final page is open", a
Noul for "the next thing the goal asks for is off screen", combined in code) is the documented
shape. **Not fixed here**: it is a redesign of the policy, not a small change. Reported.

**Now:** the rulebook is split into `OPERATION` (which kind of step) and `TARGET` (which
element, for one named operation), and each head gets only its own. Two Noul questions ride in
the same single request, each one glance-sized judgment: `final_page` ("is the page open now
the one the goal ends on?") and `needed_off_screen` ("is the next element the goal needs off
screen?"). Code combines them with the operation (`model.combine`): a DONE the final-page Noul
doubts is gated low, and a BLOCKED while the needed element is off screen scrolls instead.
Everything is still one request, as section 5 recommends.

### M5. `confidence` came back on every decision and gated nothing *(fixed in this branch)*

> "**Low confidence:** Do not act. Route to a human, request clarification, or fall back to a
> different system." … "Different actions within the same system should be gated at different
> levels depending on the consequences of getting it wrong." — `confidence.md`

`choose()` returns `confidence` and `target_confidence`; `Agent.command("act")` reads neither.
A DONE at confidence 0.2 ends the run exactly as firmly as a DONE at 0.99. Ending the run is
the most consequential thing the loop can do, and the one action with no recovery.

**Does it explain our failures?** Directly, for "giving up instead of scrolling": that is a
low-stakes-looking decision the docs would have us gate hardest. **Not fixed here**: a
fallback needs a threshold, and a threshold picked without measurement is a guess. Reported,
with the note that this is the cheapest of the four to try: the number is already in the
response.

**Now:** a DONE below 0.9 or a BLOCKED below 0.5 re-observes the page and asks once more;
the second answer stands. 0.9 is set from recorded DONE confidences (SPIKE-NOTES.md,
"Confidence gate on DONE and BLOCKED"), 0.5 is the documented floor.

### M6. The element table is re-indexed on every observation

The docs do not discuss this, so it is not a documented mismatch, but it interacts with M2.
`action_space()` numbers elements 1..N by first appearance in *this* snapshot, so "[7]" means a
different element on every page and often after every scroll, while `recent_actions` in the
state refers to actions by label. Nothing in the docs says identifiers must be stable; noted
because any fix to M2 should not make it worse.

### M7. Small things

- `TYPESAFE_MODEL` defaults to the alias `jev-latest`. `models.md`: "If you have tuned
  confidence thresholds against a specific version, pin that version's ID instead of the alias."
  We have tuned nothing, so the alias is correct today; it stops being correct the moment M5 is
  acted on.
- `GET /v1/models` is never called. Nothing needs it.
- The docs' own agent skill (`agent-skill.md`) is not installed in this repo. Its stated job is
  "so they can generate correct TypeSafe integrations for you". Worth having if anyone writes
  new TypeSafe calls here, and out of scope for a fix.

## Where the planner lives

The planner started as an arm of the benchmark, on the reasoning that a shape the vendor does
not describe should be measured before it ships. It has been measured (README, "Measured
results"), and it now ships as `browse`'s opt-in `plan: true`
(`vendor/jev-ultrafast/jev_ultrafast/planner.py`). The earlier objections are answered in how
it is offered rather than by dropping it:

1. It stays in the docs' own terms: code owns the loop, and Jev makes one narrow decision per
   step. The planner never touches the browser.
2. It is opt-in. Plain `browse` is still "Jev chooses each click and keystroke", and is still
   the default. The tool description and R11's text say when to plan (open-ended tasks), that
   it is slower, and that it bills the user's own `claude` login.
3. The benchmark runs the arm through the tool itself, so the numbers describe what a caller
   gets.

The planner's `FIND <words>` verb answers the failure the first measurement showed: a link far
below the fold never reached the ranked candidates, and the planner had no way to ask for more.
