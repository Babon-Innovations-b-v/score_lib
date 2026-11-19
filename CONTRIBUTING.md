# Contributing to score_lib

score_lib is the library SCORE builds its worlds from: models, surfaces, kits, characters, sounds and the rest. Every
place SCORE makes is laid from library entries, so a good entry here is used in every world that needs it. This page
says what you may add, how to add it, and how it is reviewed.

## What you may contribute

An entry is one of these kinds (README.md, "Kinds", says what one entry is):

| kind | added by |
|---|---|
| model, surface, kit, character, sound, take | `score-lib take-in <list.json>` |
| place | `score-lib take-in --place <place_build folder>` |
| algorithm | a definition in `definitions/algorithms/<name>.toml` and its code |
| effect, light, layer | `score-lib export` from a SCORE store |
| concept | `score-lib concept`, on a creator's pick only; not taken from outside |

Everything in the library is released under MIT (`LICENSE`), the bytes in the blob store included. So:

- **Only sources whose terms allow it.** Either the work is your own, or its terms let it be copied in and released
  under MIT (CC0 and public domain do; CC-BY, CC-BY-SA, CC-NC, GPL, BSD-3-Clause and the SIL Open Font License do
  not, because they require keeping their own licence or credit). A model or tool's output is in only when its terms
  give the output to you.
- **Every source has its row in `config/sources.toml`**, written from the source's real terms: `output` and `copy`
  set to what those terms allow, `why`, and `from` quoting where the terms say so and the date you read them. If your
  source is new, add its row in the same pull request.
- **No personal data.** No recognisable real person (face, body scan, voice) without their written consent, and no
  health or patient data, ever.
- **No brand logos or trademarks**, no copied product designs, no text or signs naming real companies.
- **Nothing from paid stores or asset packs whose licence forbids redistribution**, even if you bought it.
- **Real data only.** No placeholder or mock files.

## How to add an entry

1. **Set up.** `uv sync`, then point the blob store at a local folder: `export SCORE_LIB_BLOBS_DIR=<folder>`
   (`config/blobstore.env.template` lists every setting). Bytes never go into git; only records do.
2. **Write the take-in list**, one JSON file. Its shape is in the docstring of `src/score_lib/take_in.py`: for each
   entry its `kind`, `id`, `title`, its `files` (each with the local `path` and `from`, its name where it came from)
   and `made_with` (each part and its `source`, an id from `config/sources.toml`; `carried: true` when the source's
   own bytes are copied in). What each kind holds:
   - **model**: its glTF (`.gltf` with every buffer and picture it names, or `.glb`), in metres, Y up, at real size.
   - **surface**: its look (`material.usda`), its maps (`.png`, `.jpg`, `.webp`) and its record (`surface.json`).
   - **kit**: the piece's geometry (`.usdc`) and its record.
   - **character**: the body and its motion clips (`.npz`) with their records.
   - **sound**: the recording (`.ogg`, `.wav`, `.flac` or `.mp3`) and its record.
3. **Take it in**: `uv run score-lib take-in <list.json> --report <file>`. Every entry comes in as a draft, or is
   refused with the reason in the report. Fix and run again.
4. **Check it**: `uv run score-lib verify --address <kind>/<id>@<version>` must print no failure, and `uv run pytest`
   must pass. `score-lib quality --address <address>` gives the entry its quality score (0 to 100; 50 is the line
   where SCORE has a gate for the measure). An entry under 50 needs a reason in the pull request.
5. **Check it in a place** (models, kits, characters): laid in a SCORE place, it must pass SCORE's USD check
   (`python -m score.stages.complete.usd`): metres and Y up, colliders on what is stood on or hit, mass on what moves,
   and no triangle-mesh collider under a moving body. It must also pass SCORE's library check
   (`python -m score.stages.complete.library`): every finish it shows is a library `surface` entry, never a baked
   colour or a texture from a picture. A model made from a picture keeps its geometry and gets library finishes. The
   one exception is a player model (a character made from a picture), which may keep its own texture graded to its
   place's palette. Any other exception is written in SCORE's `data/checks/library-exceptions.json` by the owner, not
   by a contributor.
6. **Open a pull request** with the new `entries/` folders, the `index/` change and any new `config/sources.toml`
   row, and fill in the template. Upload the bytes from your blob folder (`blobs/<sha[:2]>/<sha>`) as a pull request
   on the public Hugging Face dataset `Babon-Innovations-b-v/score-lib` and link it.

File size: keep files to what the entry needs (a model at the triangle budget its size calls for, maps no larger
than the finish shows). Hugging Face's own per-file limits apply to the bytes.

## How a contribution is reviewed

A maintainer reruns `score-lib verify` and the tests, reads each source's terms against its `config/sources.toml`
row, opens the entry, and lays it in a place to run the place checks. An accepted entry is merged as a draft and
locked later; a locked entry is never changed, a change becomes the next version.

A contribution is rejected when:

- a source's terms do not allow copying in and releasing under MIT, or its terms cannot be found;
- it holds personal data, a recognisable person without consent, a logo or a trademark;
- `score-lib verify` or the tests fail, or a place check fails;
- its files are mock or placeholder data, or are not what the record says they are;
- it rewrites a locked entry, or puts bytes into git.

## Licence

By contributing you agree that your contribution is released under the MIT licence in `LICENSE`, and you confirm you
have the right to release it so. An entry names no licence of its own; its sources are named in
`provenance.made_with`.
