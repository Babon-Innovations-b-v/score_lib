"""Web 3D copies of library entries for the cockpit's viewer: each model, kit, character and place entry gets two
compressed glTF binaries, a light preview and the whole copy, made from the entry's own OpenUSD (fetched with what it
requires) on this machine, one entry at a time.

    gltf.py     the stage's drawn meshes into one glTF document (the method of score's tools/review/viewer/export.py,
                copied: score is never imported)
    finish.py   every surface's finish from the shared surface library (the owner's rule: never a picture's own
                texture): a library material as the library holds it, a library bake's maps, else the library
                surface nearest the colour the part showed
    pack.py     the pictures as WebP (the library bake's own way: colour lossy, metal-roughness and normals lossless)
                and gltfpack's meshopt compression (gltfpack 0.22.0, MIT, through npx --offline)
    build.py    what each kind's copy is made of
    run.py      which entries need a copy, and recording it: `web3d` in the entry (both files' hashes) and the two
                files in its `files` (role web3d), their bytes in the blob store
"""
