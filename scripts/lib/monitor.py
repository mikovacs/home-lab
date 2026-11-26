"""Read-only host monitor; optional webhook URL is read from a protected file."""
import datetime
import json
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
from homelab import config, mounted, require
from installer_api import open_api

c=config()
report={'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'mounts':{},'alerts':[]}
for prefix in ('MEDIA','DATA'):
    path=c[prefix+'_ROOT']
    try:
        mounted(path,c[prefix+'_UUID'],media=prefix=='MEDIA')
        usage=shutil.disk_usage(path);percent=round(100*usage.used/usage.total,1)
        report['mounts'][prefix]={'used_percent':percent,'free_bytes':usage.free}
        if percent>=int(c['DISK_WARN']):report['alerts'].append(prefix+(': critical disk usage' if percent>=int(c['DISK_CRITICAL']) else ': disk usage warning'))
    except (OSError,RuntimeError,subprocess.CalledProcessError) as exc:
        report['mounts'][prefix]={'valid':False};report['alerts'].append(prefix+': mount verification failed')
status=Path(c['RUNTIME_ROOT'])/'backup-status.json'
if not status.exists():report['alerts'].append('No successful backup recorded')
else:
    try:
        last=json.loads(status.read_text())
        age=(datetime.datetime.now(datetime.timezone.utc)-datetime.datetime.fromisoformat(last['time'])).total_seconds()
        if age>36*3600:report['alerts'].append('Backup older than 36 hours')
    except (ValueError,KeyError):report['alerts'].append('Invalid backup status')
# SMART device paths are explicitly supplied, never inferred as /dev/sda.
smart=Path(c['SECRETS_ROOT'])/'smart-devices.json'
if smart.exists():
    for item in json.loads(smart.read_text()):
        require(item['device'].startswith('/dev/'),'Invalid SMART device')
        cmd=['smartctl','-H','-j']
        if item.get('type'):cmd+=['-d',item['type']]
        result=subprocess.run(cmd+[item['device']],capture_output=True,text=True,check=False)
        try:
            info=json.loads(result.stdout)
            if info.get('smart_status',{}).get('passed') is not True:report['alerts'].append('SMART unavailable or failing: '+item['device'])
        except ValueError:report['alerts'].append('SMART unreadable: '+item['device'])
print(json.dumps(report,indent=2))
webhook=Path(c['SECRETS_ROOT'])/'monitor-webhook.json'
if report['alerts'] and webhook.exists():
    w=json.loads(webhook.read_text())
    require(w['url'].startswith('https://'),'Notification endpoint must use HTTPS')
    req=urllib.request.Request(w['url'],data=json.dumps(report).encode(),headers={'Content-Type':'application/json',**w.get('headers',{})},method='POST')
    try:
        with open_api(req,timeout=15) as response:require(response.status<300,'Notification failed')
    except Exception:
        sys.exit('Notification delivery failed; inspect protected endpoint configuration')
if report['alerts']:sys.exit(1)
