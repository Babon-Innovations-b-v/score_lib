# score_lib

A library of reusable parts of SCORE worlds: records in git (`entries/`), bytes in a blob store.

## Rules

- Provider agnostic: no cloud provider, endpoint, bucket or company name in code, tests, docs or config. The blob
  store is configured by environment variables only (`config/blobstore.env.template`, placeholders only). The one
  named host is the Hugging Face Hub backend (`blobs/hub.py`, the public copy, score #141), which names its own
  library; its repository is a setting too, never in code.
- Everything in the library is MIT (`LICENSE`), except parts from a source whose row names another licence, each
  entry naming it and its credit (its top-level `licence` block; owner, 2026-11-22, score #155); what cannot go out
  under MIT or under its row's own licence is not let in. A row in `config/sources.toml` is added only from the
  source's real terms; a source whose own bytes may not be copied in stays outside (an algorithm's `outside`
  requirement), never copied in.
- Real data only.
- Bytes live in the blob store, never on a local disk (the owner's rule for every machine, 2026-11-19: "everything
  lives in the bucket instead of my local"). A checkout and its caches are throwaway: a local copy of an entry's bytes
  is a cache that may be removed at any time, and a tool writes what it makes into the blob store, not a local folder.
- A locked entry is never rewritten; changed content becomes the next version as a draft.
- The export reads the SCORE store and the score repository and never writes to either.
- One function, one purpose; no single-letter names outside one-line comprehensions and lambdas.
- No new `.md` files beyond README.md, CLAUDE.md overlays, CONTRIBUTING.md and `.github/pull_request_template.md`
  (owner, 2026-11-19: "make a contributions tab in the score lib repo, with contribution rules and a pr template"),
  and the vendored skill `.claude/skills/security-audit/` from cloudflare/security-audit-skill, kept as released
  (owner, 2026-11-19: "also install this skill in both repos https://github.com/cloudflare/security-audit-skill").
- Commits: imperative and scoped (`export: ...`).

## Where things are

- `src/score_lib/export/<kind>.py`: one exporter per kind; `export/run.py` the order, versions and writes.
- `src/score_lib/verify.py`: every check of `score-lib verify`.
- `src/score_lib/take_in.py`: `score-lib take-in`, loose made files (no store outputs) in as draft entries; a `take`
  entry is one Pixal3D take whole with its `.svviews` camera folder; `--save` commits and pushes what it wrote.
- `src/score_lib/take_in_place.py`: `score-lib take-in --place`, a place built by score's placing method (a
  `place_build` folder) as a draft place entry, its scene pointing at the library entries it lays.
- `src/score_lib/take_in_hub.py`: `score-lib take-in hub --repo R --revision SHA [--only NAMES] [--save]`, an adopted
  set (score #155): one pinned revision of a Hugging Face dataset of OpenUSD assets, named by a `config/sources.toml`
  row's `take_in` table, taken in whole as model entries and the surfaces their materials name (`score:surface`),
  each under the row's licence and credit; bytes through a capped cache into the blob store; a gap row per asset it
  cannot take.
- `src/score_lib/adopted.py`: `score-lib adopted match [--source ID] [--proposals FILE] [--apply]`, an adopted set's
  overlap with our older models and surfaces (score #155): each paired with the adopted entry of the same kind of
  object or finish (core noun, synonyms, Wikidata class), or refused naming the clash (setting, colour, a finish
  painted for one model, a far copy); `--apply` withdraws the accepted ones naming their replacement (`replaced_by`).
- `src/score_lib/concept.py`: `score-lib concept`, a place's locked concept from score's concept review (score #40).
- `src/score_lib/handover.py`: `score-lib handover`, the one call score's runner makes (contract `library-handover` v1); `held.py`: what a place holds and which entries hold a store output.
- `src/score_lib/clip.py`: an entry's preview clip (owner, 2026-10-20): `score-lib clip-plan` says what each clip
  opens (its entries fetched), score renders it in cloud Blender at the item's handover, `score-lib clip-attach` puts
  it into the draft entry (`clip` block, role `clip`, outside the web copy's source) and saves. Nothing renders here.
- `src/score_lib/quality/<kind>.py`: one quality score system per kind (its parts and their marks); `quality/refine.py`
  the refine options and their measured prices. `score-lib quality` writes `score`, `score_parts`, `refine_options`
  and `score_made` into entries; they stay out of the digest, so scoring never rewrites a locked entry's content.
- `src/score_lib/blobs/`: the blob store backends (local folder, S3-compatible, Hugging Face Hub dataset repository)
  and `mirror.py`, `score-lib hub-mirror`: the S3 store's named blobs copied into a Hub repository and checked.
- `src/score_lib/bundle.py`: `score-lib bundle` (a contributor's new entries and their bytes in one file for the pull
  request) and `score-lib take-bundle` (a maintainer, on merge: every hash checked, the bytes into every blob store,
  each entry traced); `review.py`: `score-lib review`, an outside pull request's diff (new files only), sign-offs,
  entries and bundles checked and its contact sheet drawn; `contribution.py`: an outside entry's trace and `score-lib
  trace`. All of them open a bundle only inside the isolated review environment (a setting, no default).
- `docs/demo/`: the README's small pictures, one per kind, made from real entries (owner, 2026-11-19).
- `tests/conftest.py`: a small SCORE world (store, repo, library root) built per test.
