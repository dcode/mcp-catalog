# Agent rules for this repo

Read [docs/catalog-entry-spec.md](docs/catalog-entry-spec.md) before you
change anything in `remotes/`, `icons/` or `vmcps/`.

## Catalog entries

Catalog entries must:

- Live in `remotes/<entryKey>.yaml`, one entry per file.
- Use an `entryKey` with an allowed prefix:
  - `gws-` for Google Workspace's official servers.
  - `dcode-` for everything else.
  - Never `obot-`, which belongs to Obot's default catalog.
- Use a display `name` that is unique across all Obot catalog sources. Obot
  derives the object ID from the name alone. Names such as `Gmail` or
  `Google Drive` already exist in Obot's default catalog.
- Use `repoURL` to link to official documentation.
- Include a link to official documentation or setup instructions in the
  description before the first feature or setup section (`##` heading).
- Lead the description with concrete, product-specific functionality or
  outcomes. Do not open with protocol, hosting, or "official server"
  boilerplate.
- Use `MCP`, not the expanded term "Model Context Protocol".
- Mention hosting, authentication, or official status only when it adds
  useful context.
- Use `urlTemplate`, not `URLTemplate`, when configuring a URL template.
- Never put credentials, API keys, tokens, passwords, or other sensitive
  values in `fixedURL` or `urlTemplate`, including in query parameters. Use
  OAuth (`staticOAuthRequired`) or a sensitive `config` entry with
  `usage: header` instead. Do not add an entry whose provider requires
  credentials in the URL.
- Not use `serverUserType`, `env`, `multiUserConfig`, or
  `remoteConfig.headers`. Obot v0.28 removed or rejects these fields.
- Not edit `toolPreview` by hand. Regenerate it with
  `uv run scripts/tool_preview.py --write --entry <entryKey>`.

## Icons

- Set `icon` to a stable, public https URL. Never point it at this repo (for
  example `raw.githubusercontent.com`), because the repo may become private.
  `gws-` entries use the Wikimedia Commons (`upload.wikimedia.org`) file for
  the current Google brand icon.
- Commit a byte-identical copy as `icons/<entryKey>.svg`. Use `.png` only
  when no SVG exists upstream. Never edit or optimize the copy; it must stay
  byte-identical.
- SVGs must not contain scripts, event handlers, `foreignObject`, or external
  references. `validate.py` enforces this.

## Secrets

- Never print, log, or commit OAuth client IDs or secrets, API keys, or
  tokens. They live in Proton Pass and in SOPS-encrypted Secrets in the infra
  repo.

## Validation

After you change catalog YAML, icons, the schema, or scripts, run these
before finishing:

1. `uv run pytest`
2. `uv run scripts/validate.py --network`
3. `uv run scripts/tool_preview.py --check`
4. `yamllint --strict .`
5. If `obot mcp validate-catalog-yaml --help` succeeds, run
   `obot mcp validate-catalog-yaml --require-entry-key .`. Otherwise ask the
   user to install the Obot CLI, but do not require it.
6. `git diff --check`
