"""docclass: classify the pages of a document against a taxonomy.

The shape is ported from kyotofin/tax-doc-classifier (Apache-2.0, see
docs/CREDITS.md). US tax forms are irrelevant here; the structure is not:

  - a JSON file of document-type criteria, not a table baked into code;
  - a TWO-STAGE Choice, family first and then member within that family, so
    neither call has to hold hundreds of options at once;
  - a `not_in_this_list` escape on BOTH stages, so the model is never forced
    to pick the least-wrong answer from a list that does not contain the
    right one;
  - a confidence gate, so a low-confidence page is routed to a human instead
    of being filed wrongly and silently.

This library ships no real taxonomy. `taxonomies/example.json` is a small,
deliberately generic one. Nothing here keys off a folder tree, because a
document store's folder tree is exactly the thing that gets restructured.
See README.md.
"""
from .classify import (  # noqa: F401
    NOT_IN_LIST,
    ClassificationError,
    classify_page,
    load_taxonomy,
    validate_taxonomy,
)
