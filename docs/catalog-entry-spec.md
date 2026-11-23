# Catalog entry spec

This is the contract for files in `remotes/`. It follows Obot v0.28.2's catalog
code (`apiclient/types/mcpserver.go`, `pkg/mcpcatalog/`, `pkg/mcp/mcpvalidators.go`).
Where Obot's own GitOps docs disagree with that code, this spec follows the code.

The machine-readable version is [`schema/catalog-entry.schema.json`](../schema/catalog-entry.schema.json).
[`scripts/validate.py`](../scripts/validate.py) enforces the schema and the lint
rules below.

## How Obot reads this repo

- Obot syncs the repo as a git catalog source. [`.obotcatalogs`](../.obotcatalogs)
  limits the sync to `remotes/*.yaml` and `vmcps/*.yaml`. Everything else
  (docs, scripts, icons, CI config) is never parsed.
- Obot ignores unknown fields. A typo therefore fails silently in Obot, which
  is why our schema rejects unknown fields.
- A few legacy fields make the sync fail outright: top-level `env`,
  `remoteConfig.headers` and `multiUserConfig.userDefinedHeaders`.
- If one item is invalid, the sync reports an error for this source and still
  applies the other items.
- **The object ID comes from the display name only.** The entry's Kubernetes
  name is `default-<sanitized name>-<8 hex of sha256("default\0<sanitized>\0")>`.
  It does not include `entryKey` or the source URL. Two sources with the same
  display name overwrite one object. Obot's default catalog already uses about
  200 names, for example `Gmail`, `Google Calendar`, `Google Drive`,
  `Google Docs`, `Google Sheets` and `Calendar`, so our names add a suffix,
  for example
  `Gmail (Google Workspace)`. Run `uv run scripts/validate.py --print-ids` to
  list the IDs that the OAuth-credential API calls need.

## File layout

| Path | Contents |
| --- | --- |
| `remotes/<entryKey>.yaml` | One entry per file. A single mapping, not a list. Extension `.yaml`. |
| `icons/<entryKey>.svg` | Byte-identical copy of the entry's `icon` URL. Use `.png` only when no SVG exists upstream. |
| `vmcps/<entryKey>.yaml` | Optional `type: vmcp` items. Not covered by the schema yet. |

## entryKey prefixes

`entryKey` must be a DNS-1123 subdomain (lowercase letters, digits, `-` and `.`)
and must be unique in this repo. The file name must equal it. Use one of these
prefixes:

| Prefix | Use for |
| --- | --- |
| `gws-` | Google Workspace's official remote MCP servers (`gws-gmail`, `gws-drive`, ...). |
| `dcode-` | Anything else added to this catalog, such as self-hosted or third-party servers (`dcode-<name>`). |

Do not use `obot-`. That prefix belongs to Obot's default catalog. To add a
prefix, update `ALLOWED_PREFIXES` in `scripts/validate.py` and this table in
the same change.

## Fields

"Obot" means Obot rejects the entry when the field is missing. "Repo" means
only this repo's schema requires it.

| Field | Required | Notes |
| --- | --- | --- |
| `name` | Obot | Display name. It sets the object ID, so it must be unique across all catalog sources (see above). |
| `entryKey` | Repo | Stable key, prefixed as above. `obot mcp validate-catalog --require-entry-key` also requires it. |
| `shortDescription` | Repo | At most 160 characters. |
| `description` | Repo | Markdown. Lead with what the server does. Link to official docs before the first `##` heading. |
| `icon` | Repo | Public `https` URL. See [Icons](#icons). |
| `repoURL` | Repo | The official documentation page. |
| `runtime` | Obot | Must be `remote`. The schema only models remote servers. Extend it before adding `containerized`, `uvx`, `npx` or `openapi` entries. |
| `remoteConfig` | Obot | Exactly one of `fixedURL`, `hostname` or `urlTemplate`. Also `staticOAuthRequired`, `localhostCallbackEnabled` and `localhostCallbackPath` (which starts with `/`). `headers` and `tunnelName` are rejected. |
| `metadata` | no | String values only. `categories` is a comma-separated list (Obot drops `Official`). `unsupportedTools` is a comma-separated list of tools that start unselected. `allow-multiple` is `"true"` or `"false"`. |
| `toolPreview` | no | Generated; do not edit by hand. A list of `{name, description, params: {param: description}}`. |
| `upgradeNote` | no | Markdown shown before an admin applies a catalog update. Add one whenever a change needs users to reconnect or loses settings. |
| `minObotVersion` / `maxObotVersion` | no | `vX.Y.Z`, inclusive. Obot skips entries whose range excludes the running version. |
| `config` | no | User or admin settings (`key`, `usage` of `env`, `header`, `file`, `dynamicFile` or `interpolated`, `sensitive`, ...). `userAllowed` is rejected. Not used by OAuth-only entries. |
| `injectObotHeaders` | no | `X-Obot-*` headers to forward. |

### Fields that must not appear

- `serverUserType`. Obot v0.28 removed it and ignores it, and
  `obot mcp convert-catalog` deletes it. Sharing is now decided per vMCP
  component; see [Google Workspace](google-workspace.md#shared-server-per-user-sign-in).
- `env`, `multiUserConfig` and `remoteConfig.headers`. These are legacy fields
  and break the sync. Use `config` instead.
- `remoteConfig.tunnelName`, which Obot rejects for git-synced entries.

## Lint rules

`scripts/validate.py` checks these on top of the schema:

1. The file name equals `entryKey`, the prefix is allowed, and every
   `entryKey` and sanitized `name` is unique.
2. `name` does not collide with an entry in Obot's default catalog. The names
   are vendored in `schema/upstream-names.txt` (refresh with
   `uv run scripts/validate.py --refresh-upstream-names <checkout>`); CI also
   passes `--upstream` with a fresh shallow clone of
   `obot-platform/mcp-catalog`.
3. No file contains the phrase "Model Context Protocol"; write "MCP".
4. `description` contains a Markdown `https` link before the first `## `
   heading.
5. `fixedURL` and `urlTemplate` use `https`, contain no `user:pass@`, and have
   no query parameter that looks like a credential (`key`, `token`, `secret`,
   `auth`, `sig`, ...). Use OAuth or a sensitive header `config` instead. Do
   not add a provider that requires credentials in the URL.
6. `icon` uses `https` and is not hosted on GitHub.
7. `gws-` entries additionally:
   - `fixedURL` matches `^https://[a-z]+\.googleapis\.com/mcp/v1$`.
   - `staticOAuthRequired: true`.
   - No `config`.
   - The icon is served from `upload.wikimedia.org`.
   - `metadata.categories` includes `Google Workspace`.
8. The local icon exists and is safe:
   - An SVG is well-formed XML, has no DOCTYPE or ENTITY declarations, and
     has an `<svg>` root with a `viewBox`. An SVG with only width and height
     gets a warning instead.
   - An SVG has no `<script>`, no `<foreignObject>`, no `on*` attributes, no
     `href` other than `#fragment`, and no `url(...)` or `@import` that is not
     a local reference.
   - A PNG has PNG magic bytes.
   - The icon URL's file extension matches the local copy's.
9. `toolPreview` text is clean:
   - No hidden or format Unicode characters (zero-width, bidi, tag characters).
   - No prompt-injection phrases, such as "ignore previous instructions",
     `<system>` tags, or "don't tell the user".
   - URLs and descriptions over 1024 characters produce warnings.
10. `name`, `shortDescription` and `description` pass the same hidden-character
    and injection-phrase checks.
11. With `--network`, the icon URL's bytes equal the local copy's.

## Icons

Obot renders `icon` as a plain `<img src>` and never validates it. This repo
may become private, so icons must not point at `raw.githubusercontent.com` or
anything else served from this repo. Use a stable public URL (for Google
Workspace entries, the Wikimedia Commons upload URL of the current brand icon).
Keep a byte-identical copy in `icons/` so the icon can be re-hosted, or inlined
as a `data:` URI, if the URL stops working. Obot does not use the local copy.

## OAuth (`staticOAuthRequired`)

Set `staticOAuthRequired: true` for providers that do not support dynamic
client registration, such as Google. An admin then stores one OAuth client
(ID and secret) per entry in Obot. Until that is done the entry is hidden from
non-admins. The entry has no field for scopes. Obot discovers them from the
server's protected-resource metadata. See
[Google Workspace](google-workspace.md#getting-the-client-into-obot) for the
procedure.

## Tool previews

`toolPreview` only populates the catalog UI before a user connects. Generate it
from the live server:

```sh
uv run scripts/tool_preview.py --check          # exit 1 on drift
uv run scripts/tool_preview.py --write          # rewrite every entry
uv run scripts/tool_preview.py --write --entry gws-gmail
```

The script sends `initialize` and `tools/list` without credentials (Google's
servers allow this). For each tool it keeps the name, a description and the
parameter names with their descriptions. Each description is the first
paragraph, cut to whole sentences within 400 characters. The rest of the YAML
file is left as it was.

Both modes also lint the full live tool definitions (every description and
`inputSchema` string, including the parts the preview drops) with the same
hidden-character and injection-phrase rules, and print `LINT` lines. Lint
findings are reported but do not change the exit code.
