#!/usr/bin/env python
"""Verify all upgrade feature components."""

from pathlib import Path

# Check HTML
html_path = Path('ui/templates/index.html')
html_content = html_path.read_text(encoding='utf-8')

# Check JS
js_path = Path('ui/static/js/index.js')
js_content = js_path.read_text(encoding='utf-8')

print('=' * 60)
print('UPGRADE FEATURE COMPONENT CHECK')
print('=' * 60)

checks = [
    ('HTML: Upgrade K8s button', 'Upgrade K8s' in html_content),
    ('HTML: Upgrade modal div', 'id="upgrade-modal' in html_content),
    ('HTML: Script import', '/static/js/index.js' in html_content),
    ('JS: openUpgradeModal function', 'function openUpgradeModal' in js_content),
    ('JS: loadUpgradeVersions function', 'loadUpgradeVersions' in js_content),
    ('JS: selectUpgradeVersion function', 'selectUpgradeVersion' in js_content),
    ('JS: confirmUpgrade function', 'confirmUpgrade' in js_content),
    ('JS: closeUpgradeModal function', 'closeUpgradeModal' in js_content),
]

passed = 0
failed = 0

for check, result in checks:
    status = '✅' if result else '❌'
    print(f'{status} {check}')
    if result:
        passed += 1
    else:
        failed += 1

print('=' * 60)
print(f'Results: {passed} passed, {failed} failed')
print('=' * 60)

if failed == 0:
    print('✅ SUCCESS: All upgrade feature components are present!')
else:
    print(f'❌ FAILURE: {failed} component(s) missing')

exit(0 if failed == 0 else 1)
