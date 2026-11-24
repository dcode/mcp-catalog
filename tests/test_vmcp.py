import json
import textwrap
from pathlib import Path

import pytest
import validate
from conftest import ENTRY, ICON, REPO

VMCP = textwrap.dedent(
    """\
    type: vmcp
    entryKey: dcode-workspace
    displayName: Workspace
    toolSearch: true
    components:
      - id: gmail
        name: Gmail
        mcpServerCatalogEntryKey: gws-gmail
    profiles:
      - name: Everything
        subjects:
          - type: selector
            id: "*"
        vmcpPermissions:
          allowAllComponents: true
    """
)


def write_vmcp(root: Path, text: str = VMCP, name: str = "dcode-workspace.yaml") -> Path:
    (root / "vmcps").mkdir(exist_ok=True)
    path = root / "vmcps" / name
    path.write_text(text)
    return path


def problems_for(root: Path, **kwargs) -> list[str]:
    _, problems = validate.validate_repo(root, **kwargs)
    return [str(p) for p in problems]


def errors_for(root: Path, **kwargs) -> list[str]:
    return [p for p in problems_for(root, **kwargs) if p.startswith("ERROR")]


def test_valid_vmcp_passes(repo):
    write_vmcp(repo)
    items, problems = validate.validate_repo(repo)
    assert [p for p in problems if p.level == "error"] == []
    assert [i["entryKey"] for i in items if i.get("type") == "vmcp"] == ["dcode-workspace"]


def test_real_vmcp_schema_is_valid_draft_2020_12():
    validate.Draft202012Validator.check_schema(json.loads((REPO / "schema" / "vmcp.schema.json").read_text()))


def test_unknown_local_reference(repo):
    write_vmcp(repo, VMCP.replace("gws-gmail", "gws-nope"))
    assert any("no entry with entryKey 'gws-nope'" in e for e in errors_for(repo))


def test_external_reference_needs_checkout(repo):
    write_vmcp(repo, VMCP.replace("gws-gmail", "github.com/dcode/mcp-catalog::gws-gmail"))
    probs = problems_for(repo)
    assert any(p.startswith("WARNING") and "--external-source github.com/dcode/mcp-catalog=" in p for p in probs)
    assert errors_for(repo) == []


def test_external_reference_checked_against_checkout(repo, tmp_path):
    other = tmp_path / "other"
    (other / "remotes").mkdir(parents=True)
    (other / "remotes" / "gws-gmail.yaml").write_text(ENTRY)
    write_vmcp(repo, VMCP.replace("gws-gmail", "github.com/dcode/mcp-catalog::gws-gmail"))
    external = {"github.com/dcode/mcp-catalog": validate.entry_keys(other)}
    assert errors_for(repo, external=external) == []
    write_vmcp(repo, VMCP.replace("gws-gmail", "github.com/dcode/mcp-catalog::gws-nope"))
    assert any("has no entry with entryKey 'gws-nope'" in e for e in errors_for(repo, external=external))


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("type: vmcp", "type: entry", "need `type: vmcp`"),
        ("  - id: gmail", '  - id: " "', "schema: components.0.id"),
        ("entryKey: dcode-workspace", "entryKey: obot-workspace", "must start with one of"),
        ("toolSearch: true", "toolSearch: true\nminObotVersion: v0.28.0", "schema: <root>"),
        ("displayName: Workspace", "displayName: Model Context Protocol tools", "not \"Model Context Protocol\""),
    ],
)
def test_vmcp_rules(repo, old, new, message):
    write_vmcp(repo, VMCP.replace(old, new))
    assert any(message in e for e in errors_for(repo)), errors_for(repo)


def test_filename_must_match(repo):
    write_vmcp(repo, name="other.yaml")
    assert any("filename must be dcode-workspace.yaml" in e for e in errors_for(repo))


def test_duplicate_component_id(repo):
    write_vmcp(repo, VMCP.replace("profiles:", "  - id: gmail\n    name: Again\n    mcpServerCatalogEntryKey: gws-gmail\nprofiles:"))
    assert any("duplicate component id 'gmail'" in e for e in errors_for(repo))


def test_clear_text_fixed_value_rejected(repo):
    write_vmcp(
        repo,
        VMCP.replace(
            "    mcpServerCatalogEntryKey: gws-gmail\n",
            "    mcpServerCatalogEntryKey: gws-gmail\n    configuration:\n      - key: TOKEN\n        policy: fixed\n        value: hunter2\n",
        ),
    )
    assert any("schema: components.0.configuration.0" in e for e in errors_for(repo))


def test_profile_unknown_component(repo):
    write_vmcp(
        repo,
        VMCP.replace(
            "      allowAllComponents: true",
            "      allowedComponents:\n        calendar:\n          allowedTools: null",
        ),
    )
    assert any("unknown component id 'calendar'" in e for e in errors_for(repo))


def test_warnings_for_no_profiles_and_force_single_user(repo):
    text = VMCP.split("profiles:")[0].replace(
        "mcpServerCatalogEntryKey: gws-gmail", "mcpServerCatalogEntryKey: gws-gmail\n    forceSingleUser: true"
    )
    write_vmcp(repo, text)
    probs = problems_for(repo)
    assert any("no profiles" in p for p in probs)
    assert any("forceSingleUser" in p for p in probs)
    assert errors_for(repo) == []


def test_main_external_source_flag(repo, tmp_path, capsys):
    other = tmp_path / "other"
    (other / "remotes").mkdir(parents=True)
    (other / "remotes" / "gws-gmail.yaml").write_text(ENTRY)
    (other / "icons").mkdir()
    (other / "icons" / "gws-gmail.svg").write_bytes(ICON)
    write_vmcp(repo, VMCP.replace("gws-gmail", "github.com/dcode/mcp-catalog::gws-gmail"))
    rc = validate.main(["--root", str(repo), "--external-source", f"github.com/dcode/mcp-catalog/={other}"])
    assert rc == 0
    assert "1 entries, 1 vMCPs checked: 0 error(s)" in capsys.readouterr().err


def test_parse_external_source_rejects_bad_value():
    with pytest.raises(validate.argparse.ArgumentTypeError):
        validate.parse_external_source("no-equals-sign")
