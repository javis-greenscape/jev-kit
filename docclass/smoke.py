#!/usr/bin/env python3
"""python3 docclass/smoke.py [<file.pdf>] -- ONE live run against a real PDF.

NOT part of `unittest discover`: it makes real Jev calls and needs the key
loaded. The mocked tests prove the logic; this proves the whole path works
against a document nobody wrote the taxonomy for.

    set -a; . "$AIRLOCK_KEY_FILE"; set +a
    python3 docclass/smoke.py

With no argument it downloads a public IRS Form W-9 (a text-layer PDF, freely
available, no credentials, nothing of anyone's) to a temp file and classifies
its pages against docclass/taxonomies/example.json.

A W-9 is deliberately a poor fit for that example taxonomy, and that is the
point of the smoke test. The right answers are `form` for the form page and
something other than a confident invented label for the instruction pages. A
run that files every page confidently into a wrong family is a FAILURE, not a
success, and this script says so.
"""
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docclass.classify import default_taxonomy_path, load_taxonomy
from docclass.cli import classify_document, print_summary

PUBLIC_PDF = "https://www.irs.gov/pub/irs-pdf/fw9.pdf"


def fetch(url, dest):
    print("downloading %s" % url)
    req = urllib.request.Request(url, headers={"User-Agent": "airlock-docclass-smoke"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    with open(dest, "wb") as f:
        f.write(data)
    print("  %d bytes -> %s" % (len(data), dest))
    return dest


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    tmpdir = None
    if argv:
        path = argv[0]
    else:
        tmpdir = tempfile.mkdtemp(prefix="docclass-smoke-")
        try:
            path = fetch(PUBLIC_PDF, os.path.join(tmpdir, "w9.pdf"))
        except Exception as exc:
            print("could not download the sample PDF: %s" % exc, file=sys.stderr)
            print("pass a local PDF path instead.", file=sys.stderr)
            return 1

    taxonomy = load_taxonomy(default_taxonomy_path())
    print("taxonomy: %s (gate %.2f)"
          % (taxonomy.get("name"), taxonomy.get("confidence_gate")))
    print()

    results = classify_document(path, taxonomy)
    print_summary(results)

    print()
    classified = [r for r in results if r["status"] == "classified"]
    print("Read it honestly:")
    print("  A W-9 is a US tax form, and the example taxonomy has no such kind.")
    print("  `form` on the form page is right. `not_in_taxonomy` or")
    print("  `needs_review` on the instruction pages is also right: the escape")
    print("  hatch and the gate working is the result, not a shortfall.")
    if classified and all(r["label"] and r["label"].startswith("financial_document")
                          for r in classified):
        print("  WARNING: every classified page landed in financial_document.")
        print("  That is the failure this design exists to prevent; look at it.")
        return 1
    if tmpdir:
        print()
        print("(temp download left at %s)" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
