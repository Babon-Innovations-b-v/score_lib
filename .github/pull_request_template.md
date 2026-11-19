## What this adds

<!-- Each entry's address (kind/id@version), what it is, and where its content came from. -->

## Bundle

<!-- The link to your bundle (`score-lib bundle <address>... --out <name>.tgz`): attached here when it is at most
25 MB, else a release asset on your fork (each under 2 GiB). One link per bundle. -->

-

## Checklist

See CONTRIBUTING.md for each rule.

- [ ] Every source is my own work or its terms allow copying in and releasing under MIT (CC0, public domain).
- [ ] Every new source has its row in `config/sources.toml`, quoting its terms and the date I read them.
- [ ] No personal data, no recognisable person, no logos or trademarks, nothing from a paid store.
- [ ] This pull request only adds: new entry folders, new source rows, new algorithm definitions and the index lines
      `score-lib index` builds. It changes no existing file.
- [ ] Every commit is signed off (`git commit -s`).
- [ ] Taken in with `score-lib take-in` and its `contributor` block (my handle and my claim); entries are drafts.
- [ ] `score-lib verify --address <address> --contribution-pending` prints no failure and `uv run pytest` passes.
- [ ] Quality score: ___ (a reason given if under 50).
- [ ] Models, kits, characters: in metres, Y up, real size; pass SCORE's USD check and library check in a place.
- [ ] No bytes in git; every byte is in the bundle linked above.
- [ ] I understand this contribution is public and traced to my GitHub account, and I release it under the MIT
      licence in `LICENSE`.
