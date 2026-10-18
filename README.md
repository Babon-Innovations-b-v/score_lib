# score_lib

A library of locked, finished, reusable parts across every OpenUSD layer of a SCORE world, reused across worlds.
The index lives in git; the bytes live in a blob store. Every entry is versioned and never changed once locked.

## Kinds

| kind | one entry is |
|---|---|
| model | one made model (usd form, gltf form when the bake holds it) |
| surface | one surface library variant and the Material a place writes for it |
| effect | one particle emitter: its 35 settings and one baked clip |
| light | one sky picture with the prims that draw it |
| sound | one sound source: its SpatialAudio prim, takes and levels |
| character | one body look or dress |
| ground | one planet's terrain, or one place ground cut from it |
| kit | one place kit (and a ship place's kit layout) |
| algorithm | one engine algorithm: code snapshot, settings schema, world 1 preset, tests, run line |
| layer | one sublayer of one place (the physics layer too), its paths pointing at library addresses |
| place | one place's root, its sublayers pointing at layer entries, with its plan and cast |

An address is `<kind>/<id>@<version>`, for example `effect/devil_east.camp@1`.

## Use an entry in a world

```
score-lib fetch layer/base.hub@1 --into <root> --with-requires
PXR_AR_DEFAULT_SEARCH_PATH=<root> usdview <root>/score_lib/layer/base.hub/1/layer.usda
```

USD asset paths inside entries are search paths (`@score_lib/model/door_leaf/1/model.usdc@`); paths inside one entry
are relative (`./textures/...`). The stock resolver finds them; no plugin is needed.

## Commands

```
score-lib export --world world1 [--kind k] [--place p] [--dry-run] [--report <file>]
score-lib lock <address>... | --all-drafts --kind k
score-lib withdraw <address>
score-lib fetch <address>... --into <root> [--with-requires]
score-lib test algorithm/<name>@<v>
score-lib run algorithm/<name>@<v> --preset world1 --out <dir> [--arg name=value]
score-lib index
score-lib verify [--deep] [--address a]
score-lib page --out <dir outside the repo> [--drafts] [--public-url]
```

The export reads a SCORE store (`SCORE_STORE`, its `objects` folder) and the score repository (`SCORE_REPO`) at the
commit `worlds/<world>.toml` names, both read only. The blob store is set by environment variables only; see
`config/blobstore.env.template`.

## Layout

`entries/<kind>/<id>/<version>/entry.json` is the record; `index/` is built from it; `definitions/algorithms/` holds
one hand-written file per algorithm; `config/sources.toml` lists every source an entry's content may come from.

## Licence

The library and every entry in it, the bytes in the blob store included, are MIT (`LICENSE`). Nothing that cannot be
released under MIT is let in. An entry names no licence of its own, only what made it (`provenance.made_with`: each
part and its source in `config/sources.toml`). The export refuses an entry with a part whose source is unknown or may
not go out under MIT, and any entry carrying a refused entry's bytes; `score-lib verify` checks the same.

Our own outputs are in: code and bakes made by score, and what models make whose terms give us the output (Pixal3D,
the paid APIs, MOSS-SoundEffect). CC0 recordings and NASA's public-domain pictures are in, since their terms allow
relicensing. Code, fonts or other files whose terms require keeping their own licence (BSD-3-Clause, the SIL Open
Font License, GPL) are never copied in: an algorithm names them as outside requirements (`outside` in its
definition: name, version, where to get it, why), and its snapshot leaves their files out.

## Tests

```
uv run pytest
```
