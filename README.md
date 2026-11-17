# score_lib

A library of locked, finished, reusable parts across every OpenUSD layer of a SCORE world, reused across worlds.
The index lives in git; the bytes live in a blob store. Every entry is versioned and never changed once locked.

## Kinds

| kind | one entry is |
|---|---|
| model | one made model (usd form, gltf form when the bake holds it, and `closeup.png`, the accepted close-up it was made from, when the store holds one); also a CC0 kit's model, a TRELLIS.2 model made by score's `place_build make` (`<name>_trellis.<place>`, graded to its place's palette), and a real ground's relief (tagged `terrain`: a HiRISE elevation model's heightfield mesh) |
| surface | one surface library variant and the Material a place writes for it, or one image-mapped finish (its look, its maps and its record, `surface.json`), such as a CC0 scanned material set |
| effect | one distinct effect however many spots and places show it: one placement's 35 settings and baked clip as its sample, every placement named; each placement's own settings and clip stay in its place's layer |
| light | one sky picture with the prims that draw it |
| sound | one sound source: its SpatialAudio prim, takes and levels |
| character | one body look or dress |
| ~~ground~~ | retired 2026-10-20: a terrain piece is a `model` (tagged `terrain`, made through the prop pipeline), a ground finish a `surface`; the old ground entries were removed 2026-11-16 (score #128) |
| kit | one kit piece (a wall panel, a frame, a CC0 kit's piece such as a barricade or a dome wall panel) or one place kit (and a ship place's kit layout); the old per-place kits were removed 2026-11-16 (score #128) |
| algorithm | one engine algorithm: code snapshot, settings schema, world 1 preset, tests, run line; among them the placing method (`place_build`) and the code-made parts it lays (`dome_shell`, `stair_core`, `wall_kit`, `terrain_kit`, `rain`, `ground_tracks`), and the walkers' `walker_route` |
| layer | one sublayer of one place (the physics layer too), its paths pointing at library addresses: a part of its place, shown under it, never a shelf of its own; the old per-place layers were removed 2026-11-16 (score #128) |
| place | one place's root, its sublayers pointing at layer entries, with its plan and cast (a place only others show, such as world 1's far city, too: `shown` in the world toml); the old place entries were removed 2026-11-16 (score #128); a scene laid by score's `place_build` is not yet a place entry, since this kind is read from a store's assembly and `take-in` takes no place |
| concept | one place's locked concept (`concept.png`) and `concept.json`: its pick, the prompt, marks and notes of every draw that led to it, and every draw of the place by its sha; made by `score-lib concept` only, on the creator's pick in score's concept review, never shipped |

An address is `<kind>/<id>@<version>`, for example `effect/devil_east.camp@1`.

## What the library holds

Since 2026-11-16 (score #128) a place is laid by hand from library entries by score's placing method
(`algorithm/place_build`: an agent lays entries, renders the concept's view, compares it with the locked concept and
corrects), so the library holds what such a place is laid from:

- models: the made models of world 1's accepted bakes, the CC0 kits' models (Kenney, Poly Haven, Quaternius), the
  TRELLIS.2 models `place_build make` made for a place's props, and real ground relief from public-domain HiRISE
  elevation models (`model/hirise_*`), each a draft;
- surfaces: the surface library's variants and image-mapped finishes from CC0 scanned material sets (ambientCG, Poly
  Haven);
- kits: CC0 kit pieces and score's code-built dome wall panels;
- algorithms: the code that makes what no kit holds (a ribbed glass dome and its tunnels, a switchback stair core,
  walls from a kit's rows, terrain features, rain, ground tracks), the placing method itself, and the walkers' routes
  (a pathfinder on the walkable floor whose route is cut into Kimodo pieces and joined into one motion), with the
  physics of a person (`sim_body`) and the rest of world 1's engine;
- concepts, characters, effects, lights and sounds as before.

Every source an entry's content comes from has its row in `config/sources.toml`, read from the source's own terms.

## Use an entry in a world

```
score-lib fetch model/<id>@1 --into <root> --with-requires
PXR_AR_DEFAULT_SEARCH_PATH=<root> usdview <root>/score_lib/model/<id>/1/model.usdc
```

USD asset paths inside entries are search paths (`@score_lib/model/door_leaf/1/model.usdc@`); paths inside one entry
are relative (`./textures/...`). The stock resolver finds them; no plugin is needed.

## Commands

```
score-lib export --world world1 [--kind k] [--place p] [--dry-run] [--report <file>]
score-lib lock <address>... | --all-drafts --kind k
score-lib withdraw <address>          (a locked entry, or a draft: frozen as it stood)
score-lib fetch <address>... --into <root> [--with-requires] [--file <name>]...
score-lib take-in <list.json> [--report <file>]
score-lib test algorithm/<name>@<v>
score-lib run algorithm/<name>@<v> --preset world1 --out <dir> [--arg name=value]
score-lib index
score-lib audit --world world1 [--report <file>]
score-lib web3d [--kind k] [--address a] [--limit n] [--report <file>]
score-lib verify [--deep] [--address a]
score-lib quality [--kind k] [--address a] [--threads 4] [--dry-run]
score-lib page --out <dir outside the repo> [--drafts] [--public-url]
score-lib handover --world w [--place p] --seed n --commit <score sha> --store <objects> --repo <score> --out <dir>
score-lib held --world w [--place p]
score-lib concept --world w --place p --commit <score sha> --store <objects>
```

The export reads a SCORE store (`SCORE_STORE`, its `objects` folder) and the score repository (`SCORE_REPO`) at the
commit `worlds/<world>.toml` names, both read only. The blob store is set by environment variables only; see
`config/blobstore.env.template`.

`handover` is what score's runner calls once a place (or, without `--place`, the world's own node) is accepted: under
a lock (`.handover.lock`) it records the place in `worlds/<w>.toml`, exports the place kinds over every place of the
world at the commit given (the world node: `algorithm`), gives each entry its web 3D copy and quality
score, commits and pushes `entries/ index/ worlds/`, and writes `<out>/handover.json` (`schemas/handover.schema.json`,
example `schemas/handover.example.json`): every accepted output and the entries holding it, every entry with its
status and provenance, every refusal. It writes drafts only; it exits non-zero only when it could not run. `held`
prints the newest entries a place (or the world node) holds, as JSON, for the cockpit's check.

`concept` makes a place's `concept/<place>` entry follow its standing pick in the SCORE store (score's concept
review keeps `concepts/<place>/draws/` and `picks/` there): a pick makes the picked draw the entry, new or the next
version, and locks it; an unlock withdraws the newest version; nothing else changes it. It commits and pushes that
entry's folder and the index only, and prints one JSON line {place, did, address, saved}.

`take-in` takes loose made files, files that are no output of a SCORE store (world 1's old release of made models and
sounds, CC0 kits and material sets, models made by score's `place_build make`), in as draft `sound`, `model`, `take`, `kit`, `character` and `surface` entries from a list (`src/score_lib/take_in.py` has its shape): each entry's
files, where each came from and its sources, admitted by the same MIT rule; the bytes go to the blob store. Such an
entry has no OpenUSD form and no picture yet (a model is its glTF, `gltf/...`), so `quality` and `web3d` pass a model
of it by, and `verify` checks it whole: every file named where it came from, its role by its ending, and every buffer
and picture its `.gltf` names held beside it. score fetches one file of an entry with `fetch <address> --file <name>`.

## Web copies and the audit

`score-lib web3d` gives every model, kit, character and place entry two compressed glTF binaries for a web viewer,
a light preview (`web3d/light.glb`) and the whole copy (`web3d/full.glb`), made from the entry's own OpenUSD with
meshopt (gltfpack) and WebP pictures, and records them in the entry (`web3d`, both files' hashes, and the two files
with role `web3d`). Every finish comes from the surface library: a library material as its entry holds it, a library
bake's maps, else the library surface nearest the colour the part showed (each replacement listed in `web3d`).
A draft's copy is made again when its files are not in the blob store.

`score-lib audit` counts what a world holds, kind by kind, against the entries, and names why anything is missing.

## Layout

`entries/<kind>/<id>/<version>/entry.json` is the record; `index/` is built from it; `definitions/algorithms/` holds
one hand-written file per algorithm; `config/sources.toml` lists every source an entry's content may come from.

## Quality scores

`score-lib quality` gives every entry a quality score from 0 to 100, from free measures run on this machine by a
scoring system of its kind's own (`src/score_lib/quality/<kind>.py`), and writes into the entry `score`, `score_parts`
(each part's value, what was measured, how, and a picture of its worst spot in the blob store where one applies),
`refine_options` (what refining it would do, free or paid, a paid one priced only from measured unit costs) and
`score_made`. 50 is a gate's own line wherever the framework has a gate for the measure; a part that needs a paid
judge is named and left out. It reads `SCORE_STORE` and `SCORE_REPO` and the score repository's own tools, so it runs
under that repository's interpreter (`PYTHONPATH=src <score>/.venv/bin/python -c "from score_lib.cli import main;
main(['quality'])" < /dev/null`).

## Licence

The library and every entry in it, the bytes in the blob store included, are MIT (`LICENSE`). Nothing that cannot be
released under MIT is let in. An entry names no licence of its own, only what made it (`provenance.made_with`: each
part and its source in `config/sources.toml`). The export refuses an entry with a part whose source is unknown or may
not go out under MIT, and any entry carrying a refused entry's bytes; `score-lib verify` checks the same.

Our own outputs are in: code and bakes made by score, and what models make whose terms give us the output (Pixal3D,
the paid APIs, MOSS-SoundEffect). CC0 recordings, kits and material sets, TRELLIS.2's output (MIT code and weights), and NASA's public-domain pictures and HiRISE elevation models are in, since their terms allow
relicensing. Code, fonts or other files whose terms require keeping their own licence (BSD-3-Clause, the SIL Open
Font License, GPL) are never copied in: an algorithm names them as outside requirements (`outside` in its
definition: name, version, where to get it, why), and its snapshot leaves their files out. A code file of score's whose
head names a licence other than MIT (`SPDX-License-Identifier`, a port of someone else's code) is left out the same
way, hashed with the code and named as an outside requirement by its path.

## Tests

```
uv run pytest
```
