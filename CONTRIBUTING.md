# Contributing to score_lib

## For people

Thank you for adding to the library. A good entry is used in every place that needs it.

You may add:

- **your own work**, or work from a **CC0 or public domain** source. Everything here is released under MIT, so a
  source must allow that. CC-BY, CC-BY-SA, NC, GPL and similar terms do not.
- **no personal data**: no recognisable real person (face, body scan, voice), no health data.
- **no logos, trademarks** or copied product designs.
- **nothing from paid stores** or packs that forbid sharing, even if you bought it.

Your contribution is public. Every entry is traced to your GitHub account and the pull request it came in.
We keep nothing about you but your public GitHub handle.

Every commit needs a sign-off line (`git commit -s`). It says you have the right to release the work under MIT
([Developer Certificate of Origin](https://developercertificate.org/)).

A pull request adds new things only. It may not change any of our existing files.

**We suggest doing it with a coding agent.** Point your agent at this file and say "add these files to score_lib as
a contribution". The part below is written for it. A maintainer checks every contribution and looks at every picture,
model and sound before it is merged.

**Takedown.** If you hold the rights to something in the library and did not agree to it being here, open an issue
titled "takedown" naming the entry, or write to the maintainers. We remove the entry at once, and every other entry
from the same contributor or source if needed (`score-lib trace`).

## For coding agents

Read all of this before you start. Follow it exactly.

### 1. What a contribution may hold

An entry is one of these kinds (README.md, "What is in it"):

| kind | added by |
|---|---|
| model, surface, kit, character, sound, take | `score-lib take-in <list.json>` |
| place | `score-lib take-in --place <place_build folder>` |
| algorithm | its own entry folder holding its code and definition, plus a new `definitions/algorithms/<name>.toml` |
| effect, light, layer | not taken from outside (made by `score-lib export` from a SCORE store) |
| concept | not taken from outside (made by `score-lib concept` on a creator's pick) |

The pull request may only **add**:

- new entry folders, `entries/<kind>/<id>/<version>/` (an algorithm's code and definition inside its own folder);
- a new `definitions/algorithms/<name>.toml`;
- new rows of `config/sources.toml` (every row already there stays exactly as it is);
- the `index/` lines `score-lib index` builds for the new entries (never `index/locks.json`).

It may not change, delete or rename any existing file: not `src/`, `tests/`, `schemas/`, `worlds/`, `.github/`,
`CLAUDE.md`, `README.md`, `LICENSE`, an existing entry or anything else. `score-lib review` fails a pull request that
does. Bytes never go into git; they travel in the bundle (step 6).

### 2. Sources and the licence

- **Only sources whose terms allow it.** The work is the contributor's own, or its terms let it be copied in and
  released under MIT (CC0 and public domain do; CC-BY, CC-BY-SA, CC-NC, GPL, BSD-3-Clause and the SIL Open Font
  License do not, because they require keeping their own licence or credit). A model or tool's output is in only
  when its terms give the output to the user.
- **Every source has its row in `config/sources.toml`**, written from the source's real terms: `output` and `copy`
  set to what those terms allow, `why`, and `from` quoting where the terms say so and the date they were read. A new
  source gets its row in the same pull request.
- **No personal data.** No recognisable real person (face, body scan, voice) without their written consent, and no
  health or patient data, ever.
- **No brand logos or trademarks**, no copied product designs, no text or signs naming real companies.
- **Nothing from paid stores or asset packs whose licence forbids redistribution.**
- **Real data only.** No placeholder or mock files.

### 3. Set up

```
uv sync
export SCORE_LIB_BLOBS_DIR=<a folder outside the repository>
```

`config/blobstore.env.template` lists every setting. The bytes go to that folder; only records go into git.

### 4. Write the take-in list and take it in

One JSON file. Its shape is in the docstring of `src/score_lib/take_in.py`. It must hold the `contributor` block:

```json
{"world": "world1", "commit": "<the score_lib commit you started from>", "taken_from": "<where the files came from>",
 "contributor": {"handle": "<your GitHub handle>", "claim": "my own work | <the source> under <its terms>"},
 "entries": [{"kind": "model", "id": "my_lamp", "title": "Desk lamp",
              "files": {"gltf/my_lamp.glb": {"path": "<local file>", "from": "<its name where it came from>"}},
              "made_with": [{"what": "gltf/my_lamp.glb", "source": "<a sources.toml id>", "carried": true}]}]}
```

`carried: true` when the source's own bytes are copied in. The handle is the pull request author's. What each kind
holds (any other file type is refused, `.blend`, scripts and executables included):

- **model**: its glTF (`.glb`, or `.gltf` with every `.bin` and picture it names), in metres, Y up, at real size;
  pictures `.png`, `.jpg`, `.webp`.
- **surface**: its look (`material.usda`), its maps (`.png`, `.jpg`, `.webp`) and its record (`surface.json`).
- **kit**: the piece's geometry (`.usdc` or `.usda`) and its record.
- **character**: the body (`.glb`, `.usdc`), its pictures and its motion clips (`.npz`) with their records.
- **sound**: the recording (`.ogg`, `.wav`, `.flac` or `.mp3`) and its record.
- **take**: its models (`.glb`), its record and its close-up (`.png`).
- **algorithm**: its code (`.py`), its records (`.json`) and its definition (`.toml`). Follow the shape of an
  existing algorithm entry (`entries/algorithm/<name>/<version>/`); its `entry.json` carries
  `provenance.contributor` and the `contribution` block exactly as take-in writes them. Its code is read line by line
  by a maintainer and runs only inside the isolated review environment.

Then:

```
uv run score-lib take-in <list.json> --report <file>
uv run score-lib index
```

Every entry comes in as a draft, or is refused with the reason in the report. Fix and run again.

### 5. Check it

- `uv run score-lib verify --address <kind>/<id>@<version> --contribution-pending` prints no failure.
- `uv run pytest` passes.
- `uv run score-lib quality --address <address>` gives the quality score, 0 to 100 (50 is the line where SCORE has
  a gate for the measure). An entry under 50 needs a reason in the pull request.
- Models, kits and characters, laid in a SCORE place, pass SCORE's USD check
  (`python -m score.stages.complete.usd`): metres and Y up, colliders on what is stood on or hit, mass on what moves,
  no triangle-mesh collider under a moving body. They also pass SCORE's library check
  (`python -m score.stages.complete.library`): every finish is a library `surface` entry, never a baked colour or a
  texture from a picture. A model made from a picture keeps its geometry and gets library finishes. The one exception
  is a player model (a character made from a picture), which may keep its own texture graded to its place's palette.
  Any other exception is written in SCORE's `data/checks/library-exceptions.json` by the owner, not by a contributor.

Keep files to what the entry needs: a model at the triangle budget its size calls for, maps no larger than the finish
shows.

### 6. Bundle the bytes

```
uv run score-lib bundle <address>... --out <name>.tgz
```

The bundle holds the new entries' records and every byte they name. Upload it with the pull request:

- **at most 25 MB**: attach it to the pull request (GitHub takes `.tgz` attachments up to 25 MB);
- **larger**: attach it to a release on your fork (each release asset must be under 2 GiB) and link it;
- **2 GiB or more**: split the entries into several bundles, one link each.

(GitHub's limits as its docs state them, read 2026-11-19.)

### 7. Open the pull request

Commit with sign-off (`git commit -s`), push to your fork, open the pull request and fill in the template: each
entry's address, the bundle link(s) and the checklist.

### 8. How a maintainer reviews it

Every contribution is opened and checked by a maintainer in an isolated, throwaway environment. There:

1. `score-lib review --base origin/main --contributor <author> --bundle <link> --sheet <sheet.png>` fails the pull
   request when it changes, deletes or renames any existing file, adds anything but new entries, definitions, source
   rows and the index lines built for them, holds a commit without `Signed-off-by:`, or adds an entry that is not a
   draft taken in as the author's. It opens each bundle and refuses absolute paths, `..`, links, oversized files,
   archives that unpack far larger than they are, any file type the entry's kind does not list, files whose bytes are
   not their type, and any blob whose hash or size is not its record's.
2. A human looks at every picture, model view and sound on the contact sheet, reads each source's terms against its
   `config/sources.toml` row, and reads an algorithm's code line by line.
3. On merge, `score-lib take-bundle <link>... --pr <n> --contributor <author> --commit <signed-off sha> --save`
   checks the bundle again, puts its bytes in the blob store and the public Hugging Face dataset, and completes each
   entry's trace: handle, pull request, signed-off commit, date, claim and the bundle's hash. `score-lib verify`
   fails an outside entry without it. `score-lib trace <address | sha256 | handle:<h> | source:<id> | pr:<n>>`
   answers who added what, from which pull request and on what claim.

An accepted entry is merged as a draft and locked later; a locked entry never changes, a change becomes the next
version.

A contribution is rejected when:

- a source's terms do not allow copying in and releasing under MIT, or its terms cannot be found;
- it holds personal data, a recognisable person without consent, a logo or a trademark;
- it changes any existing file, or a commit is not signed off;
- `score-lib review`, `score-lib verify` or the tests fail, or a place check fails;
- its files are mock or placeholder data, or are not what the record says they are.

### 9. Licence

By contributing, the contributor agrees the contribution is released under the MIT licence in `LICENSE` and confirms
the right to release it so (the sign-off). An entry names no licence of its own; its sources are named in
`provenance.made_with`.
