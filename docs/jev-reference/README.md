# Jev reference

Primary sources on Jev, TypeSafe's System One model, which decides every step `browse` takes.
Fetched on **2026-09-23** and kept here so a claim about how Jev is meant to be used can be
checked against the vendor's own words rather than recalled.

**Start with [`USING-JEV.md`](USING-JEV.md).** It is the read-through: how Jev is meant to be
used according to these pages, every claim quoted and cited. Then a comparison against what
this repo actually sends, and whether each difference could explain a benchmark failure.

## How these were fetched

Every page under `typesafe-docs/` is the vendor's own Markdown rendering, served for exactly
this purpose. `https://docs.typesafe.ai/llms.txt` advertises it:

> "Fetch the complete documentation index at: https://docs.typesafe.ai/llms.txt. Use this file
> to discover all available pages before exploring further."

Each page is available at its URL with `.md` appended, and each file here is that response
verbatim with a two-line header giving the URL and the fetch date. Nothing has been edited,
summarised or reordered.

The set below is every page in `llms.txt` that bears on how a request is built, plus the
cookbooks that show a long indexed list being offered to one question. The rest of `llms.txt`
is SDK class references, one file per exception type and interface, and cookbooks about
unrelated domains. Those were listed, read for relevance, and not saved.

## What is here

| File | Source |
|---|---|
| [`api.md`](typesafe-docs/api.md) | https://docs.typesafe.ai/api.md |
| [`models.md`](typesafe-docs/models.md) | https://docs.typesafe.ai/models.md |
| [`model-jaggedness_jev-1.13.md`](typesafe-docs/model-jaggedness_jev-1.13.md) | https://docs.typesafe.ai/model-jaggedness/jev-1.13.md |
| [`confidence.md`](typesafe-docs/confidence.md) | https://docs.typesafe.ai/confidence.md |
| [`concepts_state.md`](typesafe-docs/concepts_state.md) | https://docs.typesafe.ai/concepts/state.md |
| [`concepts_system-one.md`](typesafe-docs/concepts_system-one.md) | https://docs.typesafe.ai/concepts/system-one.md |
| [`concepts_how-to-build-with-system-one.md`](typesafe-docs/concepts_how-to-build-with-system-one.md) | https://docs.typesafe.ai/concepts/how-to-build-with-system-one.md |
| [`primitives.md`](typesafe-docs/primitives.md) | https://docs.typesafe.ai/primitives.md |
| [`primitives_choice.md`](typesafe-docs/primitives_choice.md) | https://docs.typesafe.ai/primitives/choice.md |
| [`primitives_score.md`](typesafe-docs/primitives_score.md) | https://docs.typesafe.ai/primitives/score.md |
| [`primitives_noul.md`](typesafe-docs/primitives_noul.md) | https://docs.typesafe.ai/primitives/noul.md |
| [`primitives_advanced.md`](typesafe-docs/primitives_advanced.md) | https://docs.typesafe.ai/primitives/advanced.md |
| [`patterns.md`](typesafe-docs/patterns.md) | https://docs.typesafe.ai/patterns.md |
| [`patterns_fan-out.md`](typesafe-docs/patterns_fan-out.md) | https://docs.typesafe.ai/patterns/fan-out.md |
| [`patterns_confidence-routing.md`](typesafe-docs/patterns_confidence-routing.md) | https://docs.typesafe.ai/patterns/confidence-routing.md |
| [`patterns_intent-routing.md`](typesafe-docs/patterns_intent-routing.md) | https://docs.typesafe.ai/patterns/intent-routing.md |
| [`introduction.md`](typesafe-docs/introduction.md) | https://docs.typesafe.ai/introduction.md |
| [`introduction_quickstart.md`](typesafe-docs/introduction_quickstart.md) | https://docs.typesafe.ai/introduction/quickstart.md |
| [`introduction_coding-agents.md`](typesafe-docs/introduction_coding-agents.md) | https://docs.typesafe.ai/introduction/coding-agents.md |
| [`introduction_machine-learning-primer.md`](typesafe-docs/introduction_machine-learning-primer.md) | https://docs.typesafe.ai/introduction/machine-learning-primer.md |
| [`agent-skill.md`](typesafe-docs/agent-skill.md) | https://docs.typesafe.ai/agent-skill.md |
| [`legal.md`](typesafe-docs/legal.md) | https://docs.typesafe.ai/legal.md |
| [`sdk_python_usage.md`](typesafe-docs/sdk_python_usage.md) | https://docs.typesafe.ai/sdk/python/usage.md |
| [`sdk_python_api_types_questions.md`](typesafe-docs/sdk_python_api_types_questions.md) | https://docs.typesafe.ai/sdk/python/api/types/questions.md |
| [`sdk_python_api_types_responses.md`](typesafe-docs/sdk_python_api_types_responses.md) | https://docs.typesafe.ai/sdk/python/api/types/responses.md |
| [`sdk_python_api_constants.md`](typesafe-docs/sdk_python_api_constants.md) | https://docs.typesafe.ai/sdk/python/api/constants.md |
| [`cookbooks_semantic_find.md`](typesafe-docs/cookbooks_semantic_find.md) | https://docs.typesafe.ai/cookbooks/semantic_find.md |
| [`cookbooks_parallel_questions.md`](typesafe-docs/cookbooks_parallel_questions.md) | https://docs.typesafe.ai/cookbooks/parallel_questions.md |
| [`cookbooks_function_calling.md`](typesafe-docs/cookbooks_function_calling.md) | https://docs.typesafe.ai/cookbooks/function_calling.md |
| [`cookbooks_rerank_typesafe.md`](typesafe-docs/cookbooks_rerank_typesafe.md) | https://docs.typesafe.ai/cookbooks/rerank_typesafe.md |
| [`cookbooks_skill_suggestion.md`](typesafe-docs/cookbooks_skill_suggestion.md) | https://docs.typesafe.ai/cookbooks/skill_suggestion.md |

## Sources not saved here, and why

| Source | Outcome |
|---|---|
| `https://github.com/browser-use/jev-ultrafast` (README) | Fetched, HTTP 200. **Byte-identical to `vendor/jev-ultrafast/README.md`**, checked with `diff` on 2026-09-23, so the vendored copy is the saved copy. The whole upstream tree is already vendored, including `docs/design.md` and `docs/performance.md`. |
| `https://browser-use.com/ultrafast` | Fetched, HTTP 200, **no technical content**. The rendered page is a waitlist form: "SUPERFAST mode · Coming soon to Browser Use Cloud · Watch the recorded demo · Join the waitlist". Nothing about the model, the protocol or measurements. Recorded, not saved. |
| `https://typesafe.ai/` | Fetched, HTTP 200, **no technical content**. A marketing page whose only substantive text is the RLCD positioning diagram ("RLCD · reinforcement learning for calibrated decisions"), which `introduction_machine-learning-primer.md` covers properly. Recorded, not saved. |
| `https://typesafe.ai/legal/*` (DPA, MCA, privacy policy) | **Not fetched.** Linked from `legal.md`; account terms, not model documentation. |
| `https://console.typesafe.ai/playground` | **Not fetched.** Requires a sign-in. |
| A paper, model card or technical report for Jev | **Does not appear to exist.** `llms.txt` lists every documentation page and there is no paper, model card or architecture page among them; the nearest thing is `introduction_machine-learning-primer.md`, which describes RLCD in prose without citations. Recorded as absent rather than filled in from memory. |
| Third-party write-ups (you.com, flaviocopes.com, dev.to, litellm, mindstudio, tomshardware, langchain) | **Not used.** Web search surfaced them; none is a primary source, and everything they describe is in `docs.typesafe.ai`. Nothing in this directory or in `USING-JEV.md` cites one. |

## Licence

These are third-party documentation pages, kept for reference inside this repository and not
redistributed as part of any release. `legal.md` points to TypeSafe's Master Customer
Agreement, Privacy Policy and Data Processing Agreement; none of them was fetched, so nothing
here rests on a reading of those terms. If TypeSafe would rather these were not vendored, the
fetch is one `curl` per row of the table above and the directory can go.
