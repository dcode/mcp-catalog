# mcp-catalog

A git-synced [Obot](https://github.com/obot-platform/obot) MCP catalog source
for <https://obot.ditch.family>. It is modeled on
[obot-platform/mcp-catalog](https://github.com/obot-platform/mcp-catalog).

Today it publishes Google's official Google Workspace MCP servers (Gmail, Drive,
Docs, Sheets, Slides, Calendar, Chat, People, and Universal Search) as remote
entries with static OAuth. See [docs/google-workspace.md](docs/google-workspace.md).

## Layout

| Path | Contents |
| --- | --- |
| `remotes/<entryKey>.yaml` | Catalog entries, one per file. This is what Obot syncs. |
| `icons/<entryKey>.svg` | Byte-identical local copy of each entry's `icon` URL. |
| `vmcps/` | Optional `type: vmcp` items. None yet. |
| `schema/catalog-entry.schema.json` | Strict JSON Schema for entries. |
| `scripts/validate.py` | Schema and lint checks for entries and icons. |
| `scripts/tool_preview.py` | Regenerates `toolPreview` from each server's live `tools/list`. |
| `docs/` | [Entry spec](docs/catalog-entry-spec.md) and [Google Workspace guide](docs/google-workspace.md). |
| `.obotcatalogs`, `.ignoreobotcatalogs` | Tell Obot's sync which files are catalog items. |

## Using it in Obot

As an admin, go to **Admin → MCP Servers → Git Source URLs → Add server(s)
from Git**. Enter `https://github.com/dcode/mcp-catalog`, use a GitHub token
with read access to contents, and click **Sync**. Then set the OAuth client
on each `gws-*` entry. See
[Getting the client into Obot](docs/google-workspace.md#getting-the-client-into-obot).

## Adding or changing an entry

1. Pick an `entryKey` with an allowed prefix: `gws-` for Google Workspace, or
   `dcode-` for anything else. See [the spec](docs/catalog-entry-spec.md#entrykey-prefixes).
2. Create `remotes/<entryKey>.yaml`. Use an existing entry as the template and
   read [docs/catalog-entry-spec.md](docs/catalog-entry-spec.md). The display
   `name` must be unique across every Obot catalog source.
3. Set `icon` to a stable public https URL, then save a byte-identical copy
   of it:

   ```sh
   curl -fsSL -A 'dcode-mcp-catalog' "$ICON_URL" -o icons/<entryKey>.svg
   ```

4. Generate the tool preview with
   `uv run scripts/tool_preview.py --write --entry <entryKey>`.
5. Run the checks below and open a PR.

## Checks

You need [uv](https://docs.astral.sh/uv/).

```sh
uv sync                                    # once
uv run pytest                              # tooling tests
uv run scripts/validate.py                 # schema + lint (offline)
uv run scripts/validate.py --network       # also compare icon URLs with icons/
uv run scripts/validate.py --print-ids     # entryKey -> Obot entry ID
uv run scripts/tool_preview.py --check     # toolPreview vs live tools/list
yamllint --strict .
git diff --check
```

If the Obot CLI is installed, also run Obot's own validator:

```sh
obot mcp validate-catalog-yaml --require-entry-key .
```

To install the CLI, download `obot_v0.28.2_<os>_<arch>.tar.gz` and
`obot_0.28.2_checksums.txt` from the
[v0.28.2 release](https://github.com/obot-platform/obot/releases/tag/v0.28.2),
then verify the download with `shasum -a 256 -c --ignore-missing`.

`pre-commit install` runs yamllint, the basic hygiene hooks, and
`validate.py` on every commit. CI ([`.github/workflows/validate.yml`](.github/workflows/validate.yml))
runs on every PR and every push to `main`. Its `validate` job (the required
status check) runs the deterministic checks plus the Obot CLI validator. Its
`tool-preview` job compares previews with Google's live servers; it is
informational and not required, so upstream drift never blocks a PR. [`tool-preview-refresh.yml`](.github/workflows/tool-preview-refresh.yml)
runs weekly and opens a PR when Google's tool lists change.
