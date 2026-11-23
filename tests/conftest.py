import shutil
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

ICON = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M0 0h24v24H0z" fill="#4285F4"/></svg>\n'

ENTRY = textwrap.dedent(
    """\
    name: Gmail (Google Workspace)
    entryKey: gws-gmail
    shortDescription: Search, read, and draft Gmail messages
    description: |
      Search and read Gmail threads.

      See Google's [Gmail MCP server reference](https://developers.google.com/workspace/gmail/api/reference/mcp).

      ## What you'll need to connect

      OAuth.
    metadata:
      categories: Google Workspace,Productivity
    icon: https://upload.wikimedia.org/wikipedia/commons/x/xx/Gmail_icon.svg
    repoURL: https://developers.google.com/workspace/gmail/api/reference/mcp
    runtime: remote
    remoteConfig:
      fixedURL: https://gmailmcp.googleapis.com/mcp/v1
      staticOAuthRequired: true
    toolPreview:
      - name: list_labels
        description: Lists labels.
    """
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "schema").mkdir()
    shutil.copy(REPO / "schema" / "catalog-entry.schema.json", tmp_path / "schema")
    (tmp_path / "remotes").mkdir()
    (tmp_path / "icons").mkdir()
    (tmp_path / "remotes" / "gws-gmail.yaml").write_text(ENTRY)
    (tmp_path / "icons" / "gws-gmail.svg").write_bytes(ICON)
    return tmp_path
