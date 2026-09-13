"""The design export is served untouched: patches anchor exactly once, the bridge is injected
inside the logic script, and every binding the patches introduce has a default in bridge.js."""
import hashlib
import json
import re
from pathlib import Path

import pytest

from dashboard import ui
from dashboard.settings import UI_DIR

MANIFEST = Path(__file__).parent / "fixtures" / "ui_manifest.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_ui_files_are_the_recorded_export():
    """UI/ is never edited by the backend. If the design is re-exported on purpose, refresh the manifest:
    uv run python -c "from tests.test_dashboard_ui import write_manifest; write_manifest()" and re-run."""
    manifest = json.loads(MANIFEST.read_text())
    for name, digest in manifest.items():
        assert _sha(UI_DIR / name) == digest, f"{name} changed on disk — the frontend must stay byte-identical"


def write_manifest() -> None:
    files = ["Uncle Tony Overlord Dashboard.dc.html", "support.js"]
    MANIFEST.write_text(json.dumps({name: _sha(UI_DIR / name) for name in files}, indent=1))


def test_every_patch_anchor_occurs_exactly_once_and_page_renders():
    html = ui.PAGE.read_text(encoding="utf-8")
    newline = "\r\n" if "\r\n" in html else "\n"
    for anchor, _ in ui.PATCHES:
        assert html.count(anchor.replace("\n", newline)) == 1, anchor[:60]
    page = ui.render_page()
    for _, replacement in ui.PATCHES:
        assert replacement.replace("\n", newline) in page
    for anchor, replacement in ui.PATCHES:
        if "{{" not in anchor:
            assert anchor.replace("\n", newline) not in page
    # the bridge sits inside the data-dc-script block, after the class, before its closing tag
    start = page.index(ui.SCRIPT_OPEN)
    end = page.index("</script>", start)
    block = page[start:end]
    assert "class Component extends DCLogic" in block and "dashboard bridge" in block
    assert block.index("class Component") < block.index("dashboard bridge")
    assert page.count("dashboard bridge") == 1


def test_bridge_defaults_cover_every_binding_the_patches_add():
    bridge = ui.BRIDGE.read_text(encoding="utf-8")
    defaults_src = bridge.split("var DEFAULTS = {", 1)[1].split("};", 1)[0]
    defaults = set(re.findall(r"(\w+):", defaults_src))
    added = set()
    for _, replacement in ui.PATCHES:
        added |= {b.strip() for b in re.findall(r"{{\s*([\w.]+)\s*}}", replacement)}
    template = ui.PAGE.read_text(encoding="utf-8")
    # bindings the export already carries (a patch may re-emit one, e.g. next to a new button)
    added -= {b.strip() for b in re.findall(r"{{\s*([\w.]+)\s*}}", template)}
    added = {b for b in added if "." not in b}  # per-row fields of an sc-for alias, not page vals
    assert added <= defaults, added - defaults


def test_template_drift_is_loud():
    with pytest.raises(ui.TemplateDrift):
        ui.render_page(template="<html><x-dc></x-dc></html>", bridge="")
