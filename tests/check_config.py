#!/usr/bin/env python3
"""Static policy checks plus interpolation checks without a Docker daemon."""
import json
from pathlib import Path
import re
import subprocess
import tempfile
import sys

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'scripts/lib'))
from homelab import STACKS, CORE, config

def _check(temporary):
    c=config(example=True)
    import os
    env=dict(os.environ,**c,RENDER_GID='1000')
    env['REPO_ROOT']=str(root)
    env['SECRETS_ROOT']=str(Path(temporary)/'absent-secrets')
    all_services={}
    for stack in STACKS:
        raw=subprocess.check_output(['docker','compose','--env-file',str(root/'.env.example'),
                                     '-f',str(root/'stacks'/stack/'compose.yaml'),'--profile','*',
                                     'config','--format','json'],env=env,text=True)
        obj=json.loads(raw)
        assert obj['name']=='homelab-'+stack
        assert obj['services']
        for name,svc in obj['services'].items():
            all_services[stack+'/'+name]=svc
            image=svc['image']
            assert ':' in image and not re.search(r':(latest|main|stable)$',image),image
            if image.startswith(('lscr.io/linuxserver/', 'linuxserver/', 'ghcr.io/linuxserver/')):
                assert svc.get('init') is False, (stack, name, 's6 must run as PID 1 without Docker init')
            assert not svc.get('privileged',False)
            assert svc.get('logging',{}).get('options',{}).get('max-size')
            for v in svc.get('volumes',[]):
                if v['type']=='bind':assert not v.get('bind',{}).get('create_host_path',False),(stack,name,v)
            for port in svc.get('ports',[]):
                assert port.get('host_ip') not in (None,'0.0.0.0','::'),(stack,name,port)
            if name=='postgres':assert not svc.get('ports')
            if stack=='media':assert svc['restart']=='no'
        if stack=='media':
            for name in ['radarr','sonarr','qbittorrent']:
                s=obj['services'][name]
                data=[v for v in s['volumes'] if v['target']=='/data']
                assert len(data)==1 and data[0]['source']==c['MEDIA_ROOT']
                assert s['depends_on']['mount-guard']['condition']=='service_completed_successfully'
            jellyfin=obj['services']['jellyfin']
            assert not jellyfin.get('profiles')
            assert 'plex' not in obj['services']
            assert jellyfin['user']==c['PUID']+':'+c['PGID']
            assert jellyfin['cap_drop']==['ALL']
            assert jellyfin['depends_on']['mount-guard']['condition']=='service_completed_successfully'
            assert set(jellyfin['networks'])=={'media','proxy'}
            assert jellyfin['labels']['traefik.http.routers.jellyfin.entrypoints']=='websecure'
            assert jellyfin['labels']['traefik.http.routers.jellyfin.middlewares']=='headers@file'
            assert len(jellyfin['ports'])==1
            assert jellyfin['ports'][0]['host_ip']==c['LAN_IP']
            assert jellyfin['ports'][0]['target']==8096
            mounts={v['target']:v for v in jellyfin['volumes']}
            assert mounts['/data']['source']==c['MEDIA_ROOT'] and mounts['/data']['read_only']
            assert mounts['/config']['source']==c['APPDATA_ROOT']+'/jellyfin'
            assert mounts['/cache']['source']==c['RUNTIME_ROOT']+'/jellyfin-cache'
            assert mounts['/run/expected-media-device']['source']=='/dev/disk/by-uuid/'+c['MEDIA_UUID']
            assert mounts['/run/expected-media-device']['read_only']
        if stack=='infrastructure':
            assert not obj['services']['socket-proxy'].get('ports')
            assert obj['services']['socket-proxy']['environment']['POST']=='0'
            assert '--providers.docker.exposedbydefault=false' in obj['services']['traefik']['command']
            networks = obj['services']['traefik']['networks']
            assert networks['proxy']['aliases'] == ['auth.' + c['DOMAIN']]
            assert not networks['discovery'].get('aliases')
            assert not networks['tunnel'].get('aliases')
            assert obj['services']['cloudflared']['profiles']==['tunnel']
        if stack=='monitoring':
            beszel=obj['services']['beszel']
            assert not beszel.get('ports')
            assert beszel['environment']['APP_URL']=='https://monitor.'+c['DOMAIN']
            assert beszel['labels']['traefik.http.routers.monitor.middlewares']=='private@file,headers@file,beszel-sso@file'
        if stack=='management':
            dockge=obj['services']['dockge']
            assert dockge['profiles']==['admin']
            assert dockge['restart']=='no'
            assert dockge['depends_on']['data-guard']['condition']=='service_completed_successfully'
            view=c['RUNTIME_ROOT']+'/dockge-stacks'
            assert dockge['environment']['DOCKGE_STACKS_DIR']==view
            assert any(v['source']==view and v['target']==view and v['read_only'] for v in dockge['volumes'])
            assert any(v['source']==str(root) and v['target']==str(root) and v['read_only'] for v in dockge['volumes'])
        if stack=='ai':
            webui=obj['services']['webui']
            assert webui['environment']['ENABLE_OAUTH_PERSISTENT_CONFIG']=='true'
            assert webui['environment']['ENABLE_SIGNUP']=='false'
            assert not any(key.startswith(('WEBUI_ADMIN_', 'OAUTH_CLIENT_')) for key in webui['environment'])
            assert not webui.get('ports')
            assert webui['labels']['traefik.http.routers.ai.middlewares']=='headers@file,sso@file'
            assert webui['depends_on']['data-guard']['condition']=='service_completed_successfully'
        if stack in ('storage', 'management', 'ai'):
            assert all(svc['restart']=='no' for svc in obj['services'].values())
    # The same external environment activates profiles in plain Compose/Dockge.
    for profiles in ('', 'bazarr,admin'):
        for stack, optional, profile in (('media', 'bazarr', 'bazarr'),
                                         ('management', 'dockge', 'admin')):
            raw=subprocess.check_output(['docker','compose','--env-file',str(root/'.env.example'),
                '-f',str(root/'stacks'/stack/'compose.yaml'),'config','--services'],
                env={**env,'COMPOSE_PROFILES':profiles},text=True)
            if stack=='media': assert 'jellyfin' in raw.split() and 'plex' not in raw.split()
            assert (optional in raw.split()) == (profile in profiles.split(',')), (stack, profiles)
    for workflow in (root/'config/n8n/workflows').glob('*.json'):
        w=json.loads(workflow.read_text())
        assert w['active'] is False
        for node in w['nodes']:
            assert node['type'] not in ('n8n-nodes-base.executeCommand','n8n-nodes-base.readWriteFile')
            if node['type']=='n8n-nodes-base.webhook':assert node['parameters']['authentication']=='headerAuth'
    overlay=json.loads(subprocess.check_output(['docker','compose','--env-file',str(root/'.env.example'),
        '-f',str(root/'stacks/storage/compose.yaml'),'-f',str(root/'stacks/storage/media-readonly.yaml'),
        'config','--format','json'],env=env,text=True))
    assert overlay['services']['files']['depends_on']['mount-guard']['condition']=='service_completed_successfully'
    assert overlay['services']['files']['depends_on']['data-guard']['condition']=='service_completed_successfully'
    assert overlay['services']['mount-guard']['network_mode']=='none'
    assert any(v['target']=='/expected-device' and v['source'].endswith('/'+c['MEDIA_UUID'])
               for v in overlay['services']['mount-guard']['volumes'])
    secret_dir=Path(temporary)/'absent-secrets';secret_dir.mkdir()
    fixture_password='audit-only-$literal#password'
    (secret_dir/'webui-bootstrap.env').write_text('WEBUI_ADMIN_EMAIL=audit@example.test\nWEBUI_ADMIN_PASSWORD='+fixture_password+'\n')
    onboarding=json.loads(subprocess.check_output(['docker','compose','--env-file',str(root/'.env.example'),
        '-f',str(root/'stacks/ai/compose.yaml'),'-f',str(root/'stacks/ai/bootstrap.example.yaml'),
        'config','--format','json'],env=env,text=True))
    assert onboarding['services']['webui']['environment']['ENABLE_SIGNUP']=='false'
    # Compose serializes literal dollars doubled so its rendered model can be re-read.
    assert onboarding['services']['webui']['environment']['WEBUI_ADMIN_PASSWORD']==fixture_password.replace('$','$$')
    assert onboarding['services']['webui']['depends_on']['data-guard']['condition']=='service_completed_successfully'
    def seconds(value):
        match=re.fullmatch(r'(\d+)([smh])',value)
        assert match, value
        return int(match[1])*{'s':1,'m':60,'h':3600}[match[2]]
    unit=(root/'config/systemd/homelab-mount-guard.service').read_text()
    budget=seconds(re.search(r'^TimeoutStartSec=(.+)$',unit,re.MULTILINE)[1])
    needed=sum(max(seconds(service['stop_grace_period']) for key,service in all_services.items()
                   if key.startswith(stack+'/')) for stack in ('media','storage','management','ai'))
    assert budget>needed, 'Mount guard unit must allow every affected stack to stop gracefully'
    assert 'ai' not in CORE
    for p in [root/'install.sh', *(root/'scripts').glob('*.sh')]:
        text=p.read_text()
        assert 'set -euo pipefail' in text,p
        assert not re.search(r'\b(mkfs|wipefs|fdisk|parted)\b|down\s+-v|prune\s+-a|rm\s+-rf|rsync.*--delete',text),p
    print(f'Static policy checks passed: {len(STACKS)} stacks, {len(all_services)} services')

def check():
    with tempfile.TemporaryDirectory(prefix='homelab-policy-') as temporary:
        _check(temporary)

if __name__=='__main__':check()
