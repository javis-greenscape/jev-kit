You are answering questions about one PDF using a PageIndex tree instead of reading the whole document.

Files in the current directory: `tree_full.json` (a PageIndex tree: nodes with title, summary, key_items and page_index, where page_index is the 1-based page the section starts on) and `paper.pdf`.

Procedure, for EACH question independently:
1. Read `tree_full.json` once.
2. Pick the node or nodes most likely to contain the answer, from titles, summaries and key_items alone. Say which node_ids you picked and why in one line.
3. Read ONLY those pages of `paper.pdf` with the Read tool's `pages` parameter (a section runs from its page_index up to the next node's page_index). Never read the whole PDF. If the first pick does not contain the answer you may make ONE more pick.
4. Answer with a verbatim quote from the page and the page number. If the pages you read do not contain the answer, say "NOT FOUND IN DOCUMENT" and do not answer from general knowledge.

Questions:
Q1. Which model does Docling use for table structure recognition, and which section discusses it?
Q2. On the Apple M3 Max with a 16-thread budget, what total time (TTS) did the pypdfium backend take?
Q3. How much GPU memory does Docling's vision-language-model pipeline require?

Finish with one line per question: `Qn | pages read | answer | quote | page`.
