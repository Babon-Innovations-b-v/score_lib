"""The catalogue site: index.html (a section per kind, a card per entry: picture, title, version, state, licence and
commercial flag, places) and <kind>/<id>/<version>.html per entry. Pictures and the sound players' takes are copied
from the blob store (or linked through SCORE_LIB_PUBLIC_URL)."""
import html
import json
import pathlib
import posixpath
import tempfile

from score_lib import entry as entries

TEMPLATE = pathlib.Path(__file__).with_name("template.html")
NAMES = {"algorithm": "Engine algorithms", "surface": "Surfaces", "model": "Models", "light": "Skies",
         "sound": "Sounds", "effect": "Effects", "character": "Characters", "ground": "Grounds", "kit": "Kits",
         "layer": "Layers", "place": "Places"}


class OutsideRepo(ValueError):
    """The site is written outside the repository."""


def shown(root, drafts):
    """The entries the site shows: newest version of each id, withdrawn left out, drafts only when asked."""
    newest = {}
    for record in entries.all_entries(root):
        if record["state"] == "withdrawn" or (record["state"] == "draft" and not drafts):
            continue
        newest[(record["kind"], record["id"])] = record
    return list(newest.values())


def page(title, content):
    return TEMPLATE.read_text().replace("{{title}}", html.escape(title)).replace("{{content}}", content)


class Site:
    """Writes the site's files and copies or links the blobs it shows."""

    def __init__(self, blobs, out, public):
        self.blobs = blobs
        self.out = pathlib.Path(out)
        self.public = public

    def blob_link(self, sha, extension, folder):
        """A link (relative to the site's root) to a blob, copied once into <folder>/<sha><extension>, or its
        public URL."""
        if self.public and self.blobs.url(sha):
            return self.blobs.url(sha)
        name = f"{folder}/{sha}{extension}"
        target = self.out / name
        if not target.exists():
            self.blobs.get(sha, target)
        return name

    def picture(self, record):
        """The link of an entry's picture, or None."""
        picture = record.get("picture") or {}
        described = record["files"].get(picture.get("file") or "")
        if not described:
            return None
        return self.blob_link(described["sha256"], posixpath.splitext(picture["file"])[1] or ".png", "pictures")

    def write(self, name, text):
        path = self.out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def badges(record):
    licence = record["licence"]
    flag = "ok" if licence["commercial_ok"] else "no"
    return (f'<div class="badges"><span class="badge {record["state"]}">{record["state"]}</span>'
            f'<span class="badge">v{record["version"]}</span>'
            f'<span class="badge {flag}">{html.escape(licence["spdx"])}'
            f'{" · commercial" if licence["commercial_ok"] else " · not commercial"}</span></div>')


def card(site, record, prefix=""):
    """One card of the front page."""
    link = site.picture(record)
    if link:
        source = link if link.startswith("http") else prefix + link
        inside = f'<img loading="lazy" src="{html.escape(source)}" alt="">'
    else:
        inside = f'<div class="none">{html.escape(record.get("picture_reason", "no picture"))}</div>'

    places = ", ".join(record.get("seen_in", [])[:6]) + (" …" if len(record.get("seen_in", [])) > 6 else "")
    target = f"{record['kind']}/{record['id']}/{record['version']}.html"
    return (f'<a class="card" href="{prefix}{html.escape(target)}"><div class="pic">{inside}</div>'
            f'<div class="meta"><b>{html.escape(record["title"])}</b>{badges(record)}'
            f'{f"<div class=places>{html.escape(places)}</div>" if places else ""}</div></a>')


def front(site, records):
    """index.html's content."""
    by_kind = {}
    for record in records:
        by_kind.setdefault(record["kind"], []).append(record)
    kinds = [kind for kind in entries.KINDS if kind in by_kind]
    nav = "".join(f'<a href="#{kind}">{NAMES[kind]}<span>{len(by_kind[kind])}</span></a>' for kind in kinds)
    sections = []
    for kind in kinds:
        cards = "".join(card(site, record) for record in sorted(by_kind[kind], key=lambda each: each["title"].lower()))
        sections.append(f'<section id="{kind}"><h2>{NAMES[kind]}</h2><div class="grid">{cards}</div></section>')
    header = ("<header><h1>score_lib</h1><p>Reusable parts of every OpenUSD layer of a SCORE world: "
              f"{len(records)} entries.</p></header>")
    return header + f'<nav class="kinds">{nav}</nav>' + "".join(sections)


def files_table(record):
    rows = "".join(f"<tr><td>{html.escape(name)}</td><td>{described['role']}</td><td>{described['bytes']:,}</td></tr>"
                   for name, described in sorted(record["files"].items()))
    return f'<div class="scroll"><table><tr><th>file</th><th>role</th><th>bytes</th></tr>{rows}</table></div>'


def requires_list(record, prefix):
    items = []
    for address in record.get("requires", []):
        kind, entry_id, version = entries.parse_address(address)
        items.append(f'<li><a href="{prefix}{kind}/{entry_id}/{version}.html">{html.escape(address)}</a></li>')
    return f"<ul>{''.join(items)}</ul>" if items else "<p>none</p>"


def sound_player(site, record, prefix):
    """An audio player for a sound entry's first take."""
    takes = sorted(name for name, described in record["files"].items() if described["role"] == "audio")
    if not takes:
        return ""
    link = site.blob_link(record["files"][takes[0]]["sha256"], posixpath.splitext(takes[0])[1], "audio")
    source = link if link.startswith("http") else prefix + link
    return f'<audio controls preload="none" src="{html.escape(source)}"></audio>'


def algorithm_parts(site, record):
    """The settings table and the world 1 preset files of an algorithm entry."""
    described = record["files"].get("settings.schema.json")
    if not described:
        return ""
    with tempfile.TemporaryDirectory() as folder:
        target = pathlib.Path(folder) / "settings.schema.json"
        site.blobs.get(described["sha256"], target)
        schema = json.loads(target.read_text())
    rows = []
    for name, field in sorted(schema["properties"]["constants"]["properties"].items()):
        value = json.dumps(field.get("default")) if "default" in field else html.escape(field.get("expression", ""))
        rows.append(f"<tr><td>{html.escape(name)}</td><td>{html.escape(str(value))}</td>"
                    f"<td>{html.escape(field.get('$comment', ''))}</td></tr>")
    for name, field in sorted(schema["properties"]["record"]["properties"].items()):
        rows.append(f"<tr><td>{html.escape(name)}</td><td>{html.escape(field.get('type', ''))}</td>"
                    f"<td>{html.escape(field.get('description', ''))}</td></tr>")
    presets = [name for name in sorted(record["files"]) if name.startswith("presets/world1/")]
    preset_list = "".join(f"<li>{html.escape(name[len('presets/world1/'):])}</li>" for name in presets)
    return (f"<h2>Settings</h2><div class=scroll><table><tr><th>name</th><th>default or type</th><th>source</th></tr>"
            f"{''.join(rows)}</table></div><h2>World 1 preset</h2><ul>{preset_list}</ul>")


def entry_page(site, record):
    """<kind>/<id>/<version>.html's content."""
    prefix = "../../../"
    link = site.picture(record)
    picture = (f'<div class="big"><img src="{html.escape(link if link.startswith("http") else prefix + link)}" '
               f'alt=""></div>') if link else f'<p>{html.escape(record.get("picture_reason", "no picture"))}</p>'
    usd = record.get("usd") or {}
    facts = [("address", record["address"]), ("state", record["state"]), ("licence", record["licence"]["spdx"]),
             ("commercial use", "yes" if record["licence"]["commercial_ok"] else "no"),
             ("licence note", record["licence"]["note"]), ("seen in", ", ".join(record.get("seen_in", []))),
             ("use in USD", f"{usd.get('use')} @{usd.get('path')}@" if usd else "not a USD entry")]
    listed = "".join(f"<dt>{html.escape(name)}</dt><dd>{html.escape(str(value))}</dd>" for name, value in facts
                     if value)
    extra = sound_player(site, record, prefix) if record["kind"] == "sound" else ""
    extra += algorithm_parts(site, record) if record["kind"] == "algorithm" else ""
    provenance = html.escape(json.dumps(record.get("provenance", {}), indent=2, sort_keys=True))
    return (f'<p><a href="{prefix}index.html">score_lib</a> / {NAMES[record["kind"]]}</p>'
            f'<div class="entry"><div>{picture}{extra}</div><div><h1>{html.escape(record["title"])}</h1>'
            f'{badges(record)}<dl>{listed}</dl><h2>Files</h2>{files_table(record)}'
            f'<h2>Requires</h2>{requires_list(record, prefix)}<h2>Licence components</h2>'
            f'<pre>{html.escape(json.dumps(record["licence"]["components"], indent=2))}</pre>'
            f'<h2>Provenance</h2><pre>{provenance}</pre></div></div>')


def build(root, blobs, out, drafts=False, public=False):
    """Write the site; the number of pages written."""
    out = pathlib.Path(out).resolve()
    if out.is_relative_to(pathlib.Path(root).resolve()):
        raise OutsideRepo(f"{out} is inside the repository; the site is written outside it")
    site = Site(blobs, out, public)
    records = shown(root, drafts)
    site.write("index.html", page("score_lib catalogue", front(site, records)))
    for record in records:
        site.write(f"{record['kind']}/{record['id']}/{record['version']}.html",
                   page(record["title"], entry_page(site, record)))
    return len(records) + 1
