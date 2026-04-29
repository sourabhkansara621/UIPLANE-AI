#!/usr/bin/env python
"""Verify upgrade features are in the template."""

from pathlib import Path

template_path = Path('ui/templates/index.html')
content = template_path.read_text(encoding='utf-8')

checks = {
    'Upgrade K8s button': 'Upgrade K8s' in content,
    'Upgrade modal': 'id="upgrade-modal"' in content,
    'openUpgradeModal function': 'openUpgradeModal' in content,
    'confirmUpgrade function': 'confirmUpgrade' in content,
    'loadUpgradeVersions function': 'loadUpgradeVersions' in content,
}

print("=" * 50)
print("Upgrade Feature Component Check")
print("=" * 50)

for check, result in checks.items():
    status = '✅' if result else '❌'
    print(f'{status} {check}')

print("=" * 50)
if all(checks.values()):
    print("✅ All upgrade components present!")
else:
    print("❌ Some components are missing")
