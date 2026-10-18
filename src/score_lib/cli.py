"""score-lib: argument parsing only, one function per command."""
import argparse
import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]


def blob_store():
    from score_lib.blobs import NoBlobStore, from_environment
    try:
        return from_environment()
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
    from score_lib.fetch import fetch
    records = fetch(options.root, blob_store(), options.address, options.into, options.with_requires)
    print(f"fetched {len(records)} entries into {options.into}")


def command_test(options):
    import tempfile
    from score_lib import algorithms
    with tempfile.TemporaryDirectory(prefix="score-lib-test-") as folder:
        raise SystemExit(algorithms.test(options.root, blob_store(), options.address, folder))


def command_run(options):
    from score_lib import algorithms
    try:
        result = algorithms.run(options.root, blob_store(), options.address, options.out, options.preset,
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
    failures = verify(options.root, blob_store(), options.address or None, options.deep, not options.no_usd)
    for address, message in failures:
        print(f"FAIL {address}: {message}")
    print(f"{len(failures)} failures")
    raise SystemExit(1 if failures else 0)


def command_page(options):
    from score_lib.page.build import build
    pages = build(options.root, blob_store(), options.out, options.drafts, options.public_url)
    print(f"wrote {pages} pages into {options.out}")


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
    lock = commands.add_parser("lock", help="lock drafts")
    lock.add_argument("address", nargs="*")
    lock.add_argument("--all-drafts", action="store_true")
    lock.add_argument("--kind")
    lock.set_defaults(handle=command_lock)
    withdraw = commands.add_parser("withdraw", help="withdraw a locked entry")
    withdraw.add_argument("address")
    withdraw.set_defaults(handle=command_withdraw)
    fetched = commands.add_parser("fetch", help="materialise entries into a folder")
    fetched.add_argument("address", nargs="+")
    fetched.add_argument("--into", required=True)
    fetched.add_argument("--with-requires", action="store_true")
    fetched.set_defaults(handle=command_fetch)
    tested = commands.add_parser("test", help="run an algorithm entry's tests")
    tested.add_argument("address")
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
    verified.set_defaults(handle=command_verify)
    page = commands.add_parser("page", help="write the catalogue site")
    page.add_argument("--out", required=True)
    page.add_argument("--drafts", action="store_true")
    page.add_argument("--public-url", action="store_true")
    page.set_defaults(handle=command_page)
    return found


def main(argv=None):
    options = parser().parse_args(argv)
    if options.command == "lock" and options.all_drafts and not options.kind:
        raise SystemExit("--all-drafts needs --kind")
    options.handle(options)
