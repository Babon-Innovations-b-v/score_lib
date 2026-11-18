# score_lib

A library of reusable parts of SCORE worlds: records in git (`entries/`), bytes in a blob store.

## Rules

- Provider agnostic: no cloud provider, endpoint, bucket or company name in code, tests, docs or config. The blob
  store is configured by environment variables only (`config/blobstore.env.template`, placeholders only).
- Everything in the library is MIT (`LICENSE`); what cannot be released under MIT is not let in. A row in
  `config/sources.toml` is added only from the source's real terms; a source whose own bytes cannot go out under MIT
  stays outside (an algorithm's `outside` requirement), never copied in.
- Real data only.
- A locked entry is never rewritten; changed content becomes the next version as a draft.
- The export reads the SCORE store and the score repository and never writes to either.
- One function, one purpose; no single-letter names outside one-line comprehensions and lambdas.
- No new `.md` files beyond README.md and CLAUDE.md overlays.
- Commits: imperative and scoped (`export: ...`).

## Where things are

- `src/score_lib/export/<kind>.py`: one exporter per kind; `export/run.py` the order, versions and writes.
- `src/score_lib/verify.py`: every check of `score-lib verify`.
- `src/score_lib/take_in.py`: `score-lib take-in`, loose made files (no store outputs) in as draft entries; a `take`
  entry is one Pixal3D take whole with its `.svviews` camera folder; `--save` commits and pushes what it wrote.
- `src/score_lib/take_in_place.py`: `score-lib take-in --place`, a place built by score's placing method (a
  `place_build` folder) as a draft place entry, its scene pointing at the library entries it lays.
- `src/score_lib/concept.py`: `score-lib concept`, a place's locked concept from score's concept review (score #40).
- `src/score_lib/handover.py`: `score-lib handover`, the one call score's runner makes (contract `library-handover` v1); `held.py`: what a place holds and which entries hold a store output.
- `src/score_lib/clip.py`: an entry's preview clip (owner, 2026-10-20): `score-lib clip-plan` says what each clip
  opens (its entries fetched), score renders it in cloud Blender at the item's handover, `score-lib clip-attach` puts
  it into the draft entry (`clip` block, role `clip`, outside the web copy's source) and saves. Nothing renders here.
- `src/score_lib/quality/<kind>.py`: one quality score system per kind (its parts and their marks); `quality/refine.py`
  the refine options and their measured prices. `score-lib quality` writes `score`, `score_parts`, `refine_options`
  and `score_made` into entries; they stay out of the digest, so scoring never rewrites a locked entry's content.
- `tests/conftest.py`: a small SCORE world (store, repo, library root) built per test.
