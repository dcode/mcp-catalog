# Google Workspace MCP servers

This catalog publishes Google's official Google Workspace MCP servers to Obot
at <https://obot.ditch.family>. Google offers them as a Developer Preview: they
use streamable HTTP and OAuth, and they do not support dynamic client
registration. Classroom is left out on purpose.

Google's reference: [Configure the Google Workspace MCP servers](https://developers.google.com/workspace/guides/configure-mcp-servers).
Each product also has a reference page, such as
`https://developers.google.com/workspace/gmail/api/reference/mcp`.

## Entries

| entryKey | Display name | Endpoint | Obot entry ID |
| --- | --- | --- | --- |
| `gws-gmail` | Gmail (Google Workspace) | `https://gmailmcp.googleapis.com/mcp/v1` | `default-gmail-google-workspace-22cefcca` |
| `gws-drive` | Google Drive (Google Workspace) | `https://drivemcp.googleapis.com/mcp/v1` | `default-google-drive-google-workspace-4c1bec1d` |
| `gws-docs` | Google Docs (Google Workspace) | `https://docsmcp.googleapis.com/mcp/v1` | `default-google-docs-google-workspace-ed6ff5ff` |
| `gws-sheets` | Google Sheets (Google Workspace) | `https://sheetsmcp.googleapis.com/mcp/v1` | `default-google-sheets-google-workspace-d55e1123` |
| `gws-slides` | Google Slides (Google Workspace) | `https://slidesmcp.googleapis.com/mcp/v1` | `default-google-slides-google-workspace-7a759aba` |
| `gws-calendar` | Google Calendar (Google Workspace) | `https://calendarmcp.googleapis.com/mcp/v1` | `default-google-calendar-google-workspace-ecc37b83` |
| `gws-chat` | Google Chat (Google Workspace) | `https://chatmcp.googleapis.com/mcp/v1` | `default-google-chat-google-workspace-c05cee78` |
| `gws-people` | Google People (Google Workspace) | `https://people.googleapis.com/mcp/v1` | `default-google-people-google-workspace-ee9bb1b2` |
| `gws-search` | Google Workspace Search | `https://workspacemcp.googleapis.com/mcp/v1` | `default-google-workspace-search-ff12dc69` |

The display names carry a "(Google Workspace)" suffix because Obot's default
catalog already has entries named `Gmail`, `Google Drive`, and so on. Obot
derives each entry's object ID from its display name alone, so reusing a name
would overwrite that entry. The IDs above come from
`uv run scripts/validate.py --print-ids`. Confirm them after the first sync
with `GET /api/mcp-catalogs/default/entries`.

## Google Cloud setup (once)

1. **Project.** Use a Google Cloud project owned by the Workspace org. Join
   the [Google Workspace Developer Preview Program](https://developers.google.com/workspace/preview)
   if you have not already.
2. **Enable the product APIs and the MCP services.**

   ```sh
   gcloud services enable \
     gmail.googleapis.com drive.googleapis.com docs.googleapis.com \
     sheets.googleapis.com slides.googleapis.com calendar-json.googleapis.com \
     chat.googleapis.com people.googleapis.com \
     gmailmcp.googleapis.com drivemcp.googleapis.com docsmcp.googleapis.com \
     sheetsmcp.googleapis.com slidesmcp.googleapis.com calendarmcp.googleapis.com \
     chatmcp.googleapis.com workspacemcp.googleapis.com \
     --project=PROJECT_ID
   ```

   People serves MCP from `people.googleapis.com` itself. Universal Search
   (`workspacemcp`) needs no separate product API.
3. **Consent screen (Google Auth Platform → Branding / Audience).** Set the
   audience to **Internal**. Gmail's `https://mail.google.com/` and Drive's
   `drive` scopes are restricted, and an External app would need Google's
   verification before it can request them.
4. **Data Access.** Add the scopes from the table below that you want users to
   be able to grant.
5. **OAuth client.** Create one **Web application** client and check **Use
   this client for an AI-powered agent**.
   - Authorized redirect URI: `https://obot.ditch.family/oauth/mcp/callback`
   - Authorized JavaScript origins: none needed.

   All nine entries share this one client.
6. **Chat app (write access only).** To send Chat messages, configure a Chat
   app in the project (Google Chat API → Configuration). Read-only Chat tools
   work without it.
7. Store the client ID and secret in Proton Pass as item
   `workspace-mcp-oauth2`. Never commit them in plaintext or paste them into
   tickets or logs.

## Scopes per server

No catalog field sets scopes. When a user connects, Obot reads the server's
OAuth protected-resource metadata
(`https://<host>/.well-known/oauth-protected-resource/mcp/v1`) and requests
every scope in `scopes_supported`. Later per-tool challenges can add more. Obot
always sends `access_type=offline` and `prompt=consent`, so Google issues a
refresh token. The lists below are what the metadata advertised on 2026-11-22.
Re-check them with
`curl -s https://gmailmcp.googleapis.com/.well-known/oauth-protected-resource/mcp/v1`.

| Server | Scopes (`https://www.googleapis.com/auth/` prefix omitted) |
| --- | --- |
| Gmail | `https://mail.google.com/`, `gmail.compose`, `gmail.drafts`, `gmail.drafts.create`, `gmail.drafts.readonly`, `gmail.labels`, `gmail.labels.create`, `gmail.labels.readonly`, `gmail.messages`, `gmail.messages.delete`, `gmail.messages.modify`, `gmail.messages.readonly`, `gmail.metadata`, `gmail.modify`, `gmail.readonly`, `gmail.send`, `gmail.send.self`, `gmail.settings.basic`, `gmail.settings.filters`, `gmail.settings.filters.readonly` |
| Drive | `drive`, `drive.readonly`, `drive.file` |
| Docs | `drive.readonly`, `documents.readonly`, `drive`, `documents` |
| Sheets | `drive.readonly`, `spreadsheets.readonly`, `drive`, `spreadsheets` |
| Slides | `drive.readonly`, `presentations.readonly`, `drive`, `drive.file`, `presentations` |
| Calendar | `calendar`, `calendar.acls`, `calendar.calendarlist`, `calendar.calendarlist.readonly`, `calendar.calendars`, `calendar.calendars.readonly`, `calendar.events`, `calendar.events.freebusy`, `calendar.events.readonly`, `calendar.freebusy`, `calendar.readonly`, `calendar.settings.readonly` |
| Chat | `chat.spaces`, `chat.spaces.readonly`, `chat.memberships`, `chat.memberships.readonly`, `chat.messages`, `chat.messages.readonly`, `chat.messages.create`, `chat.users.readstate`, `chat.users.readstate.readonly`, `chat.users.spacesettings` |
| People | `directory.readonly`, `userinfo.profile`, `contacts.readonly` |
| Universal Search | `drive`, `drive.readonly`, `https://mail.google.com/`, `gmail.modify`, `gmail.readonly`, `calendar`, `calendar.readonly`, `chat.messages`, `chat.messages.readonly`, `chat.spaces.readonly`, `chat.memberships.readonly` |

Users consent to the broad scopes (`https://mail.google.com/`, `drive`). Limit
what the tools can actually do with Obot tool selection (or
`metadata.unsupportedTools`), not with scopes.

## Getting the client into Obot

Every entry sets `staticOAuthRequired: true`. Obot keeps the client in its own
credential store (context `mcp-oauth-<entry ID>`). It cannot read the client
from a Kubernetes Secret, so the client has to be set through the API or the
UI once for each entry, nine times in total. Until it is set, non-admins
cannot see the entry. The credential survives re-syncs because the entry ID
does not change.

### Automated (infra repo)

Pending: this lives on the infra repo branch `feat/obot-gws-mcp-catalog`
(`kubernetes/apps/obot-mcp-catalog`, Flux) and is not active until that PR
merges. The Job reads the client ID and secret from a SOPS-encrypted Secret
(`obot-mcp-catalog`). For each `gws-*` entry it looks up the entry ID, checks
`GET .../oauth-credentials`, and POSTs only when the credential is not yet
configured, so it can safely run again.

Token order (`configure.py`): `OBOT_API_KEY` from the `obot-mcp-catalog`
Secret (optional key) is preferred; otherwise it falls back to
`OBOT_BOOTSTRAP_TOKEN` from `obot-env`. The bootstrap token only works while
bootstrap is enabled (no auth provider or no owner yet, or
`OBOT_SERVER_FORCE_ENABLE_BOOTSTRAP=true`). This deployment uses Authentik and
has an owner, so in practice add an admin API key (`ok1-...`, with API
access) as `OBOT_API_KEY`.

### By hand

UI: **Admin → MCP Servers →** open the entry **→ Configure OAuth Credentials**,
enter the client ID and secret, then **Save**.

API (the secret never appears on the command line or in shell history):

```sh
export OBOT=https://obot.ditch.family
# OBOT_TOKEN: an admin API key, read into the environment without echoing it
for id in $(uv run scripts/validate.py --print-ids 2>/dev/null | cut -f2); do
  pass-cli ... | jq -c '{clientID: .client_id, clientSecret: .client_secret}' \
    | curl -sf -X POST "$OBOT/api/mcp-catalogs/default/entries/$id/oauth-credentials" \
        -H "Authorization: Bearer $OBOT_TOKEN" -H 'Content-Type: application/json' \
        --data @- >/dev/null && echo "set $id"
done
```

Replace `pass-cli ...` with the command that prints the
`workspace-mcp-oauth2` item as JSON. Useful API calls:

- `GET .../oauth-credentials` returns `{configured, clientID}`. The secret is
  never returned.
- `POST` returns 400 if a credential already exists. There is no update.
- `DELETE` removes the credential **and every user's Google token for that
  entry**.

To rotate the secret, run DELETE and then POST for each entry. Every user must
reconnect afterwards.

## Shared server, per-user sign-in

**Decision:** we omit `serverUserType`. In effect the entries are multi-user:
users share one server, and each user signs in to Google with their own
account.

**Why:**

- Obot v0.28 no longer has the field. The constants remain only for legacy
  servers, the field is ignored when synced, and `obot mcp convert-catalog`
  deletes it. Upstream catalog files still include
  `serverUserType: singleUser` only so that older Obot versions can read them.
- Users reach these servers through a vMCP. For each vMCP component, Obot
  decides whether the server is shared (`pkg/vmcp/sharing.go`). A `fixedURL`
  remote with no per-user configuration is shared unless the component sets
  **Force single-user**. Leave that option off.
- A shared server still keeps tokens separate: Obot stores upstream tokens
  per user and per connection, keyed by (user ID, connection ID, URL). Every
  user goes through Google's consent screen once and calls Google as
  themselves. Only the OAuth *client* is shared.
- Remote servers never get containers or pods. Even a forced single-user
  component only adds an extra MCPServer object, so there is no container to
  save.

So we get the multi-user behavior without any setting, and the single-user
fallback is not needed.

## vMCP

Users connect through a vMCP, which can bundle all nine servers, either built
in the UI or as a `type: vmcp` item in `vmcps/`. Reference components by
`entryKey`. From another source, use the source ID: for example,
`github.com/dcode/mcp-catalog::gws-gmail`. Do not set `forceSingleUser`.

## Keeping tool previews current

Google changes these servers often during the preview. The `tool-preview` job
in the `validate` workflow fails when an entry's `toolPreview` no longer
matches the live `tools/list`. That job is informational and is not a required
status check, so upstream drift never blocks unrelated PRs. The weekly
`tool-preview-refresh` workflow, which can also be started by hand,
regenerates the previews and opens a PR. Give it a `CATALOG_AUTOMATION_TOKEN`
secret (fine-grained PAT or GitHub App token) so CI runs on that PR; PRs opened
with the default `GITHUB_TOKEN` do not trigger workflows. The PR body also
lists `LINT` findings from the full live tool definitions (every description
and `inputSchema` string, not just the shortened preview). Review added or changed tools in that
PR before merging, and consider adding destructive ones to
`metadata.unsupportedTools`.

## Icon deviations

Each entry's `icon` is the Wikimedia Commons upload of the 2026 Google brand
icon, with a byte-identical copy in `icons/`. Two entries deviate because
Commons has no suitable 2026 file:

- `gws-search` uses `Google Favicon 2025.svg`, the generic Google "G". Commons
  has no standalone 2026 Google Workspace mark (the 2026 files are wordmarks
  and a product-icon strip).
- `gws-people` uses `Google Contacts logo (2026).png`. Contacts is the product
  that fronts the People API, and no 2026 SVG exists, so it is a PNG.

Re-check Commons occasionally and swap the URL and the local copy together
(`validate.py --network` confirms they match).
