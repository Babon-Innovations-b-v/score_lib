# score_lib

A library of reusable parts of SCORE worlds: records in git (`entries/`), bytes in a blob store.

## Rules

- Provider agnostic: no cloud provider, endpoint, bucket or company name in code, tests, docs or config. The blob
  store is configured by environment variables only (`config/blobstore.env.template`, placeholders only).
- Real data only. A licence row in `config/licences.toml` is added only from a real source text.
- A locked entry is never rewritten; changed content becomes the next version as a draft.
- The export reads the SCORE store and the score repository and never writes to either.
- One function, one purpose; no single-letter names outside one-line comprehensions and lambdas.
- No new `.md` files beyond README.md and CLAUDE.md overlays.
- Commits: imperative and scoped (`export: ...`).

## Where things are

- `src/score_lib/export/<kind>.py`: one exporter per kind; `export/run.py` the order, versions and writes.
- `src/score_lib/verify.py`: every check of `score-lib verify`.
- `tests/conftest.py`: a small SCORE world (store, repo, library root) built per test.
