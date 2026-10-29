"""Check shared plugin metadata and bundled resources without installing either host."""
import json
from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
def read(name):
    return json.loads((root / name).read_text(encoding='utf-8'))

portable = read('plugin.json')
version = re.search(r'^version = "([^"]+)"', (root / 'pyproject.toml').read_text(), re.M).group(1)
for manifest in [portable, read('.codex-plugin/plugin.json'), read('.claude-plugin/plugin.json')]:
    assert manifest['name'] == 'sixth-magnitude'
    assert manifest['version'] == version
    assert manifest['license'] == 'MIT'
    assert manifest['repository'] == 'https://github.com/chaosbeagle/sixth-magnitude'
    assert manifest['author']['name'] == 'Kuan Ting Wu'
    assert not any(key in manifest for key in ('hooks', 'mcpServers', 'apps', 'dependencies'))
for marketplace in ('.agents/plugins/marketplace.json', '.claude-plugin/marketplace.json'):
    catalog = read(marketplace)
    assert catalog['name'] == 'sixth-magnitude'
    assert len(catalog['plugins']) == 1
    plugin = catalog['plugins'][0]
    assert plugin['name'] == portable['name']
    source = plugin['source']
    path = source['path'] if isinstance(source, dict) else source
    assert path == './'
    assert (root / path / 'pyproject.toml').is_file()
assert read('.codex-plugin/plugin.json')['skills'] == './skills/'
skill = root / 'skills/sixth-magnitude/SKILL.md'
assert skill.read_bytes() == (root / 'sixth_magnitude/skill/SKILL.md').read_bytes()
assert skill.read_text().startswith('---\nname: sixth-magnitude\n')
assert 'Copyright (c) 2026 Kuan Ting Wu' in (root / 'LICENSE.md').read_text()
assert (root / 'docs/plugins.md').is_file()
print('Plugin metadata, marketplace roots, shared skill, workbench and MIT notice: PASS')
