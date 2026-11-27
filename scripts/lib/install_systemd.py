import argparse
from pathlib import Path
from homelab import ROOT, atomic, config, require, root_only, run, env_file, start_timeout

p=argparse.ArgumentParser()
p.add_argument('--dry-run',action='store_true')
a=p.parse_args()
c=config()
require(c['REPO_ROOT']==str(ROOT),'REPO_ROOT must match checkout')
# systemd-escape handles hyphens and custom mount paths correctly.
mount=run(['systemd-escape','--path','--suffix=mount',c['MEDIA_ROOT']],capture=True).strip() if not a.dry_run else '<escaped-media-mount>'
data_mount=run(['systemd-escape','--path','--suffix=mount',c['DATA_ROOT']],capture=True).strip() if not a.dry_run else '<escaped-data-mount>'
values={**c,'MEDIA_MOUNT':mount,'DATA_MOUNT':data_mount,
        'STACK_UNIT_TIMEOUT':str(2 * int(start_timeout(c)) + 60)}
for src in sorted((ROOT/'config/systemd').glob('homelab-*')):
    content=src.read_text()
    for k,v in values.items():content=content.replace('@'+k+'@',v)
    if '[Service]' in content:
        content=content.replace('[Service]', '[Service]\nEnvironment=PYTHONDONTWRITEBYTECODE=1\nEnvironment=HOMELAB_ENV_FILE='+str(env_file()))
    if a.dry_run:
        print('DRY RUN: install',src.name)
    else:
        root_only()
        atomic(Path('/etc/systemd/system')/src.name,content,0o644)
if not a.dry_run:run(['systemctl','daemon-reload'])
print('Units installed only. Enable selected stack services, timers and firewall after acceptance checks.')
