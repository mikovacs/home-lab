"""Validate local documentation link targets without making network requests."""
from pathlib import Path
import re
root=Path(__file__).resolve().parents[1]
errors=[]
for doc in root.rglob('*.md'):
    for target in re.findall(r'(?<!!)\[[^\]]*\]\(([^)]+)\)',doc.read_text()):
        if target.startswith(('https://','http://','#','mailto:')):continue
        p=(doc.parent/target.split('#')[0]).resolve()
        if not p.exists():errors.append(f'{doc.relative_to(root)}: {target}')
if errors:raise SystemExit('Broken documentation links:\n'+'\n'.join(errors))
print('Local documentation link checks passed')
