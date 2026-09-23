"""The six-task Wikipedia suite both arms of bench/run_wiki_bench.py are scored on.

One task is one goal in plain words, a page to start on, and a check. The check
never asks an arm whether it succeeded: it looks at the final URL the run ended
on and at the text that came back, and decides from those. Two tasks are pure
navigation (get to a named article by clicking links) and four also want a fact
off the last page, so those check the URL and the fact together.

Every expected answer here was read off the live article before the sweep ran,
on 2026-09-23. W4 was rewritten that day: Guido van Rossum's infobox now says
`employer = Retired`, so "his current employer" had stopped having an answer.
The hop is now named by when he worked there, which keeps the two-hop shape and
the same expected year.
"""

WIKI = "https://en.wikipedia.org/wiki/"

TASKS = [
    {
        "id": "W1",
        "start_url": WIKI + "Photosynthesis",
        "goal": (
            "Starting on the Photosynthesis article, reach the Wikipedia article "
            "'Ancient Rome' by clicking links on the page only. Do not use the "
            "search box and do not type a URL. Keep clicking article links that "
            "lead towards ancient history and Rome until the Ancient Rome article "
            "is open. Stop when the page heading is 'Ancient Rome'."
        ),
        "extract": "#firstHeading",
        "url_must_contain": "/wiki/Ancient_Rome",
        "answer_must_contain": None,
    },
    {
        "id": "W2",
        "start_url": WIKI + "Bicycle",
        "goal": (
            "Starting on the Bicycle article, reach the Wikipedia article 'Quantum "
            "mechanics' by clicking links on the page only. Do not use the search "
            "box and do not type a URL. Keep clicking article links that lead "
            "towards physics until the Quantum mechanics article is open. Stop "
            "when the page heading is 'Quantum mechanics'."
        ),
        "extract": "#firstHeading",
        "url_must_contain": "/wiki/Quantum_mechanics",
        "answer_must_contain": None,
    },
    {
        "id": "W3",
        "start_url": WIKI + "Chess",
        "goal": (
            "Step 1: from the Chess article, open the article about the 1972 World "
            "Chess Championship. Step 2: open the article of the player who lost "
            "that match. Step 3: on his article, open the article of the city he "
            "was born in. Step 4: stop on that city's article, which states the "
            "year the city was founded."
        ),
        "extract": "#firstHeading, .infobox",
        "url_must_contain": "/wiki/Saint_Petersburg",
        "answer_must_contain": "1703",
    },
    {
        "id": "W4",
        "start_url": WIKI + "Python_(programming_language)",
        "goal": (
            "Step 1: from the Python (programming language) article, open the "
            "article about the person who created Python. Step 2: on his article, "
            "open the article of the company he joined in 2020 and retired from in "
            "2026. Step 3: stop on that company's article, which states the year "
            "the company was founded."
        ),
        "extract": "#firstHeading, .infobox",
        "url_must_contain": "/wiki/Microsoft",
        "answer_must_contain": "1975",
    },
    {
        "id": "W5",
        "start_url": WIKI + "Nobel_Prize_in_Physics",
        "goal": (
            "Step 1: from the Nobel Prize in Physics article, open the list of "
            "Nobel laureates in Physics. Step 2: from that list, open the article "
            "about Richard Feynman. Step 3: on his article, open the article of "
            "his doctoral advisor. Step 4: stop on the advisor's article, which "
            "states the year he was born."
        ),
        "extract": "#firstHeading, .infobox",
        "url_must_contain": "/wiki/John_Archibald_Wheeler",
        "answer_must_contain": "1911",
    },
    {
        "id": "W6",
        "start_url": WIKI + "Main_Page",
        "goal": (
            "Step 1: use Wikipedia's own search box to search for Kilimanjaro and "
            "open the article about the mountain. Step 2: on that article, open "
            "the article of the man who was first to reach the summit. Step 3: "
            "stop on his article, which states his nationality."
        ),
        "extract": "#firstHeading, .infobox",
        "url_must_contain": "/wiki/Hans_Meyer",
        "answer_must_contain": "German",
    },
]

BY_ID = {t["id"]: t for t in TASKS}


def check(task_id, final_url, answer_text):
    """Score one run. Returns (passed, reason). Never consults the arm's own claim."""
    task = BY_ID[task_id]
    url = (final_url or "").replace("%28", "(").replace("%29", ")")
    if task["url_must_contain"].lower() not in url.lower():
        return False, "final URL %r does not contain %r" % (final_url, task["url_must_contain"])
    wanted = task["answer_must_contain"]
    if wanted and wanted.lower() not in (answer_text or "").lower():
        return False, "the answer text does not contain %r" % wanted
    return True, None
