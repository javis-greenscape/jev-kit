# docclass: classify the pages of a document against a taxonomy

> **Experimental.** Tested: the two-stage logic, the `not_in_this_list` escape
> hatch and the confidence gate, by 25 unit tests with Jev mocked
> (`tests/test_docclass.py`), plus one live run against a public IRS Form W-9
> (`docclass/smoke.py`, see [Tests](#tests)). Not tested: any real taxonomy, any
> real document set, any accuracy claim. There is **no labelled corpus and no
> measured accuracy** for this component. The numbers quoted below are the
> upstream behaviour study's, not this code's.

`classify_page(text, taxonomy)` plus a CLI for text-layer PDFs. The shape is
ported from kyotofin/tax-doc-classifier (Apache-2.0, see
[`docs/CREDITS.md`](../docs/CREDITS.md)); none of its content is, because US tax
forms are irrelevant here.

## The three properties worth having

**Two stages.** A Choice over every document type in a real taxonomy is a
question with dozens of options, most of them irrelevant to the page in front
of it. Family first, then the specific type within the chosen family, keeps
each question small. A family with no narrower types costs exactly one call.

**`not_in_this_list` on both stages.** A Choice has to return something.
Without an escape option, a page that is none of the listed kinds comes back as
the least-wrong one, with a confidence that says nothing about whether the
answer belonged in the list at all.

**A confidence gate.** Below the gate, the page is `needs_review` and goes to a
person. The gate lives in the taxonomy file rather than in code, because how
costly a misfile is depends entirely on what is being filed.

## Use it as a library

```python
from docclass import classify_page, load_taxonomy

taxonomy = load_taxonomy("docclass/taxonomies/example.json")
result = classify_page(page_text, taxonomy)
# {'label': 'letter/covering_letter', 'status': 'classified',
#  'family': 'letter', 'family_confidence': 0.99,
#  'member': 'covering_letter', 'member_confidence': 0.93,
#  'gated': False, 'reason': '...'}
```

`status` is one of:

| status | meaning |
|---|---|
| `classified` | a confident answer, at or above the gate |
| `not_in_taxonomy` | the model took the escape hatch: no listed kind fits |
| `needs_review` | below the gate, no extractable text, or an answer outside the offered options |

`classify_page` takes its `ask` as an argument, so the tests run with Jev fully
mocked and nothing in the module reads an API key.

## Use it as a CLI

```bash
python3 -m docclass.cli --taxonomy docclass/taxonomies/example.json doc.pdf
python3 -m docclass.cli doc.pdf --summary
python3 -m docclass.cli doc.pdf --first 3 --last 5
```

JSON lines by default, so it pipes. Needs `pdftotext`:

```bash
sudo apt install poppler-utils
```

**Text-layer PDFs only.** `pdftotext` returns nothing for a scan, and a page
with no text is reported as `needs_review` with that as the reason, rather than
sending an empty string to a model and filing whatever comes back. A scan or a
handwritten page is a different job: put the page image in front of a vision
model.

## The taxonomy file

```json
{
  "name": "example taxonomy",
  "confidence_gate": 0.8,
  "families": {
    "letter": {
      "what": "Correspondence from one party to another.",
      "not_for": "A form or an invoice that merely arrived with a letter.",
      "examples": ["Dear Mr Smith, further to our meeting..."],
      "members": {
        "covering_letter": {"what": "...", "not_for": "...", "examples": ["..."]}
      }
    }
  }
}
```

`what`, `not_for` and `examples` per option, rather than one-line labels: that
structured shape is the intervention jev-behavior-study measured as working
(25/25) where a sterner preamble measured as failing (0/25). When a page is
misclassified, the fix is **another example in the criteria**, never a firmer
instruction.

`not_in_this_list` is reserved and added automatically. Using it as a family or
member name is rejected at load time.

## Write your own taxonomy

`taxonomies/example.json` is generic and exists so the library has something to
run against. When you write a real one:

**Key off document kind only. Do not encode your document store's folder
tree.** Such a tree gets restructured, so a taxonomy mirroring it would be
wrong the week after it was written. Document kinds (O&M manual, commissioning record, RAMS,
variation order, invoice, site photo sheet) survive a restructure; folder paths
do not.

## Tests

```bash
python3 -m unittest tests.test_docclass      # 25 tests, Jev fully mocked

set -a; . "$AIRLOCK_KEY_FILE"; set +a
python3 docclass/smoke.py                    # one live run, real calls
```

The smoke test downloads a public IRS Form W-9 and classifies its six pages
against the example taxonomy, which has no US-tax-form kind in it. Run
2026-09-19: all six pages came back `form`, confidence 0.98-1.00.

Read it this way: `form` is right for the form pages and defensible but crude for
the instruction pages, which the example taxonomy has no kind for at all. That
is a finding about the example taxonomy rather than about the code. It is the
shape of thing a real taxonomy has to get right, which is why the gate and
the escape hatch exist.
