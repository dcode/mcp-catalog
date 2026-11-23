#!/usr/bin/env python3
"""Regenerate `toolPreview` from each remote server's live tools/list.

Google's Workspace MCP servers answer `initialize` and `tools/list` without
credentials, so the preview can be generated deterministically (no LLM).

For every remotes/*.yaml entry with a remoteConfig.fixedURL (or just the
entries named with --entry / FILE arguments) this script calls initialize and
tools/list unauthenticated and maps each tool to
    {name, description: <summary>, params: {prop: <summary of its description>}}
where <summary> is the first paragraph, whitespace collapsed, cut to whole
sentences within 400 characters.

Modes:
    --check   (default) exit 1 if any toolPreview differs from the live server
    --write   rewrite toolPreview in place, preserving the rest of the file
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from ruamel.yaml import YAML
from validate import _text_issues
from ruamel.yaml.comments import CommentedMap, CommentedSeq

ROOT = Path(__file__).resolve().parent.parent
ENTRY_DIR = "remotes"
PROTOCOL_VERSION = "2025-06-18"
USER_AGENT = "dcode-mcp-catalog-tool-preview/0.1 (https://github.com/dcode/mcp-catalog)"
TIMEOUT = 30.0
MAX_PAGES = 20
MAX_TEXT = 400  # max chars per preview description / param description

Poster = Callable[[str, dict, dict], tuple[dict, dict]]


class MCPError(RuntimeError):
    pass


def _parse_body(body: bytes, content_type: str, request_id: Any) -> dict:
    text = body.decode("utf-8")
    if "text/event-stream" in content_type:
        for block in re.split(r"\r?\n\r?\n", text):
            data = "\n".join(
                line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")
            )
            if not data:
                continue
            msg = json.loads(data)
            if isinstance(msg, dict) and msg.get("id") == request_id:
                return msg
        raise MCPError("no JSON-RPC response with matching id in event stream")
    return json.loads(text) if text.strip() else {}


def http_post(url: str, payload: dict, headers: dict) -> tuple[dict, dict]:
    """POST one JSON-RPC message. Returns (parsed response, response headers)."""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": USER_AGENT,
            **headers,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 - https URLs from catalog
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            body = resp.read()
    except urllib.error.HTTPError as exc:
        raise MCPError(f"{payload.get('method')}: HTTP {exc.code}") from exc
    return _parse_body(body, resp_headers.get("content-type", ""), payload.get("id")), resp_headers


def list_tools(url: str, post: Poster = http_post) -> list[dict]:
    """initialize + notifications/initialized + paginated tools/list."""
    headers: dict[str, str] = {}
    init, resp_headers = post(
        url,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mcp-catalog-tool-preview", "version": "0.1"},
            },
        },
        headers,
    )
    if "error" in init:
        raise MCPError(f"initialize failed: {init['error']}")
    if session := resp_headers.get("mcp-session-id"):
        headers["Mcp-Session-Id"] = session
    negotiated = (init.get("result") or {}).get("protocolVersion")
    if negotiated:
        headers["MCP-Protocol-Version"] = negotiated
    try:
        post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, headers)
    except (MCPError, json.JSONDecodeError):
        pass  # stateless servers may reject or ignore the notification

    tools: list[dict] = []
    cursor = None
    for page in range(MAX_PAGES):
        params = {"cursor": cursor} if cursor else {}
        resp, _ = post(url, {"jsonrpc": "2.0", "id": page + 2, "method": "tools/list", "params": params}, headers)
        if "error" in resp:
            raise MCPError(f"tools/list failed: {resp['error']}")
        result = resp.get("result") or {}
        tools.extend(result.get("tools") or [])
        cursor = result.get("nextCursor")
        if not cursor:
            return tools
    raise MCPError(f"tools/list did not finish after {MAX_PAGES} pages")


def first_paragraph(text: str | None) -> str:
    if not text:
        return ""
    para = re.split(r"\n\s*\n", text.strip(), maxsplit=1)[0]
    return re.sub(r"\s+", " ", para).strip()


SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z`\"(\[])")


def summarize(text: str | None, limit: int = MAX_TEXT) -> str:
    """First paragraph, whitespace collapsed, cut to whole sentences within `limit` chars.

    If even the first sentence is longer than `limit`, it is hard-cut with an ellipsis.
    """
    para = first_paragraph(text)
    if len(para) <= limit:
        return para
    out = ""
    for sentence in SENTENCE_END.split(para):
        candidate = f"{out} {sentence}".strip()
        if len(candidate) > limit:
            break
        out = candidate
    return out or para[: limit - 1].rstrip() + "\u2026"


def to_preview(tool: dict) -> dict:
    item: dict[str, Any] = {
        "name": tool["name"],
        "description": summarize(tool.get("description"))
        or summarize((tool.get("annotations") or {}).get("title")),
    }
    props = ((tool.get("inputSchema") or {}).get("properties")) or {}
    params = {
        str(name): summarize(spec.get("description") or spec.get("title")) if isinstance(spec, dict) else ""
        for name, spec in props.items()
    }
    if params:
        item["params"] = params
    return item


def generate_preview(url: str, post: Poster = http_post) -> list[dict]:
    return [to_preview(t) for t in list_tools(url, post)]


def _strings(value: Any, where: str):
    if isinstance(value, str):
        yield where, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(v, f"{where}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _strings(v, f"{where}[{i}]")


def lint_tools(tools: list[dict]) -> list[str]:
    """Lint the FULL live tool definitions (every description and inputSchema string),
    not just the shortened preview, for hidden characters and injection-like phrases."""
    findings: list[str] = []
    for tool in tools:
        name = str(tool.get("name", "?"))
        fields = {k: tool.get(k) for k in ("name", "title", "description", "inputSchema", "annotations")}
        for where, text in _strings(fields, name):
            findings += [f"{where}: {issue}" for issue in _text_issues(text)]
    return findings


def diff_summary(old: list | None, new: list[dict]) -> list[str]:
    old_by = {t.get("name"): t for t in (old or []) if isinstance(t, dict)}
    new_by = {t["name"]: t for t in new}
    lines = [f"+ {n}" for n in new_by if n not in old_by]
    lines += [f"- {n}" for n in old_by if n not in new_by]
    lines += [f"~ {n}" for n in new_by if n in old_by and old_by[n] != new_by[n]]
    if not lines and [t.get("name") for t in old or []] != list(new_by):
        lines.append("~ (order changed)")
    return lines


def _ruamel() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _to_commented(preview: list[dict]) -> CommentedSeq:
    seq = CommentedSeq()
    for tool in preview:
        item = CommentedMap()
        item["name"] = tool["name"]
        item["description"] = tool["description"]
        if "params" in tool:
            item["params"] = CommentedMap(tool["params"])
        seq.append(item)
    return seq


def write_preview(path: Path, preview: list[dict]) -> None:
    y = _ruamel()
    data = y.load(path.read_text(encoding="utf-8"))
    data["toolPreview"] = _to_commented(preview)
    with path.open("w", encoding="utf-8") as fh:
        y.dump(data, fh)


def select_files(root: Path, files: list[Path], entries: list[str]) -> list[Path]:
    if files:
        return [f.resolve() for f in files]
    candidates = sorted((root / ENTRY_DIR).glob("*.yaml"))
    if entries:
        wanted = set(entries)
        candidates = [c for c in candidates if c.stem in wanted]
        missing = wanted - {c.stem for c in candidates}
        if missing:
            raise SystemExit(f"unknown entry key(s): {', '.join(sorted(missing))}")
    return candidates


def run(paths: list[Path], write: bool, post: Poster = http_post, out=sys.stdout, err=sys.stderr) -> int:
    drift = failures = 0
    for path in paths:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        url = ((data or {}).get("remoteConfig") or {}).get("fixedURL")
        if not url:
            print(f"skip {path.name}: no remoteConfig.fixedURL", file=err)
            continue
        try:
            tools = list_tools(url, post)
        except (MCPError, OSError, ValueError) as exc:
            print(f"ERROR {path.name}: {url}: {exc}", file=err)
            failures += 1
            continue
        preview = [to_preview(t) for t in tools]
        for finding in lint_tools(tools):
            print(f"LINT {path.name}: {finding}", file=out)
        current = data.get("toolPreview")
        if current == preview:
            print(f"ok   {path.name} ({len(preview)} tools)", file=out)
            continue
        drift += 1
        changes = diff_summary(current, preview)
        if write:
            write_preview(path, preview)
            print(f"wrote {path.name} ({len(preview)} tools): {' '.join(changes)}", file=out)
        else:
            print(f"DRIFT {path.name}: {' '.join(changes)}", file=out)
    if not write and drift:
        print(
            f"{drift} entr{'y' if drift == 1 else 'ies'} out of date; run `uv run scripts/tool_preview.py --write`. "
            "Google's servers are in Developer Preview, so tool lists change upstream.",
            file=err,
        )
    return 1 if failures or (drift and not write) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", type=Path, help="entry files (default: all remotes/*.yaml)")
    parser.add_argument("--entry", action="append", default=[], help="entryKey to process (repeatable)")
    parser.add_argument("--root", type=Path, default=ROOT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="report drift and exit 1 (default)")
    mode.add_argument("--write", action="store_true", help="rewrite toolPreview in place")
    args = parser.parse_args(argv)
    return run(select_files(args.root.resolve(), args.files, args.entry), write=args.write)


if __name__ == "__main__":
    sys.exit(main())
