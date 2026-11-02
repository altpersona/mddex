# docdex

Which file should you read?

An offline BM25 router over markdown doc trees. Index a tree once, then ask
a question and get the few files worth opening — read one chapter, not
nineteen. Stdlib-only Python ≥3.10, no network, no LLM, no daemon, nothing
leaves your machine.

## Try it

Don't take any benchmark on faith — the only one that matters is your tree,
your question, your machine:

```bash
git clone https://github.com/altpersona/docdex && cd docdex
python3 -m venv .venv && .venv/bin/pip install -e .

# index your biggest doc tree, then ask it something you actually needed
# to know last week:
.venv/bin/docdex index /path/to/your/docs
.venv/bin/docdex ask "that thing you spent an hour grepping for" -k 3
time .venv/bin/docdex ask "another real question"
```

Open the files it names. If they're the right files, keep it; if they're
not, the Limits section below tells you why, honestly.

## What it does

- `docdex index <dir>` — index (or re-index) one tree; re-indexing replaces
  only that root's rows.
- `docdex ask "<question>"` — top matches across **every** indexed root,
  absolute paths out, `-k N` for more.
- `docdex ask ... --root <dir>` — scope to one root.
- `docdex roots` / `docdex list` / `docdex forget <dir>` — manage the index.
- Default index: `~/.cache/docdex/docs.db` (`--db` or `$DOCEX_HOME` moves it).

## How it ranks

Each file's searchable text is its **first 1–2 markdown headings** (capped
120 chars) plus its filename stem plus the body's **distinct vocabulary** —
a term living only in prose is still findable, while the db holds a sorted
word list, not readable prose. Ranking is Okapi BM25 (k1=1.2, b=0.75).

## Limits (honest)

- Keyword matching, not semantic: it finds files whose *words* match the
  question. Paraphrases it can't see. No stemming ("message" ≠ "messages").
- Heading-sparse files rank mostly on body terms — a weaker signal, though
  still findable.
- Noisy corpora poison generic queries: if one subtree mentions your topic
  constantly (generated reports, eval logs), index it as its own root and
  scope your asks with `--root`.
- Dot-directories (.git, .venv, …) are skipped at index time.
- The corpus is rebuilt in memory per ask — thousands of docs, not hundreds
  of thousands.

## Provenance & license

The docs-routing concept is inspired by
[mcptoon](https://github.com/activeing123/mcptoon) (Apache-2.0). docdex is a
clean-room implementation sharing no code with it; the ranking is Okapi BM25,
a long-public algorithm. MIT — see LICENSE.
