"""score-lib: argument parsing only, one function per command."""
import argparse
import json
import os
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]


def blob_store(reading=False):
    """The configured blob store; for a command that only reads, the library's public copy when none is set."""
    from score_lib.blobs import PUBLIC_STORE, NoBlobStore, from_environment
    try:
        return from_environment(public=PUBLIC_STORE if reading else None)
    except NoBlobStore as error:
        raise SystemExit(str(error))


def command_export(options):
    from score_lib.export import run
    context = run.context_from_environment(options.root, options.world, options.place or None)
    try:
        blobs = None if options.dry_run else blob_store()
        report = run.export(context, options.kind or None, options.dry_run, blobs)
    finally:
        shutil.rmtree(context.scratch, ignore_errors=True)
    if options.report:
        pathlib.Path(options.report).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n" if options.json else run.report_text(report))


def command_audit(options):
    from score_lib import audit
    from score_lib.export import run
    context = run.context_from_environment(options.root, options.world)
    try:
        report = audit.audit(context)
    finally:
        shutil.rmtree(context.scratch, ignore_errors=True)
    if options.report:
        pathlib.Path(options.report).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    sys.stdout.write(audit.report_text(report))


def command_web3d(options):
    import os
    from score_lib.web3d import run
    cache = pathlib.Path(os.environ.get("SCORE_LIB_CACHE") or pathlib.Path.home() / ".cache/score-lib") / "web3d"
    report = run.run(options.root, blob_store(), cache, tuple(options.kind or run.KINDS), options.address or None,
                     options.limit)
    if options.report:
        pathlib.Path(options.report).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"made {len(report['made'])}, standing {report['standing']}, locked {len(report['locked'])}, "
          f"failed {len(report['failed'])}")
    raise SystemExit(1 if report["failed"] else 0)


def command_lock(options):
    from score_lib import locks
    addresses = list(options.address)
    if options.all_drafts:
        addresses += locks.drafts(options.root, options.kind)
    locks.lock_many(options.root, addresses)
    print(f"locked {len(addresses)}")


def command_withdraw(options):
    from score_lib import index, locks
    locks.withdraw(options.root, options.address)
    index.rebuild(options.root)
    print(f"withdrawn {options.address}")


def command_fetch(options):
    from score_lib.fetch import MissingEntry, MissingFile, fetch
    try:
        records = fetch(options.root, blob_store(reading=True), options.address, options.into, options.with_requires,
                        names=options.file)
    except (MissingEntry, MissingFile) as missing:
        raise SystemExit(f"not in the library: {missing}")
    print(f"fetched {len(records)} entries into {options.into}")


def take_in_report(options):
    """The take-in's report: a place_build folder's (--place) or a list's."""
    if options.place:
        from score_lib import take_in_place
        if not options.commit:
            raise SystemExit("take-in --place needs --commit <the score commit that built the place>")
        taking = take_in_place.taken_in_place_saved if options.save else take_in_place.taken_in_place
        return taking(options.root, blob_store(), options.place, options.commit, options.world)
    from score_lib import take_in
    if not options.listing:
        raise SystemExit("take-in needs a list file or --place <folder>")
    taking = take_in.taken_in_saved if options.save else take_in.take_in
    return taking(options.root, blob_store(), take_in.read_listing(options.listing))


def command_take_in(options):
    report = take_in_report(options)
    if options.report:
        pathlib.Path(options.report).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(", ".join(f"{status} {len(found)}" for status, found in report.items() if status != "saved"))
    saved = report.get("saved")
    if saved:
        print(f"saved: commit {saved['committed']}, pushed {saved['pushed']} {saved['why']}".rstrip())
    raise SystemExit(1 if report["refused"] or (saved and not saved["pushed"]) else 0)


def command_test(options):
    import tempfile
    from score_lib import algorithms
    with tempfile.TemporaryDirectory(prefix="score-lib-test-") as folder:
        try:
            code = algorithms.test(options.root, blob_store(reading=True), options.address, folder,
                                   options.score or None)
        except algorithms.Refused as error:
            raise SystemExit(str(error))
        raise SystemExit(code)


def command_run(options):
    from score_lib import algorithms
    try:
        result = algorithms.run(options.root, blob_store(reading=True), options.address, options.out, options.preset,
                                options.arg or [])
    except algorithms.Refused as error:
        raise SystemExit(str(error))
    print(json.dumps(result, default=str, indent=2) if result is not None else "done")


def command_index(options):
    from score_lib import index
    index.rebuild(options.root)
    print("index rebuilt")


def command_verify(options):
    from score_lib.verify import verify
    failures = verify(options.root, blob_store(reading=True), options.address or None, options.deep, not options.no_usd,
                      options.contribution_pending)
    for address, message in failures:
        print(f"FAIL {address}: {message}")
    print(f"{len(failures)} failures")
    raise SystemExit(1 if failures else 0)


def command_quality(options):
    import os
    from score_lib import index, quality
    from score_lib.source.store import Store
    results = quality.run(options.root, blob_store(), Store(os.environ["SCORE_STORE"]), os.environ["SCORE_REPO"],
                          options.kind or (), options.address or (), options.threads, options.dry_run)
    if not options.dry_run:
        index.rebuild(options.root)
    scored = [score for score, _, _ in results.values() if score is not None]
    print(f"scored {len(scored)} of {len(results)} entries; {sum(score < 50 for score in scored)} under 50")


def command_page(options):
    from score_lib.page.build import build
    pages = build(options.root, blob_store(), options.out, options.drafts, options.public_url)
    print(f"wrote {pages} pages into {options.out}")


def command_handover(options):
    from score_lib.handover import HandoverError, handover
    command = ["score-lib", *options.argv]
    try:
        report = handover(options.root, blob_store(), options.world, options.place, options.seed, options.commit,
                          options.store, options.repo, options.out, command, options.route)
    except HandoverError as error:
        raise SystemExit(str(error))
    covered = report["covered"]
    print(f"handed {report['world']}@{report['place'] or 'world'}: {len(report['entries'])} entries, "
          f"{sum(1 for row in covered if row['entries'])} of {len(covered)} outputs held, "
          f"{len(report['refused'])} refused, saved {report['saved']['committed'] or report['saved']['why']}")


def command_clip_plan(options):
    from score_lib.clip import plan
    rows = plan(options.root, blob_store(reading=True), options.address, options.into)
    print(f"planned {sum(not row['why'] for row in rows)} clips of {len(rows)} entries into {options.into}")


def command_clip_attach(options):
    from score_lib.clip import attach
    rows = json.loads(pathlib.Path(options.clips).read_text())
    report = attach(options.root, blob_store(), rows, options.message)
    pathlib.Path(options.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"attached {len(report['attached'])} clips, {len(report['refused'])} refused, saved "
          f"{report['saved']['committed'] or report['saved']['why']}")


def command_concept(options):
    from score_lib import concept
    report = concept.hand_over(options.root, blob_store(), options.store, options.world, options.place,
                               options.commit)
    print(json.dumps(report, sort_keys=True))


def command_held(options):
    from score_lib.held import held
    rows = [{"address": record["address"], "kind": record["kind"], "state": record["state"],
             "node": record.get("provenance", {}).get("node"), "output": record.get("provenance", {}).get("output")}
            for record in held(options.root, options.world, options.place)]
    sys.stdout.write(json.dumps(rows, indent=2, sort_keys=True) + "\n")


def command_hub_mirror(options):
    from huggingface_hub import HfApi
    from score_lib.blobs import mirror
    from score_lib.blobs.s3 import S3BlobStore
    source = blob_store()
    if not isinstance(source, S3BlobStore):
        raise SystemExit("hub-mirror copies from the S3-compatible store: set SCORE_LIB_S3_ENDPOINT and "
                         "SCORE_LIB_S3_BUCKET (and its keys), and leave SCORE_LIB_BLOBS_DIR unset")
    report = mirror.mirror(pathlib.Path(options.root), source, HfApi(), options.repo, options.revision,
                           options.prefix, options.public, options.deep, options.records, options.check_only)
    if options.report:
        pathlib.Path(options.report).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(mirror.report_line(report))
    raise SystemExit(1 if report["failures"] else 0)


def command_bundle(options):
    from score_lib import bundle
    try:
        packed = bundle.pack(options.root, blob_store(reading=True), options.address, options.out)
    except bundle.Refused as why:
        raise SystemExit(f"refused: {why}")
    print(json.dumps(packed, sort_keys=True))


def command_take_bundle(options):
    from score_lib import bundle, handover
    from score_lib.blobs import NoBlobStore
    try:
        report = bundle.take(options.root, bundle.stores_from_environment(), options.source, options.pr,
                             options.contributor, options.commit)
    except (bundle.Refused, NoBlobStore) as why:
        raise SystemExit(f"refused: {why}")
    if options.save and report["entries"]:
        folders = sorted({"entries/{0}/{1}".format(*entries_of(address)) for address in report["entries"]})
        report["saved"] = handover.saved(options.root, f"take-bundle: {len(report['entries'])} entries from "
                                                       f"#{options.pr} by {options.contributor}", (*folders, "index"))
    print(json.dumps(report, sort_keys=True))


def entries_of(address):
    from score_lib import entry
    return entry.parse_address(address)[:2]


def command_review(options):
    from score_lib import bundle, review
    try:
        faults, tiles = review.review(options.root, options.base, options.contributor, options.bundle or (),
                                      options.sheet)
    except bundle.Refused as why:
        raise SystemExit(f"refused: {why}")
    for fault in faults:
        print(f"FAIL {fault}")
    if options.sheet:
        print(f"contact sheet: {options.sheet} ({tiles} pictures, model views and sounds); look at every one")
    print(f"{len(faults)} faults")
    raise SystemExit(1 if faults else 0)


def command_trace(options):
    from score_lib import contribution
    rows = contribution.traced(options.root, options.query)
    for row in rows:
        print(json.dumps(row, sort_keys=True))
    print(f"{len(rows)} entries")


def parser():
    found = argparse.ArgumentParser(prog="score-lib", description=__doc__)
    found.add_argument("--root", default=str(ROOT), help="the score_lib repository (default: this checkout)")
    commands = found.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="export a world's accepted outputs as entries")
    export.add_argument("--world", required=True)
    export.add_argument("--kind", action="append")
    export.add_argument("--place", action="append")
    export.add_argument("--dry-run", action="store_true")
    export.add_argument("--report")
    export.add_argument("--json", action="store_true")
    export.set_defaults(handle=command_export)
    audited = commands.add_parser("audit", help="count what a world holds against the library, kind by kind")
    audited.add_argument("--world", required=True)
    audited.add_argument("--report", help="write the full report (every missing thing) as JSON")
    audited.set_defaults(handle=command_audit)
    web3d = commands.add_parser("web3d", help="make the web 3D copies of model, kit, character and place entries")
    web3d.add_argument("--kind", action="append", choices=("model", "character", "kit", "place"))
    web3d.add_argument("--address", action="append")
    web3d.add_argument("--limit", type=int)
    web3d.add_argument("--report")
    web3d.set_defaults(handle=command_web3d)
    lock = commands.add_parser("lock", help="lock drafts")
    lock.add_argument("address", nargs="*")
    lock.add_argument("--all-drafts", action="store_true")
    lock.add_argument("--kind")
    lock.set_defaults(handle=command_lock)
    withdraw = commands.add_parser("withdraw", help="withdraw a locked entry or a draft")
    withdraw.add_argument("address")
    withdraw.set_defaults(handle=command_withdraw)
    fetched = commands.add_parser("fetch", help="materialise entries into a folder")
    fetched.add_argument("address", nargs="+")
    fetched.add_argument("--into", required=True)
    fetched.add_argument("--with-requires", action="store_true")
    fetched.add_argument("--file", action="append", help="only this file of the entries (repeatable)")
    fetched.set_defaults(handle=command_fetch)
    taken = commands.add_parser("take-in", help="take loose made files (no store outputs), or a place built by score's "
                                                "placing method, in as draft entries")
    taken.add_argument("listing", nargs="?",
                       help="the list of entries and their files (take_in.py's docstring has its shape)")
    taken.add_argument("--place", help="a place built by score's placing method (a place_build folder), taken in "
                                       "as a draft place entry (take_in_place.py)")
    taken.add_argument("--commit", help="with --place: the score commit that built the place")
    taken.add_argument("--world", default="world1", help="with --place: the world the place belongs to")
    taken.add_argument("--report", help="write the report (every address by status, every refusal) as JSON")
    taken.add_argument("--save", action="store_true",
                       help="under the handover's lock, commit and push the entries written and the index")
    taken.set_defaults(handle=command_take_in)
    tested = commands.add_parser("test", help="run an algorithm entry's tests")
    tested.add_argument("address")
    tested.add_argument("--score", default=os.environ.get("SCORE_REPO", ""),
                        help="a score checkout to lay in the code the library leaves out (default SCORE_REPO)")
    tested.set_defaults(handle=command_test)
    ran = commands.add_parser("run", help="run an algorithm entry's free core with a preset")
    ran.add_argument("address")
    ran.add_argument("--preset", default="world1")
    ran.add_argument("--out", required=True)
    ran.add_argument("--arg", action="append", help="name=value passed to the entry ({preset}, {out} filled in)")
    ran.set_defaults(handle=command_run)
    indexed = commands.add_parser("index", help="rebuild the index from entries/")
    indexed.set_defaults(handle=command_index)
    verified = commands.add_parser("verify", help="check entries, index, locks, blobs, the MIT admission and USD")
    verified.add_argument("--deep", action="store_true")
    verified.add_argument("--address", action="append")
    verified.add_argument("--no-usd", action="store_true", help="skip the USD check (section 8.7)")
    verified.add_argument("--contribution-pending", action="store_true",
                          help="an outside contribution before its merge: check only the contributor's half of its "
                               "trace")
    verified.set_defaults(handle=command_verify)
    scored = commands.add_parser("quality", help="score entries' quality from free local measures (reads "
                                 "SCORE_STORE and SCORE_REPO; run with stdin from /dev/null when detached)")
    scored.add_argument("--kind", action="append")
    scored.add_argument("--address", action="append")
    scored.add_argument("--threads", type=int, default=4)
    scored.add_argument("--dry-run", action="store_true")
    scored.set_defaults(handle=command_quality)
    page = commands.add_parser("page", help="write the catalogue site")
    page.add_argument("--out", required=True)
    page.add_argument("--drafts", action="store_true")
    page.add_argument("--public-url", action="store_true")
    page.set_defaults(handle=command_page)
    conceived = commands.add_parser("concept", help="make a place's concept entry follow its standing pick in the "
                                                    "SCORE store: locked on a pick, withdrawn on an unlock")
    conceived.add_argument("--world", required=True)
    conceived.add_argument("--place", required=True)
    conceived.add_argument("--commit", required=True, help="the score commit the pick was made at")
    conceived.add_argument("--store", required=True, help="the SCORE store's objects folder (read only)")
    conceived.set_defaults(handle=command_concept)
    handed = commands.add_parser("handover", help="hand a place's accepted outputs to the library as drafts and "
                                                  "write handover.json")
    handed.add_argument("--world", required=True)
    handed.add_argument("--place", help="the place; left out for the world's own node")
    handed.add_argument("--seed", required=True, type=int)
    handed.add_argument("--commit", required=True, help="the score commit the outputs were read at")
    handed.add_argument("--store", required=True, help="the SCORE store's objects folder (read only)")
    handed.add_argument("--repo", required=True, help="the score repository (read only)")
    handed.add_argument("--out", required=True, help="the folder handover.json is written into")
    handed.add_argument("--route", help="the route score made the outputs under, stamped into each entry's provenance")
    handed.set_defaults(handle=command_handover)
    holding = commands.add_parser("held", help="the newest entries a world's place (or its own node) holds, as JSON")
    holding.add_argument("--world", required=True)
    holding.add_argument("--place", help="the place; left out for the world's own node")
    holding.set_defaults(handle=command_held)
    clip_plan = commands.add_parser("clip-plan", help="what each entry's preview clip opens, its entries fetched")
    clip_plan.add_argument("address", nargs="+")
    clip_plan.add_argument("--into", required=True, help="the folder the entries are fetched into, the plan written")
    clip_plan.set_defaults(handle=command_clip_plan)
    clip_attach = commands.add_parser("clip-attach", help="put rendered preview clips into their draft entries")
    clip_attach.add_argument("--clips", required=True,
                             help="JSON: [{address, file, shelf, frames, seconds, size, made_by}]")
    clip_attach.add_argument("--out", required=True, help="the report written")
    clip_attach.add_argument("--message", default="clips: preview clips made at handover")
    clip_attach.set_defaults(handle=command_clip_attach)
    hub = commands.add_parser("hub-mirror", help="copy the blobs the entries name from the S3-compatible store into a "
                                                 "Hugging Face dataset repository, then check the copy")
    hub.add_argument("repo", help="the dataset repository, owner/name")
    hub.add_argument("--revision", default="main")
    hub.add_argument("--prefix", default="", help="a path prefix in the repository")
    hub.add_argument("--public", action="store_true", help="make the repository public when it is created")
    hub.add_argument("--deep", type=int, default=0, help="also download and hash this many random blobs")
    hub.add_argument("--records", help="the records' link for the card (default: this checkout's origin)")
    hub.add_argument("--check-only", action="store_true", help="only check the copy, write nothing")
    hub.add_argument("--report", help="write the report (every failure) as JSON")
    hub.set_defaults(handle=command_hub_mirror)
    packing = commands.add_parser("bundle", help="pack your new entries' records and bytes into one bundle file for "
                                                 "your pull request")
    packing.add_argument("address", nargs="+")
    packing.add_argument("--out", required=True, help="the bundle file (<name>.tgz)")
    packing.set_defaults(handle=command_bundle)
    taking = commands.add_parser("take-bundle", help="maintainers, on merge: check a pull request's bundles, put "
                                                     "their bytes in every blob store and trace each entry")
    taking.add_argument("source", nargs="+", help="a bundle's https URL or file")
    taking.add_argument("--pr", type=int, required=True)
    taking.add_argument("--contributor", required=True, help="the pull request author's GitHub handle")
    taking.add_argument("--commit", required=True, help="the pull request's signed-off head commit (full sha)")
    taking.add_argument("--save", action="store_true", help="commit and push the traced entries and the index")
    taking.set_defaults(handle=command_take_bundle)
    reviewed = commands.add_parser("review", help="maintainers: check an outside pull request (its diff, sign-offs, "
                                                  "entries and bundles) and draw its contact sheet")
    reviewed.add_argument("--base", required=True, help="the branch it goes into, e.g. origin/main")
    reviewed.add_argument("--contributor", required=True, help="the pull request author's GitHub handle")
    reviewed.add_argument("--bundle", action="append", help="a bundle's https URL or file")
    reviewed.add_argument("--sheet", help="where to draw the contact sheet (a .png)")
    reviewed.set_defaults(handle=command_review)
    traced = commands.add_parser("trace", help="who added an entry or a blob, from which pull request, on what claim")
    traced.add_argument("query", help="an address, a blob's sha256, handle:<h>, source:<id> or pr:<n>")
    traced.set_defaults(handle=command_trace)
    return found


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    options = parser().parse_args(argv)
    options.argv = argv
    if options.command == "lock" and options.all_drafts and not options.kind:
        raise SystemExit("--all-drafts needs --kind")
    options.handle(options)
