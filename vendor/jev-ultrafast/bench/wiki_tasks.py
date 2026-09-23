"""The eight-task Wikipedia suite bench/run_wiki_bench.py scores both arms on.

Two groups, because the two arms are not built for the same job:

Group A (A1-A6) is navigation with every hop spelled out and a "Stop when the
X article is open" line: named links, one at a time. This is what `browse`'s
Jev chooser is built for (README.md, vendor/jev-ultrafast/README.md: it picks
one on-screen action per step and does not plan). Passing means the final URL
is the target article, nothing else. Neither arm is ever asked to report a
fact: the goal text says only "stop when...", and the scorer reads the fact
itself off the final page's own text after the run ends, the same way for
both arms (browse's own `text`/`extracted` for jev, an independent urllib
fetch of the final URL for sonnet). A wrong or missing fact fails the run even
if the URL is right.

Group B (B1-B2) is the old open-ended pair: a start article, a goal article,
no hops named. This is not what Jev is built for and is kept only as a
contrast, labelled as such in the README - it is not a fair test of `browse`.

Both arms get identical task text in both groups. `check()` never consults
what either arm claims about itself.

Every link and fact below was read off the live English Wikipedia article on
2026-09-23, using the MediaWiki API and the rendered HTML (not memory):
  A1 Photosynthesis -> "chlorophyll" -> "Chlorophyll a"
  A2 Chess -> "1972" (the World Chess Championship 1972 article) ->
     "Spassky" -> "Saint Petersburg" (his birthplace link); founding year 1703
  A3 Python (programming language) -> "Guido van Rossum" -> "Microsoft";
     founding year 1975
  A4 Nobel Prize in Physics -> "List of Nobel laureates in Physics" ->
     "Feynman" -> "John Archibald Wheeler" (his doctoral advisor); birth
     year 1911
  A5 Main Page -> search "Kilimanjaro" (resolves to Mount Kilimanjaro) ->
     "Hans Meyer" (first to the summit); nationality German
  A6 Bicycle -> "Bicycle wheel" (Bicycle has no direct link to a "Wheel"
     article) -> "axle"
"""

import re
import urllib.error
import urllib.request

WIKI = "https://en.wikipedia.org/wiki/"

TASKS = [
    {
        "id": "A1",
        "group": "A",
        "start_url": WIKI + "Photosynthesis",
        "goal": (
            "Starting on the Photosynthesis article, click the link for "
            "'chlorophyll'. Then, on the Chlorophyll article, click the link "
            "for 'Chlorophyll a'. Stop when the Chlorophyll a article is open."
        ),
        "extract": "#firstHeading, .infobox",
        "url_must_contain": "/wiki/Chlorophyll_a",
        "answer_must_contain": None,
    },
    {
        "id": "A2",
        "group": "A",
        "start_url": WIKI + "Chess",
        "goal": (
            "Starting on the Chess article, click the link for '1972' that "
            "opens the World Chess Championship 1972 article. Then click the "
            "link for 'Spassky' (Boris Spassky). Then, on his article, click "
            "the link for the city he was born in, Saint Petersburg. Stop "
            "when the Saint Petersburg article is open."
        ),
        "extract": "#firstHeading, .infobox, #mw-content-text p",
        "url_must_contain": "/wiki/Saint_Petersburg",
        "answer_must_contain": "1703",
    },
    {
        "id": "A3",
        "group": "A",
        "start_url": WIKI + "Python_(programming_language)",
        "goal": (
            "Starting on the Python (programming language) article, click "
            "the link for 'Guido van Rossum'. Then, on his article, click "
            "the link for 'Microsoft'. Stop when the Microsoft article is "
            "open."
        ),
        "extract": "#firstHeading, .infobox, #mw-content-text p",
        "url_must_contain": "/wiki/Microsoft",
        "answer_must_contain": "1975",
    },
    {
        "id": "A4",
        "group": "A",
        "start_url": WIKI + "Nobel_Prize_in_Physics",
        "goal": (
            "Starting on the Nobel Prize in Physics article, click the link "
            "for 'List of Nobel laureates in Physics'. Then, on that list, "
            "click the link for 'Feynman' (Richard Feynman). Then, on his "
            "article, click the link for his doctoral advisor, John "
            "Archibald Wheeler. Stop when the John Archibald Wheeler article "
            "is open."
        ),
        "extract": "#firstHeading, .infobox, #mw-content-text p",
        "url_must_contain": "/wiki/John_Archibald_Wheeler",
        "answer_must_contain": "1911",
    },
    {
        "id": "A5",
        "group": "A",
        "start_url": WIKI + "Main_Page",
        "goal": (
            "Starting on Wikipedia's Main Page, use the search box to "
            "search for 'Kilimanjaro' and open the article it resolves to "
            "(Mount Kilimanjaro). Then, on that article, click the link for "
            "'Hans Meyer', the first person to reach the summit. Stop when "
            "the Hans Meyer article is open."
        ),
        "extract": "#firstHeading, .infobox, #mw-content-text p",
        "url_must_contain": "/wiki/Hans_Meyer_(geographer)",
        "answer_must_contain": "German",
    },
    {
        "id": "A6",
        "group": "A",
        "start_url": WIKI + "Bicycle",
        "goal": (
            "Starting on the Bicycle article, click the link for 'Bicycle "
            "wheel'. Then, on that article, click the link for 'axle'. Stop "
            "when the Axle article is open."
        ),
        "extract": "#firstHeading, .infobox",
        "url_must_contain": "/wiki/Axle",
        "answer_must_contain": None,
    },
    {
        "id": "B1",
        "group": "B",
        "start_url": WIKI + "Photosynthesis",
        "goal": (
            "Starting on the Photosynthesis article, reach the Wikipedia "
            "article 'Ancient Rome' by clicking links on the page only. Do "
            "not use the search box and do not type a URL. Keep clicking "
            "article links that lead towards ancient history and Rome until "
            "the Ancient Rome article is open. Stop when the page heading "
            "is 'Ancient Rome'."
        ),
        "extract": "#firstHeading",
        "url_must_contain": "/wiki/Ancient_Rome",
        "answer_must_contain": None,
    },
    {
        "id": "B2",
        "group": "B",
        "start_url": WIKI + "Bicycle",
        "goal": (
            "Starting on the Bicycle article, reach the Wikipedia article "
            "'Quantum mechanics' by clicking links on the page only. Do not "
            "use the search box and do not type a URL. Keep clicking "
            "article links that lead towards physics until the Quantum "
            "mechanics article is open. Stop when the page heading is "
            "'Quantum mechanics'."
        ),
        "extract": "#firstHeading",
        "url_must_contain": "/wiki/Quantum_mechanics",
        "answer_must_contain": None,
    },
]

BY_ID = {t["id"]: t for t in TASKS}


def check(task_id, final_url, page_text):
    """Score one run from the final URL and the final page's own text.

    Neither arm is ever asked what the answer is, and neither is believed
    about its own success. `page_text` is whatever the scorer independently
    read off the final page: for jev, `browse`'s own `text`/`extracted`
    fields; for sonnet, a fresh fetch of `final_url` done by the scorer
    itself (fetch_page_text below), never the model's own prose.
    """
    task = BY_ID[task_id]
    url = (final_url or "").replace("%28", "(").replace("%29", ")")
    if task["url_must_contain"].lower() not in url.lower():
        return False, "final URL %r does not contain %r" % (final_url, task["url_must_contain"])
    wanted = task["answer_must_contain"]
    if wanted and wanted.lower() not in (page_text or "").lower():
        return False, "the target page's own text does not contain %r" % wanted
    return True, None


_TAG_RE = re.compile(r"<[^>]+>")


def fetch_page_text(url, timeout_s=15.0):
    """Read the fact straight off the final page ourselves, for the sonnet
    arm, which never reports a fact in its own words. Never trusts the
    model's transcript for the answer text."""
    if not url:
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "jev-kit-wiki-bench/1.0"})
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return "(could not fetch final_url for scoring: %s: %s)" % (type(exc).__name__, exc)
    body = html
    m = re.search(r'id="mw-content-text"', html)
    if m:
        body = html[m.start():m.start() + 40000]
    text = _TAG_RE.sub(" ", body)
    return re.sub(r"\s+", " ", text)
