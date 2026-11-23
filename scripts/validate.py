#!/usr/bin/env python3
"""Validate the catalog entries in remotes/*.yaml.

Checks every entry against schema/catalog-entry.schema.json, then applies this
repo's lint rules (see docs/catalog-entry-spec.md). With --network it also
downloads each entry's icon URL and checks it is byte-identical to the local
copy in icons/<entryKey>.svg (or .png when no SVG exists upstream).

Usage:
    uv run scripts/validate.py [--network] [--print-ids] [--root DIR] [FILE ...]

Exit status is 1 if any error is found. Warnings never fail the run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = Path("schema/catalog-entry.schema.json")
ENTRY_DIR = "remotes"
ICON_DIR = "icons"
USER_AGENT = "dcode-mcp-catalog-validator/0.1 (https://github.com/dcode/mcp-catalog)"

# entryKey prefix -> meaning. Keep in sync with docs/catalog-entry-spec.md.
ALLOWED_PREFIXES: dict[str, str] = {
    "gws-": "Google Workspace official remote MCP servers",
    "dcode-": "Everything else: self-hosted or third-party servers added to this catalog",
}

# Display names already used by Obot's default catalog
# (obot-platform/mcp-catalog). The entry object ID is derived only from the
# sanitized display name, so reusing one would overwrite that entry.
# The full list is vendored in UPSTREAM_NAMES_FILE (refresh with
# `validate.py --refresh-upstream-names <checkout>`); CI also compares against
# a fresh shallow clone via --upstream. These few are kept as a floor.
RESERVED_NAMES: frozenset[str] = frozenset(
    {"Gmail", "Google Calendar", "Google Drive", "Google Docs", "Google Sheets"}
)
UPSTREAM_NAMES_FILE = "schema/upstream-names.txt"

GOOGLE_FIXED_URL = re.compile(r"^https://[a-z]+\.googleapis\.com/mcp/v1$")
GOOGLE_ICON_HOST = "upload.wikimedia.org"
# Hosts that stop working if this repo becomes private.
FORBIDDEN_ICON_HOSTS = frozenset({"raw.githubusercontent.com", "github.com"})

LEGACY_FIELDS = {
    ("serverUserType",): "removed in Obot v0.28; omit it (Obot ignores it and `obot mcp convert-catalog` deletes it)",
    ("env",): "legacy field; Obot sync hard-fails. Use top-level `config` with usage: env",
    ("multiUserConfig",): "legacy field; use top-level `config`",
    ("remoteConfig", "headers"): "legacy field; Obot sync hard-fails. Use top-level `config` with usage: header",
    ("remoteConfig", "tunnelName"): "rejected on git-synced catalog entries",
}

CREDENTIAL_PARAM = re.compile(
    r"(api[-_]?key|apikey|key|token|secret|password|passwd|pwd|auth|authorization|"
    r"credential|access[-_]?token|client[-_]?secret|sig|signature)",
    re.IGNORECASE,
)
BANNED_PHRASE = re.compile(r"model\s+context\s+protocol", re.IGNORECASE)
TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bignore\s+(all\s+|any\s+)?(previous|prior|above|earlier)\s+(instructions|prompts|messages)",
        r"\bdisregard\s+(all\s+|any\s+)?(previous|prior|above|earlier|system)",
        r"<\s*/?\s*(system|important|instructions?|secret)\s*>",
        r"\b(do\s+not|don't|never)\s+(tell|inform|reveal\s+to|mention\s+to)\s+the\s+user",
        r"\byou\s+are\s+now\b",
        r"\bsystem\s+prompt\b",
    )
]
URL_IN_TEXT = re.compile(r"https?://", re.IGNORECASE)
MAX_TOOL_DESCRIPTION = 1024
SVG_NS = "http://www.w3.org/2000/svg"
ICON_EXTENSIONS = (".svg", ".png")  # SVG preferred; PNG only when upstream has no SVG
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_PNG_BYTES = 256 * 1024


@dataclass(frozen=True)
class Problem:
    level: str  # "error" or "warning"
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.level.upper()}: {self.path}: {self.message}"


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys, like Obot does."""


def _construct_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", key_node.start_mark
            )
        seen.add(key)
    return loader.construct_mapping(node, deep)


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def load_yaml(text: str) -> Any:
    return yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 - SafeLoader subclass


def sanitize_name(name: str) -> str:
    """Approximate Obot's SanitizeName: lowercase, non-alphanumerics -> '-'."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def object_id(name: str, catalog: str = "default") -> str:
    """Obot entry object name: nah SafeHashConcatName(catalog, sanitized name).

    Names of 64+ characters are truncated by Obot; verify those via the API.
    """
    sanitized = sanitize_name(name)
    digest = hashlib.sha256(f"{catalog}\0{sanitized}\0".encode()).hexdigest()[:8]
    return f"{catalog}-{sanitized}-{digest}"


def load_schema(root: Path) -> Draft202012Validator:
    schema = json.loads((root / SCHEMA_PATH).read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _get(data: dict, path: tuple[str, ...]) -> Any:
    cur: Any = data
    for part in path:
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def check_url(url: str, field: str, rel: str) -> list[Problem]:
    problems: list[Problem] = []
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        problems.append(Problem("error", rel, f"{field} must use https: {url}"))
    if parsed.username or parsed.password:
        problems.append(Problem("error", rel, f"{field} must not embed credentials (userinfo)"))
    for key, _ in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        if CREDENTIAL_PARAM.search(key):
            problems.append(
                Problem("error", rel, f"{field} has a credential-looking query parameter {key!r}; use OAuth or a sensitive header config")
            )
    return problems


def description_has_docs_link(description: str) -> bool:
    """True if a markdown https link appears before the first '## ' heading."""
    head = re.split(r"^##\s", description, maxsplit=1, flags=re.MULTILINE)[0]
    return re.search(r"\[[^\]]+\]\(https://[^)\s]+\)", head) is not None


def check_svg(path: Path, rel: str) -> list[Problem]:
    problems: list[Problem] = []
    raw = path.read_bytes()
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        return [Problem("error", rel, "SVG must not contain a DOCTYPE or ENTITY declaration")]
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        return [Problem("error", rel, f"SVG is not well-formed XML: {exc}")]
    if root.tag not in (f"{{{SVG_NS}}}svg", "svg"):
        problems.append(Problem("error", rel, f"root element must be <svg>, got {root.tag}"))
    if "viewBox" not in root.attrib:
        if "width" in root.attrib and "height" in root.attrib:
            # Upstream (Wikimedia) files are kept byte-identical, so only warn.
            problems.append(Problem("warning", rel, "root <svg> has no viewBox (width/height only); scaling relies on the <img> box"))
        else:
            problems.append(Problem("error", rel, "root <svg> must have a viewBox or width and height"))
    for el in root.iter():
        local = el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""
        if local.lower() in ("script", "foreignobject"):
            problems.append(Problem("error", rel, f"SVG must not contain <{local}>"))
        for attr, value in el.attrib.items():
            name = attr.rsplit("}", 1)[-1]
            if name.lower().startswith("on"):
                problems.append(Problem("error", rel, f"SVG must not use event handler attribute {name!r}"))
            if name == "href" and not value.startswith("#"):
                problems.append(Problem("error", rel, f"SVG must not reference external resources (href={value[:60]!r})"))
            if re.search(r"url\(\s*['\"]?(?!#)", value):
                problems.append(Problem("error", rel, f"SVG attribute {name!r} references a non-local url()"))
        if local == "style" and el.text and (re.search(r"url\(\s*['\"]?(?!#)", el.text) or "@import" in el.text):
            problems.append(Problem("error", rel, "SVG <style> must not import or reference external resources"))
    return problems


def check_png(path: Path, rel: str) -> list[Problem]:
    raw = path.read_bytes()
    if not raw.startswith(PNG_MAGIC):
        return [Problem("error", rel, "not a PNG file")]
    if len(raw) > MAX_PNG_BYTES:
        return [Problem("warning", rel, f"PNG is {len(raw)} bytes; prefer an SVG or a smaller PNG")]
    return []


def local_icon(root: Path, key: str) -> Path | None:
    """Local copy of an entry's icon: icons/<entryKey>.svg, else .png."""
    for ext in ICON_EXTENSIONS:
        candidate = root / ICON_DIR / f"{key}{ext}"
        if candidate.is_file():
            return candidate
    return None


def _text_issues(text: str) -> list[str]:
    issues: list[str] = []
    hidden = sorted(
        {
            f"U+{ord(ch):04X}"
            for ch in text
            if unicodedata.category(ch) in ("Cf", "Co", "Cs")
            or (unicodedata.category(ch) == "Cc" and ch not in "\n\t")
        }
    )
    if hidden:
        issues.append(f"hidden/control characters {', '.join(hidden)}")
    for pattern in INJECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            issues.append(f"prompt-injection-like phrase {match.group(0)!r}")
    return issues


def check_tool_preview(tools: Any, rel: str) -> list[Problem]:
    """Deterministic lint of tool names/descriptions for hidden instructions."""
    problems: list[Problem] = []
    if not isinstance(tools, list):
        return problems
    seen: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name", ""))
        where = f"toolPreview[{name}]"
        if not TOOL_NAME.match(name):
            problems.append(Problem("error", rel, f"{where}: invalid tool name"))
        if name in seen:
            problems.append(Problem("error", rel, f"{where}: duplicate tool name"))
        seen.add(name)
        desc = str(tool.get("description", ""))
        texts = {"description": desc}
        for param, pdesc in (tool.get("params") or {}).items():
            texts[f"params.{param}"] = str(pdesc)
        for field, text in texts.items():
            for issue in _text_issues(text):
                problems.append(Problem("error", rel, f"{where}.{field}: {issue}"))
            if URL_IN_TEXT.search(text):
                problems.append(Problem("warning", rel, f"{where}.{field}: contains a URL; review it"))
        if len(desc) > MAX_TOOL_DESCRIPTION:
            problems.append(Problem("warning", rel, f"{where}: description is {len(desc)} chars (> {MAX_TOOL_DESCRIPTION})"))
    return problems


def _catalog_names(data: Any) -> list[str]:
    items = data if isinstance(data, list) else [data]
    names = []
    for item in items:
        if isinstance(item, dict) and item.get("type") in (None, "entry") and isinstance(item.get("name"), str):
            names.append(item["name"])
    return names


def scan_upstream_names(checkout: Path) -> set[str]:
    """Collect entry display names from a checkout of obot-platform/mcp-catalog."""
    names: set[str] = set()
    for path in sorted(checkout.rglob("*")):
        rel = path.relative_to(checkout).parts
        if path.suffix not in (".yaml", ".yml", ".json") or not path.is_file():
            continue
        if any(part.startswith(".") for part in rel) or rel[0] == "scripts":
            continue
        try:
            names.update(_catalog_names(yaml.safe_load(path.read_text(encoding="utf-8"))))
        except (yaml.YAMLError, UnicodeDecodeError):
            continue
    return names


def load_reserved_names(root: Path, upstream: Path | None = None) -> frozenset[str]:
    """Sanitized names of every entry in Obot's default catalog."""
    names = set(RESERVED_NAMES)
    vendored = root / UPSTREAM_NAMES_FILE
    if vendored.is_file():
        names.update(
            line.strip() for line in vendored.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")
        )
    if upstream is not None:
        names.update(scan_upstream_names(upstream))
    return frozenset(sanitize_name(n) for n in names)


def check_entry_text(data: dict, rel: str) -> list[Problem]:
    """Lint the entry's own prose (name, shortDescription, description) for hidden instructions."""
    problems: list[Problem] = []
    for field in ("name", "shortDescription", "description"):
        value = data.get(field)
        if isinstance(value, str):
            problems += [Problem("error", rel, f"{field}: {issue}") for issue in _text_issues(value)]
    return problems


def check_entry(
    path: Path, root: Path, validator: Draft202012Validator, reserved: frozenset[str] | None = None
) -> tuple[dict | None, list[Problem]]:
    """Validate one entry file. Returns (parsed entry or None, problems)."""
    rel = path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)
    problems: list[Problem] = []
    text = path.read_text(encoding="utf-8")

    if path.suffix != ".yaml":
        problems.append(Problem("error", rel, "entry files must use the .yaml extension"))
    if BANNED_PHRASE.search(text):
        problems.append(Problem("error", rel, 'say "MCP", not "Model Context Protocol"'))

    try:
        data = load_yaml(text)
    except yaml.YAMLError as exc:
        return None, problems + [Problem("error", rel, f"YAML error: {exc}")]
    if not isinstance(data, dict):
        return None, problems + [Problem("error", rel, "file must contain exactly one mapping (one entry per file)")]
    if data.get("type") == "vmcp":
        return None, problems + [Problem("error", rel, "vMCP items belong in vmcps/, not remotes/")]

    for field_path, why in LEGACY_FIELDS.items():
        if _get(data, field_path) is not None:
            problems.append(Problem("error", rel, f"{'.'.join(field_path)}: {why}"))

    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
        loc = ".".join(str(p) for p in err.absolute_path) or "<root>"
        problems.append(Problem("error", rel, f"schema: {loc}: {err.message}"))

    key = data.get("entryKey")
    if isinstance(key, str):
        if key != path.stem:
            problems.append(Problem("error", rel, f"filename must be {key}.yaml (entryKey is {key!r})"))
        if not any(key.startswith(p) for p in ALLOWED_PREFIXES):
            problems.append(
                Problem("error", rel, f"entryKey {key!r} must start with one of {', '.join(ALLOWED_PREFIXES)}")
            )

    if reserved is None:
        reserved = load_reserved_names(root)
    name = data.get("name")
    if isinstance(name, str) and sanitize_name(name) in reserved:
        problems.append(
            Problem("error", rel, f"name {name!r} collides with an entry in Obot's default catalog; pick a distinct display name")
        )

    desc = data.get("description")
    if isinstance(desc, str) and not description_has_docs_link(desc):
        problems.append(Problem("error", rel, "description must link to official docs (markdown https link) before the first '## ' heading"))

    problems += check_entry_text(data, rel)

    remote = data.get("remoteConfig") if isinstance(data.get("remoteConfig"), dict) else {}
    for field in ("fixedURL", "urlTemplate"):
        if isinstance(remote.get(field), str):
            problems += check_url(remote[field], f"remoteConfig.{field}", rel)

    icon = data.get("icon")
    if isinstance(icon, str):
        host = urllib.parse.urlsplit(icon).hostname or ""
        if not icon.startswith("https://"):
            problems.append(Problem("error", rel, "icon must be an https URL"))
        if host in FORBIDDEN_ICON_HOSTS:
            problems.append(Problem("error", rel, f"icon must not be hosted on {host} (this repo may become private)"))

    if isinstance(key, str) and key.startswith("gws-"):
        fixed = remote.get("fixedURL")
        if not (isinstance(fixed, str) and GOOGLE_FIXED_URL.match(fixed)):
            problems.append(Problem("error", rel, f"gws- entries need remoteConfig.fixedURL matching {GOOGLE_FIXED_URL.pattern}"))
        if remote.get("staticOAuthRequired") is not True:
            problems.append(Problem("error", rel, "gws- entries need remoteConfig.staticOAuthRequired: true"))
        if data.get("config"):
            problems.append(Problem("error", rel, "gws- entries are OAuth only and must not define config"))
        categories = _get(data, ("metadata", "categories"))
        if not (isinstance(categories, str) and "Google Workspace" in [c.strip() for c in categories.split(",")]):
            problems.append(Problem("error", rel, "gws- entries need 'Google Workspace' in metadata.categories"))
        if isinstance(icon, str) and urllib.parse.urlsplit(icon).hostname != GOOGLE_ICON_HOST:
            problems.append(Problem("error", rel, f"gws- icons must be served from {GOOGLE_ICON_HOST}"))

    if isinstance(key, str):
        local = local_icon(root, key)
        if local is None:
            problems.append(Problem("error", rel, f"missing local icon copy {ICON_DIR}/{key}.svg (or .png)"))
        else:
            local_rel = f"{ICON_DIR}/{local.name}"
            if isinstance(icon, str):
                url_ext = Path(urllib.parse.unquote(urllib.parse.urlsplit(icon).path)).suffix.lower()
                if url_ext != local.suffix:
                    problems.append(Problem("error", rel, f"icon URL is {url_ext or 'extensionless'} but local copy is {local_rel}"))
            problems += check_svg(local, local_rel) if local.suffix == ".svg" else check_png(local, local_rel)

    problems += check_tool_preview(data.get("toolPreview"), rel)
    return data, problems


def fetch(url: str, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - https enforced above
        return resp.read()


def check_icon_remote(entry: dict, root: Path, rel: str, fetcher=fetch) -> list[Problem]:
    icon, key = entry.get("icon"), entry.get("entryKey")
    local = local_icon(root, key) if isinstance(key, str) else None
    if not (isinstance(icon, str) and icon.startswith("https://") and local is not None):
        return []
    try:
        remote = fetcher(icon)
    except Exception as exc:  # noqa: BLE001 - report any download failure
        return [Problem("error", rel, f"could not download icon {icon}: {exc}")]
    if remote != local.read_bytes():
        return [Problem("error", rel, f"icon URL content differs from {ICON_DIR}/{local.name}")]
    return []


def validate_repo(
    root: Path, files: list[Path] | None = None, network: bool = False, fetcher=fetch, upstream: Path | None = None
) -> tuple[list[dict], list[Problem]]:
    validator = load_schema(root)
    reserved = load_reserved_names(root, upstream)
    all_files = sorted((root / ENTRY_DIR).glob("*")) if (root / ENTRY_DIR).is_dir() else []
    all_files = [f for f in all_files if f.is_file() and not f.name.startswith(".")]
    targets = {f.resolve() for f in (files or all_files)}
    entries: list[dict] = []
    problems: list[Problem] = []
    if not all_files:
        problems.append(Problem("warning", ENTRY_DIR, "no entries found"))

    # Uniqueness is always checked across the whole directory.
    keys: dict[str, str] = {}
    names: dict[str, str] = {}
    for path in all_files:
        data, file_problems = check_entry(path, root, validator, reserved)
        rel = path.relative_to(root).as_posix()
        if path.resolve() in targets:
            problems += file_problems
            if data is not None and network:
                problems += check_icon_remote(data, root, rel, fetcher)
        if data is None:
            continue
        entries.append(data)
        key, name = data.get("entryKey"), data.get("name")
        if isinstance(key, str):
            if key in keys:
                problems.append(Problem("error", rel, f"duplicate entryKey {key!r} (also in {keys[key]})"))
            keys.setdefault(key, rel)
        if isinstance(name, str):
            san = sanitize_name(name)
            if san in names:
                problems.append(Problem("error", rel, f"display name {name!r} maps to the same Obot object as {names[san]}"))
            names.setdefault(san, rel)

    icon_dir = root / ICON_DIR
    if icon_dir.is_dir():
        for icon_file in sorted(icon_dir.iterdir()):
            if icon_file.suffix not in ICON_EXTENSIONS:
                problems.append(Problem("error", f"{ICON_DIR}/{icon_file.name}", "only .svg and .png icons are allowed"))
            elif icon_file.stem not in keys:
                problems.append(Problem("warning", f"{ICON_DIR}/{icon_file.name}", "icon has no matching entry"))
    return entries, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", type=Path, help="entry files to report on (default: all remotes/*)")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root")
    parser.add_argument("--network", action="store_true", help="download icon URLs and compare with the local copies in icons/")
    parser.add_argument("--print-ids", action="store_true", help="print entryKey -> Obot entry object ID")
    parser.add_argument(
        "--upstream", type=Path, help="checkout of obot-platform/mcp-catalog; its entry names are reserved too"
    )
    parser.add_argument(
        "--refresh-upstream-names",
        type=Path,
        metavar="CHECKOUT",
        help=f"rewrite {UPSTREAM_NAMES_FILE} from a checkout of obot-platform/mcp-catalog and exit",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    if args.refresh_upstream_names:
        names = sorted(scan_upstream_names(args.refresh_upstream_names))
        header = (
            "# Entry display names in Obot's default catalog (github.com/obot-platform/mcp-catalog).\n"
            "# Generated by `scripts/validate.py --refresh-upstream-names <checkout>`; do not edit by hand.\n"
        )
        (root / UPSTREAM_NAMES_FILE).write_text(header + "".join(f"{n}\n" for n in names), encoding="utf-8")
        print(f"wrote {len(names)} names to {UPSTREAM_NAMES_FILE}", file=sys.stderr)
        return 0
    files = [f if f.is_absolute() else (Path.cwd() / f) for f in args.files]
    files = [f for f in files if f.suffix in (".yaml", ".yml") and (root / ENTRY_DIR) in f.resolve().parents]
    if args.files and not files:
        return 0  # e.g. pre-commit passed only non-entry files
    entries, problems = validate_repo(root, files or None, network=args.network, upstream=args.upstream)

    for problem in problems:
        print(problem, file=sys.stderr)
    errors = sum(1 for p in problems if p.level == "error")
    warnings = len(problems) - errors

    if args.print_ids:
        for entry in sorted(entries, key=lambda e: str(e.get("entryKey"))):
            name = str(entry.get("name", ""))
            oid = object_id(name)
            note = "  (>=64 chars: Obot truncates; verify via API)" if len(oid) >= 64 else ""
            print(f"{entry.get('entryKey')}\t{oid}{note}")

    print(f"{len(entries)} entries checked: {errors} error(s), {warnings} warning(s)", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
