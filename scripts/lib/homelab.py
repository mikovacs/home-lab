#!/usr/bin/env python3
"""HomeLab control plane. No shell evaluation of operator configuration."""
import argparse
import contextlib
import datetime
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
STACKS = ('infrastructure', 'identity', 'media', 'storage', 'automation',
          'monitoring', 'management', 'remote-access', 'ai')
CORE = ('infrastructure', 'identity', 'monitoring', 'management')
# MemTotal excludes reserved RAM. Allow 512 MiB of reservations on an 8 GiB host.
# This is an eligibility floor, not a guarantee that a model fits available RAM.
AI_MIN_VISIBLE_RAM_MIB = 7680
MEDIA_DIRS = ('media/movies', 'media/tv', 'downloads/torrents/complete/movies',
              'downloads/torrents/complete/tv', 'downloads/torrents/incomplete')
SECRET_NAMES = ('authentik-db-password', 'authentik-secret-key', 'n8n-db-password',
                'n8n-encryption-key', 'webui-secret-key', 'restic-password')
STACK_SECRETS = {'infrastructure': ('cloudflare-dns-token', 'admin-htpasswd'),
                 'identity': ('authentik-db-password', 'authentik-secret-key'),
                 'automation': ('n8n-db-password', 'n8n-encryption-key'),
                 'ai': ('webui-secret-key',), 'remote-access': ('admin-htpasswd',),
                 'monitoring': ('admin-htpasswd',)}
APPS = {'traefik': 0, 'adguard-conf': 0, 'adguard-work': 0,
        'authentik-postgres': 70, 'authentik-data': 1000, 'authentik-templates': 1000,
        'n8n-postgres': 70, 'n8n': 1000, 'plex': None, 'radarr': None, 'sonarr': None,
        'prowlarr': None, 'qbittorrent': None, 'bazarr': None, 'files': None,
        'kuma': 0, 'beszel': 0, 'homepage': None, 'dockge': 0, 'webui': 0}


class SafetyError(RuntimeError):
    pass


def require(ok, message):
    if not ok:
        raise SafetyError(message)


def run(args, *, capture=False, dry=False, env=None, **kwargs):
    if dry:
        print('DRY RUN:', ' '.join(map(str, args)))
        return ''
    result = subprocess.run(list(map(str, args)), check=True, text=True,
                            stdout=subprocess.PIPE if capture else None,
                            env=env, **kwargs)
    return result.stdout if capture else ''


def read_env(path):
    values = {}
    for i, line in enumerate(Path(path).read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        require(re.fullmatch(r'[A-Z][A-Z0-9_]*=[^\n\r]*', line), f'{path}:{i}: invalid KEY=value')
        key, value = line.split('=', 1)
        require(key not in values, f'Duplicate setting: {key}')
        require(not any(x in value for x in ('$','`','"',"'",'\\')), f'Unsupported quoting/interpolation: {key}')
        values[key] = value
    return values


def env_file():
    return Path(os.environ.get('HOMELAB_ENV_FILE', str(ROOT / '.env'))).absolute()


def config(example=False):
    p = ROOT / '.env.example' if example else env_file()
    require(p.is_file(), 'Create host configuration with make init or the interactive installer first')
    c = read_env(ROOT / '.env.example')
    c.update(read_env(p))
    validate_paths(c, check_config_canonical=not example)
    return c


def safe_path(value):
    p = Path(value)
    require(p.is_absolute() and str(p) == value and value not in ('/', '/mnt', '/srv', '/home', '/etc', '/var'),
            f'Unsafe absolute path: {value}')
    require(not any(x in value for x in ('..', '\n', ' ', ':', '\t')), f'Unsafe path: {value}')
    forbidden = ('/etc','/proc','/sys','/dev','/usr','/boot','/bin','/sbin','/lib','/lib64','/var/lib/docker')
    require(not any(value==x or value.startswith(x+'/') for x in forbidden),f'System path forbidden: {value}')
    require(p.resolve() == p, f'Symlinked path forbidden: {value}')
    return p


def configuration_directory(c, canonical=True):
    value = c.get('HOMELAB_CONFIG_DIR', '/etc/homelab')
    path = Path(value)
    if value == '/etc/homelab' or value.startswith('/etc/homelab/'):
        require(path.is_absolute() and (not canonical or path.resolve() == path) and not any(x in value for x in ('..', ' ', '\n', '\r', ':', '\t')),
                'Host configuration directory must be canonical and safe')
    else:
        safe_path(value)
    return path


def validate_paths(c, check_config_canonical=True):
    names = ('REPO_ROOT','APPDATA_ROOT','SECRETS_ROOT','RUNTIME_ROOT','DATA_ROOT','MEDIA_ROOT')
    paths = [safe_path(c[n]) for n in names]
    cfg = configuration_directory(c, canonical=check_config_canonical)
    require(all(cfg != p and cfg not in p.parents and p not in cfg.parents for p in paths),
            'Host configuration must not overlap repository, application state, data or media roots')
    for i, p in enumerate(paths):
        for q in paths[i+1:]:
            require(p != q and p not in q.parents and q not in p.parents,
                    f'Storage roots must not overlap: {p}, {q}')
    for n in ('PUID','PGID'):
        require(c[n].isdigit() and int(c[n]) > 0, f'{n} must be a non-root numeric ID')
    require(re.fullmatch(r'[a-zA-Z0-9.-]+', c['DOMAIN']), 'Invalid domain')
    for n in ('MEDIA_UUID','DATA_UUID'):
        require(re.fullmatch(r'[a-zA-Z0-9_-]+', c[n]), f'Invalid {n}')
    require(0 < int(c['DISK_WARN']) < int(c['DISK_CRITICAL']) < 100, 'Invalid disk thresholds')


def mounted(path, uuid, *, media=False):
    require(uuid != 'SET_ME', 'Set the filesystem UUID from read-only preflight output')
    root = safe_path(path)
    raw = run(['findmnt','--json','--mountpoint',str(root),'-o','TARGET,UUID,FSTYPE,OPTIONS'], capture=True)
    entries = json.loads(raw).get('filesystems', [])
    require(len(entries) == 1, f'Expected exact mount at {root}')
    item = entries[0]
    require(item['target'] == str(root) and item['uuid'] == uuid, f'Wrong filesystem at {root}')
    require('rw' in item['options'].split(','), f'Read-only filesystem at {root}')
    require(not media or item['fstype'] == 'ext4', 'Media filesystem must be ext4')
    require(root.stat().st_dev != Path('/').stat().st_dev, f'{root} is on OS filesystem')
    device=Path('/dev/disk/by-uuid')/uuid
    require(device.exists() and stat.S_ISBLK(device.stat().st_mode) and device.stat().st_rdev==root.stat().st_dev, 'Expected UUID block device is unavailable or does not match mount')
    return root


def check_media(c):
    root = mounted(c['MEDIA_ROOT'], c['MEDIA_UUID'], media=True)
    for rel in MEDIA_DIRS:
        p = root / rel
        require(p.is_dir() and p.resolve() == p, f'Missing/redirected media directory: {p}')
        require(p.stat().st_dev == root.stat().st_dev, f'Cross-filesystem path: {p}')
        mode = p.stat()
        uid, gid = int(c['PUID']), int(c['PGID'])
        bits = (mode.st_mode >> 6) & 7 if mode.st_uid == uid else ((mode.st_mode >> 3) & 7 if mode.st_gid == gid else mode.st_mode & 7)
        require(bits & 5 == 5, f'Configured service IDs cannot read/traverse {p}')
        if not bits & 2:
            print(f'WARNING: {p} not writable by configured service IDs; review ACLs manually')
    free = shutil.disk_usage(root).free
    require(free >= int(c['MIN_MEDIA_FREE_GIB']) * 1024**3, 'Insufficient free media storage')
    print(f'Media UUID verified; {free // 1024**3} GiB free; common filesystem supports hardlinks.')
    # Only metadata reads; never create a probe link or enumerate entire libraries.
    for rel in ('media/movies','media/tv'):
        with os.scandir(root / rel) as entries:
            first = next(entries, None)
            print(rel + ': ' + ('visible entries' if first else 'empty'))


def need_secrets(c, stack):
    root = Path(c['SECRETS_ROOT'])
    require(root.is_dir() and stat.S_IMODE(root.stat().st_mode) == 0o700, 'Secrets directory must be mode 0700')
    for name in STACK_SECRETS.get(stack, ()):
        p = root / name
        require(p.is_file() and not p.is_symlink() and p.stat().st_size > 10,
                f'Missing/empty secret: {name}')
        require(stat.S_IMODE(p.stat().st_mode) == 0o600, f'Secret must have mode 0600: {name}')
        if name == 'cloudflare-dns-token':
            require('SET_ME' not in p.read_text(), 'Configure a real scoped Cloudflare DNS token')


def compose(c, stack, *args, dry=False, capture=False, stdin_text=None):
    require(stack in STACKS, f'Unknown stack: {stack}')
    # Preserve the explicit homelab-<stack> project identity from Compose.
    env = dict(os.environ)
    for k in read_env(ROOT / '.env.example'):
        env.pop(k, None)
    env.update(c)
    env.pop('COMPOSE_PROFILES', None)
    env.pop('COMPOSE_PROJECT_NAME', None)
    cmd = ['docker','compose','--project-directory',ROOT/'stacks'/stack,
           '--env-file',env_file(),'-f',ROOT/'stacks'/stack/'compose.yaml']
    return run(cmd + list(args), dry=dry, capture=capture, env=env, input=stdin_text)


def root_only():
    require(sys.platform.startswith('linux') and os.geteuid() == 0, 'Run on Debian as root (sudo)')


def mkdir_new(p, uid=0, gid=0, mode=0o750):
    p = Path(p)
    safe_path(str(p))
    if p.exists():
        require(p.is_dir(), f'Expected directory: {p}')
        return
    p.mkdir(parents=True, mode=mode)
    p.chmod(mode)
    if os.geteuid() == 0:
        os.chown(p, uid, gid)  # New directory only. Never recurse or alter existing data.


def atomic(p, text, mode=0o640):
    p = Path(p)
    require(not p.is_symlink(), f'Refusing symlink: {p}')
    fd, name = tempfile.mkstemp(dir=p.parent, prefix='.'+p.name)
    try:
        with os.fdopen(fd,'w') as out:
            out.write(text)
        os.chmod(name, mode)
        os.replace(name, p)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def prepare(c, dry=False):
    require(Path(c['REPO_ROOT']) == ROOT, 'REPO_ROOT must equal the checkout absolute path')
    if dry:
        print('DRY RUN: create new appdata/runtime directories only; existing ownership untouched')
        return
    root_only()
    cfg = configuration_directory(c)
    if cfg != ROOT and not cfg.exists():
        cfg.mkdir(parents=True, mode=0o700)
    for name in ('APPDATA_ROOT','RUNTIME_ROOT'):
        mkdir_new(c[name], mode=0o755)
    for name, uid in APPS.items():
        mkdir_new(Path(c['APPDATA_ROOT'])/name, int(c['PUID']) if uid is None else uid,
                  int(c['PGID']) if uid is None else uid)
    for sub in ('traefik','beszel','homepage','dumps','restore'):
        mkdir_new(Path(c['RUNTIME_ROOT'])/sub, int(c['PUID']) if sub=='homepage' else 0,
                  int(c['PGID']) if sub=='homepage' else 0, 0o755 if sub in ('traefik','beszel','homepage') else 0o700)
    for stack in STACKS:
        p = ROOT/'stacks'/stack/'.env'
        if not p.exists() and not p.is_symlink():
            p.symlink_to(env_file())
        require(p.resolve() == env_file().resolve(), f'{p}: must link to the selected host environment file')
    # Secondary data directories only when the configured filesystem is already mounted.
    if c['DATA_UUID'] != 'SET_ME':
        data = mounted(c['DATA_ROOT'],c['DATA_UUID'])
        for sub in ('files','shared','ai/models','backups','.usage'):
            mkdir_new(data/sub,int(c['PUID']),int(c['PGID']))
    render(c)


def generate_secrets(c, dry=False):
    if dry:
        print('DRY RUN: create only missing random secrets; never rotate existing keys')
        return
    root_only()
    root = Path(c['SECRETS_ROOT'])
    mkdir_new(root, mode=0o700)
    require(stat.S_IMODE(root.stat().st_mode) == 0o700, 'Secrets root must be mode 0700')
    bootstrap = root / 'authentik-bootstrap.env'
    if not bootstrap.exists():
        atomic(bootstrap, '', 0o600)
    for name in SECRET_NAMES:
        p = root/name
        if not p.exists():
            atomic(p, secrets.token_hex(32)+'\n', 0o600)
            # Native file-secret consumers run as UID 1000; parent stays root-only.
            if name in ('authentik-db-password','authentik-secret-key','n8n-db-password','n8n-encryption-key'):
                os.chown(p,1000,1000)
    print('Generated missing secrets. Add cloudflare-dns-token, admin-htpasswd and optional tunnel-credentials manually.')


def render_dockge_stacks(c):
    """Expose canonical source through project-named links, without copying it."""
    view = Path(c['RUNTIME_ROOT']) / 'dockge-stacks'
    mkdir_new(view, mode=0o755)
    expected = {'homelab-' + stack: Path(c['REPO_ROOT']) / 'stacks' / stack for stack in STACKS}
    # Validate the entire existing view before adding anything. Never replace
    # operator files, redirect links, or remove unknown entries automatically.
    for entry in view.iterdir():
        require(entry.name in expected and entry.is_symlink()
                and entry.readlink() == expected[entry.name],
                f'Conflicting Dockge discovery entry: {entry}')
    for name, target in expected.items():
        link = view / name
        if not link.is_symlink():
            link.symlink_to(target, target_is_directory=True)


def render(c):
    """JSON is a YAML subset; avoid a runtime YAML package dependency."""
    runtime = Path(c['RUNTIME_ROOT'])
    admin = [str(ipaddress.ip_network(x.strip())) for x in c['ADMIN_CIDRS'].split(',')]
    require(all(ipaddress.ip_network(x).prefixlen>0 for x in admin),'Administrative allowlist cannot contain a default route')
    middlewares = {'headers':{'headers':{'contentTypeNosniff':True,'referrerPolicy':'same-origin','stsSeconds':31536000,'stsIncludeSubdomains':False}},
                   'private':{'ipAllowList':{'sourceRange':admin}},
                   'admin-auth':{'basicAuth':{'usersFile':'/run/secrets/admin-htpasswd','removeHeader':True}},
                   'sso':{'forwardAuth':{'address':'http://server:9000/outpost.goauthentik.io/auth/traefik',
                                        'trustForwardHeader':False,'authResponseHeaders':['X-authentik-username','X-authentik-email','X-authentik-groups']}}}
    # Alias server resolves only on shared proxy network; no public/internal API catch-all bypass.
    # PocketBase uses Authorization for its native token. Authenticate the proxy
    # session using cookies only; leave the original token untouched for Beszel.
    middlewares['beszel-sso']={'forwardAuth':{
        'address':'http://server:9000/outpost.goauthentik.io/auth/traefik',
        'trustForwardHeader':False,'authRequestHeaders':['Cookie']}}
    dynamic = {'http':{'middlewares':middlewares,'routers':{},'services':{}}}
    allowed = {'auth':('http://server:9000',[]),'home':('http://homepage:3000',['sso@file']),
               'files':('http://files:8080',[]),'radarr':('http://radarr:7878',['sso@file']),
               'sonarr':('http://sonarr:8989',['sso@file']),'prowlarr':('http://prowlarr:9696',['sso@file']),
               'torrent':('http://qbittorrent:8080',['sso@file']),'n8n':('http://n8n:5678',['sso@file']),
               'ai':('http://webui:8080',['sso@file']),'status':('http://kuma:3001',['sso@file']),
               'hooks':('http://n8n:5678',[])}
    remote = [x.strip() for x in c['REMOTE_APPS'].split(',') if x.strip()]
    require(len(remote)==len(set(remote)), 'Duplicate remote application')
    require(set(remote) <= set(allowed), 'Forbidden remote app (private admin and Plex cannot be exported)')
    require(not remote or c['REMOTE_READY']=='yes' and c['IDENTITY_READY']=='yes', 'Complete identity/endpoint setup before remote exposure')
    ingress=[]
    for name in remote:
        url, mids = allowed[name]
        host = name+'.'+c['DOMAIN']
        rule = 'Host(`'+host+'`)'
        if name=='hooks':
            rule += ' && PathPrefix(`/webhook/`)'
        dynamic['http']['routers']['remote-'+name] = {'entryPoints':['tunnel'],'rule':rule,'service':'remote-'+name,'tls':{},'middlewares':['headers@file']+mids}
        dynamic['http']['services']['remote-'+name] = {'loadBalancer':{'servers':[{'url':url}]}}
        ingress.append({'hostname':host,'service':'https://traefik:8443','originRequest':{'originServerName':host,'httpHostHeader':host}})
    dynamic['http']['routers']['local-hooks']={'entryPoints':['websecure'],'rule':'Host(`hooks.'+c['DOMAIN']+'`) && PathPrefix(`/webhook/`)','tls':{'certResolver':'cloudflare'},'service':'hooks','middlewares':['headers@file']}
    dynamic['http']['services']['hooks']={'loadBalancer':{'servers':[{'url':'http://n8n:5678'}]}}
    # Authentik embedded outpost callback must reach Authentik for every protected host.
    dynamic['http']['routers']['outpost']={'entryPoints':['websecure'],'rule':'HostRegexp(`^.+\\.'+re.escape(c['DOMAIN'])+'$`) && PathPrefix(`/outpost.goauthentik.io/`)','priority':1000,'tls':{'certResolver':'cloudflare'},'service':'outpost'}
    dynamic['http']['services']['outpost']={'loadBalancer':{'servers':[{'url':'http://server:9000'}]}}
    # This higher-priority route also restricts Beszel callbacks to admin sources.
    dynamic['http']['routers']['beszel-outpost']={
        'entryPoints':['websecure'],
        'rule':'Host(`monitor.'+c['DOMAIN']+'`) && PathPrefix(`/outpost.goauthentik.io/`)',
        'priority':1100,'tls':{'certResolver':'cloudflare'},'service':'outpost',
        'middlewares':['private@file','headers@file']}
    remote_sso=[name for name in remote if 'sso@file' in allowed[name][1]]
    if remote_sso:
        dynamic['http']['routers']['remote-outpost']={'entryPoints':['tunnel'],'rule':'('+ ' || '.join('Host(`'+x+'.'+c['DOMAIN']+'`)' for x in remote_sso)+') && PathPrefix(`/outpost.goauthentik.io/`)','priority':1000,'tls':{},'service':'outpost'}
    atomic(runtime/'traefik'/'dynamic.yml',json.dumps(dynamic,indent=2)+'\n',0o644)
    ingress.append({'service':'http_status:404'})
    atomic(runtime/'cloudflared.yml',json.dumps({'tunnel':c['TUNNEL_ID'],'credentials-file':'/run/secrets/tunnel-credentials','ingress':ingress},indent=2)+'\n',0o644)
    files = {'http':{'port':8080,'trustProxyHeaders':True},
             'server':{'cacheDir':'/home/filebrowser/data/tmp','sources':[
                 {'path':'/srv/personal','name':'Personal','config':{'defaultEnabled':False}},
                 {'path':'/srv/shared','name':'Shared','config':{'defaultEnabled':True}}]},
             'auth':{'methods':{'password':{'enabled':True,'signup':False,'minLength':14},
                                'oidc':{'enabled':False}}}}
    oidc=Path(c['SECRETS_ROOT'])/'files-oidc.json'
    if oidc.exists():
        client=json.loads(oidc.read_text())
        require(client.get('clientId') and client.get('clientSecret'),'Invalid files OIDC credentials')
        files['auth']['methods']['oidc']={'enabled':True,**client,
             'issuerUrl':'https://auth.'+c['DOMAIN']+'/application/o/files/',
             'adminGroup':'homelab-admins','userGroups':['homelab-admins','homelab-users'],
             'groupsClaim':'groups','scopes':'openid profile email'}
    # Config contains OAuth client secret; owner matches the unprivileged file manager.
    atomic(runtime/'files.yaml',json.dumps(files,indent=2)+'\n',0o600)
    if os.geteuid()==0:os.chown(runtime/'files.yaml',int(c['PUID']),int(c['PGID']))
    services=[]
    groups={'Media':['plex','radarr','sonarr','prowlarr','torrent'],'Storage':['files'],'Automation':['n8n'],
            'Artificial intelligence':['ai'],'Monitoring':['status','monitor'],'Infrastructure':['auth','adguard','traefik'],'Administration':['docker','terminal']}
    for group,names in groups.items():
        services.append({group:[{n:{'href':f'https://{n}.{c["DOMAIN"]}','description':n}} for n in names]})
    hp = runtime/'homepage'
    atomic(hp/'services.yaml',json.dumps(services,indent=2)+'\n',0o644)
    atomic(hp/'settings.yaml',json.dumps({'title':'HomeLab','theme':'dark'})+'\n',0o644)
    atomic(hp/'widgets.yaml',json.dumps([{'resources':{'cpu':True,'memory':True,'disk':'/storage'}}])+'\n',0o644)
    atomic(hp/'bookmarks.yaml','[]\n',0o644)
    render_dockge_stacks(c)


def gates(c, stack):
    require(Path(c['REPO_ROOT']) == ROOT, 'REPO_ROOT must match checkout')
    require(c['LAN_IP'] != '192.0.2.10' and c['DOMAIN'] != 'example.com', 'Configure real LAN_IP and DOMAIN')
    addr=ipaddress.ip_address(c['LAN_IP'])
    require(addr.version==4 and addr.is_private and not addr.is_loopback and not addr.is_link_local,'LAN_IP must be a private LAN IPv4 address')
    need_secrets(c,stack)
    if stack in ('media','storage','ai','management'):
        if stack=='media':
            check_media(c)
        else:
            mounted(c['DATA_ROOT'],c['DATA_UUID'])
    if stack in ('media','storage','automation','management','ai'):
        require(c['IDENTITY_READY']=='yes', 'Configure Authentik providers, MFA and policies; set IDENTITY_READY=yes')
    if stack=='remote-access':
        require(c['ADMIN_SSH_USER'] not in ('SET_ME','root') and c['ADMIN_SSH_HOST']!='SET_ME', 'Configure non-root SSH target')
        for name in ('id_ed25519','known_hosts'):
            require((Path(c['SECRETS_ROOT'])/'terminal-ssh'/name).is_file(), 'Provision pinned terminal SSH credentials')
    if stack=='ai':
        total = int(re.search(r'MemTotal:\s+(\d+)',Path('/proc/meminfo').read_text())[1])//1024
        require(total>=AI_MIN_VISIBLE_RAM_MIB,
                'AI requires at least 7680 MiB OS-visible RAM (8 GiB-class host); review available RAM before enabling')
    require((Path(c['RUNTIME_ROOT'])/'traefik/dynamic.yml').is_file(), 'Run make prepare first')


def start_timeout(c):
    timeout = c.get('STACK_START_TIMEOUT', '600')
    require(timeout.isdigit() and 1 <= int(timeout) <= 3600, 'STACK_START_TIMEOUT must be 1–3600 seconds')
    return timeout


def deploy(c, stack, dry=False):
    gates(c,stack)  # Dry runs still fail closed.
    compose(c,stack,'config','--quiet')
    timeout = start_timeout(c)
    # Source directory replacement and remounted storage need fresh bind mounts, even with unchanged YAML.
    compose(c,stack,'up','-d','--force-recreate','--wait','--wait-timeout',timeout,dry=dry)
    if not dry:
        health(c,stack)


def health(c,stack=None):
    failed=[]
    observed_any=False
    for n in ([stack] if stack else STACKS):
        records=compose(c,n,'ps','--all','--format','json',capture=True)
        if not records.strip():
            if stack: failed.append(n+' (no containers)')
            continue
        try:
            parsed=json.loads(records)
            rows=parsed if isinstance(parsed,list) else [parsed]
        except json.JSONDecodeError:
            rows=[json.loads(x) for x in records.splitlines()]
        observed_any=observed_any or bool(rows)
        expected=set(compose(c,n,'config','--services',capture=True).split())
        observed={row.get('Service') for row in rows}
        failed.extend(n+'/'+name+' (missing)' for name in sorted(expected-observed))
        for row in rows:
            if (row.get('Service') in ('mount-guard','data-guard')
                and row.get('State')=='exited' and row.get('ExitCode')==0):
                continue
            if row.get('State')!='running' or row.get('Health','') not in ('','healthy'):
                failed.append(n+'/'+row.get('Service','?'))
    require(observed_any, 'No application containers found; deploy the selected stacks first')
    require(not failed,'Unhealthy or stopped containers: '+', '.join(failed))
    print('Container status checks passed (HTTP/application setup still requires acceptance testing).')


def configure_storage(c, dry=False, confirmed=False):
    lines=[]
    for prefix in ('MEDIA','DATA'):
        uuid=c[prefix+'_UUID']; path=c[prefix+'_ROOT']
        mounted(path,uuid,media=True)
        # Nonzero passno schedules boot-time fsck, whose default can repair data.
        # Filesystem repair is an explicit manual operation, including on media.
        lines.append(f'UUID={uuid} {path} ext4 defaults,nofail,x-systemd.device-timeout=10s 0 0')
    proposal='\n'.join(lines)+'\n'
    print('Proposed fstab additions:\n'+proposal)
    if dry:
        return
    require(confirmed,'Storage change requires --confirm-storage after reviewing UUIDs and mount points')
    root_only()
    fstab=Path('/etc/fstab'); original=fstab.read_text()
    additions=[]
    for line in lines:
        uuid,path=line.split()[:2]
        matches=[x.split() for x in original.splitlines() if x.strip() and not x.lstrip().startswith('#')]
        conflicts=[x for x in matches if x[0]==uuid or x[1]==path]
        require(not conflicts or all(x[:3]==[uuid,path,'ext4'] for x in conflicts),'Conflicting fstab entry; reconcile manually')
        require(all(len(x)<6 or x[5]=='0' for x in conflicts),
                'Existing fstab entry enables automatic filesystem repair; review passno manually before continuing')
        if not conflicts:
            additions.append(line)
    if additions:
        backup=fstab.with_name('fstab.homelab-'+datetime.datetime.now().strftime('%Y%m%d%H%M%S'))
        require(not backup.exists(),'fstab backup already exists')
        shutil.copy2(fstab,backup)
        atomic(fstab,original.rstrip()+'\n'+'\n'.join(additions)+'\n',0o644)
        run(['systemctl','daemon-reload'])
    print('No formatting, mounting, chmod/chown or media directory writes performed.')


def preflight():
    commands=[['uname','-a'],['lscpu'],['free','-h'],['swapon','--show'],
              ['lspci','-nn'],['lsblk','-o','NAME,MODEL,SERIAL,SIZE,TYPE,FSTYPE,UUID,MOUNTPOINTS'],
              ['findmnt'],['df','-hT'],['ip','-brief','address'],['ip','route'],['ss','-lntup'],
              ['systemctl','list-units','--type=service','--state=running','--no-pager'],
              ['smartctl','--scan-open'],['docker','--version'],['docker','compose','version']]
    print('Read-only discovery. Output contains serial numbers/IPs; redact before sharing.')
    for cmd in commands:
        print('\n## '+' '.join(cmd),flush=True)
        if shutil.which(cmd[0]):
            subprocess.run(cmd,check=False)
        else:
            print('Unavailable (install during bootstrap if appropriate)')
    print('\nDRI nodes:',','.join(map(str,Path('/dev/dri').glob('*'))))
    if shutil.which('vainfo'):
        for node in sorted(Path('/dev/dri').glob('renderD*')):
            print('\n## Read-only VA-API codec discovery:',node,flush=True)
            subprocess.run(['vainfo','--display','drm','--device',str(node)],check=False)
    else:
        print('vainfo unavailable: codec acceleration remains unverified; optionally install for read-only VA-API discovery.')
    if Path('/proc/meminfo').exists():
        ram=int(re.search(r'MemTotal:\s+(\d+)',Path('/proc/meminfo').read_text())[1])//1024
        print('RAM MiB:',ram)
        print('Recommendation:', 'Core only; enable stacks incrementally' if ram<AI_MIN_VISIBLE_RAM_MIB else 'Core/media; AI optional after measuring free RAM')
        print('AI model guidance:', 'No local LLM recommended' if ram<AI_MIN_VISIBLE_RAM_MIB else 'Start with one 0.5–1.5B Q4 model, no automatic downloads')
    print('SMART detail: administrator may run smartctl -a on discovered devices; USB may need -d sat. Do not run self-tests automatically.')


def restic_environment(c):
    env=dict(os.environ, RESTIC_REPOSITORY=c['RESTIC_REPOSITORY'],
             RESTIC_PASSWORD_FILE=c['SECRETS_ROOT']+'/restic-password')
    extra=Path(c['SECRETS_ROOT'])/'restic-env.json'
    if extra.exists():
        values=json.loads(extra.read_text())
        require(isinstance(values,dict) and all(isinstance(k,str) and isinstance(v,str)
                and re.fullmatch(r'[A-Z][A-Z0-9_]*',k) for k,v in values.items()),
                'Invalid Restic backend credentials')
        require(not any(k.startswith(('RESTIC_','LD_','DYLD_','PYTHON','COMPOSE_','DOCKER_'))
                        or k in ('PATH','BASH_ENV','ENV','SHELLOPTS') for k in values),
                'Backend credentials cannot override repository, password or process execution settings')
        env.update(values)
    return env


def backup(c,dry=False,check=False,prune=False,init=False):
    root_only() if not dry else None
    mounted(c['DATA_ROOT'],c['DATA_UUID'])
    repo=c['RESTIC_REPOSITORY']
    require(repo != c['MEDIA_ROOT'] and not repo.startswith(c['MEDIA_ROOT']+'/'),'Backup repository must not target media')
    if repo.startswith('/'):
        p=safe_path(repo)
        require(Path(c['DATA_ROOT']) in p.parents,'Local Restic repository must be on the verified secondary disk')
    password=Path(c['SECRETS_ROOT'])/'restic-password'
    require(password.is_file(),'Missing restic-password')
    env=restic_environment(c)
    def restic(*args):
        return run(['restic',*args],env=env,dry=dry)
    if init:
        restic('init');return
    if check:
        restic('check','--read-data-subset=10%');return
    if prune:
        restic('forget','--keep-daily',c['RESTIC_KEEP_DAILY'],'--keep-weekly',c['RESTIC_KEEP_WEEKLY'],
               '--keep-monthly',c['RESTIC_KEEP_MONTHLY'],'--prune');return
    runtime=Path(c['RUNTIME_ROOT']); dumps=runtime/'dumps'
    require(dumps.is_dir(),'Run prepare first')
    timeout=start_timeout(c)
    run(['restic','version'],dry=dry)
    # Stop application writers before dumps and SQLite copies. Restore exactly the running set.
    active={}
    for n in STACKS:
        services=compose(c,n,'ps','--status','running','--services',capture=True).split()
        active[n]=services
    restart_errors=[]
    logical_dumps=[]
    try:
        for n,services in active.items():
            writers=[s for s in services if s not in ('postgres','socket-proxy','cloudflared')]
            if writers:
                compose(c,n,'stop',*writers,dry=dry)
        for n,db in [('identity','authentik'),('automation','n8n')]:
            if 'postgres' in active[n]:
                if dry:
                    print('DRY RUN: pg_dump',n)
                else:
                    target=dumps/(db+'.sql')
                    fd,tmp=tempfile.mkstemp(dir=dumps)
                    try:
                        with os.fdopen(fd,'w') as out:
                            pg_env=dict(os.environ,**c)
                            pg_env.pop('COMPOSE_PROJECT_NAME',None)
                            pg_env.pop('COMPOSE_PROFILES',None)
                            subprocess.run(['docker','compose','--project-directory',str(ROOT/'stacks'/n),'--env-file',str(env_file()),'-f',str(ROOT/'stacks'/n/'compose.yaml'),'exec','-T','postgres','pg_dump','-U',db,'--no-owner','--no-acl',db],stdout=out,env=pg_env,check=True,text=True)
                        os.replace(tmp,target)
                    finally:
                        if os.path.exists(tmp): os.unlink(tmp)
                logical_dumps.append(db)
                compose(c,n,'stop','postgres',dry=dry)
        if not dry:
            atomic(dumps/'manifest.json',json.dumps({'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'running_services':active,'fresh_logical_dumps':logical_dumps},indent=2)+'\n')
        # Databases are now stopped as well: physical copies are consistent; logical dumps provide portability.
        cfg = configuration_directory(c)
        config_source = str(cfg) if cfg.is_dir() else str(env_file())
        restic('backup',str(ROOT),config_source,str(env_file()),c['APPDATA_ROOT'],c['SECRETS_ROOT'],str(dumps),
               '--exclude-file',str(ROOT/'config/restic-excludes.txt'),'--tag','homelab')
    finally:
        for n,services in active.items():
            if not services: continue
            try:
                if n=='media': check_media(c)
                if n in ('storage','management','ai'): mounted(c['DATA_ROOT'],c['DATA_UUID'])
                compose(c,n,'start','--wait','--wait-timeout',timeout,*services,dry=dry)
            except (OSError,ValueError,SafetyError,subprocess.CalledProcessError) as exc:
                print(f'ERROR: {n} not restarted: {exc}',file=sys.stderr)
                restart_errors.append(n)
    require(not restart_errors,'Backup finished but services were not restarted: '+','.join(restart_errors))
    if not dry:
        atomic(runtime/'backup-status.json',json.dumps({'success':True,'time':datetime.datetime.now(datetime.timezone.utc).isoformat()})+'\n')


def restore(c,snapshot,target,dry=False):
    require(snapshot and re.fullmatch('[a-zA-Z0-9_-]+',snapshot),'Select snapshot ID explicitly')
    require(target,'Specify a new empty restore target under RUNTIME_ROOT/restore')
    p=safe_path(target); base=Path(c['RUNTIME_ROOT'])/'restore'
    require(base in p.parents and not p.exists(),'Restore target must be new and under runtime/restore')
    env=restic_environment(c)
    if c['RESTIC_REPOSITORY'].startswith('/'):
        repo=safe_path(c['RESTIC_REPOSITORY'])
        require(Path(c['DATA_ROOT']) in repo.parents,'Local restore repository must be on verified data storage')
        mounted(c['DATA_ROOT'],c['DATA_UUID'])
    run(['restic','restore',snapshot,'--target',str(p),'--verify'],env=env,dry=dry)
    print('Staged only. Follow docs/disaster-recovery.md; live data and media are never overwritten.')


def guard(c,dry=False):
    bad=[]
    try:
        check_media(c)
    except (OSError,SafetyError,subprocess.CalledProcessError):
        # Storage may have the optional read-only media overlay enabled outside
        # the controller. Stop it conservatively as well; never assume it is absent.
        bad.extend(['media','storage'])
    try:
        mounted(c['DATA_ROOT'],c['DATA_UUID'])
    except (OSError,SafetyError,subprocess.CalledProcessError):
        bad.extend(['storage','management','ai'])
    errors=[]
    bad=list(dict.fromkeys(bad))
    for n in bad:
        try:
            compose(c,n,'stop',dry=dry)
        except (OSError,ValueError,SafetyError,subprocess.CalledProcessError):
            errors.append(n)
    require(not bad,'Mount validation failed; stop requested for: '+','.join(bad)
            + ('; stop FAILED for: '+','.join(errors) if errors else ''))


def validate():
    config(example=True)
    run([sys.executable,ROOT/'tests/check_config.py'])
    if shutil.which('docker'):
        c=config(example=True)
        with tempfile.TemporaryDirectory(prefix='homelab-validation-') as temporary:
            for n in STACKS:
                # Optional env files must not load real host secrets during example validation.
                env=dict(os.environ,**c)
                env.update({'REPO_ROOT':str(ROOT), 'SECRETS_ROOT':str(Path(temporary)/'absent-secrets')})
                run(['docker','compose','--env-file',ROOT/'.env.example','-f',ROOT/'stacks'/n/'compose.yaml','config','--quiet'],env=env)
    else:
        raise SafetyError('Docker Compose CLI required for full validation')
    print('Repository and all Compose definitions validated')


def initialize_config(dry=False):
    """Create a private host configuration once; never replace an existing one."""
    target = env_file()
    require(not target.is_symlink(), '.env must not be a symlink')
    if target.exists():
        require(target.is_file(), '.env must be a regular file')
        print('Existing .env retained; no settings or credentials changed.')
        return
    source = (ROOT / '.env.example').read_text()
    source = re.sub(r'^REPO_ROOT=.*$', 'REPO_ROOT=' + str(ROOT), source, flags=re.MULTILINE)
    if dry:
        print('DRY RUN: create .env with checkout path; fill host settings before setup.')
        return
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as out:
        out.write(source)
    print('Created private .env. Set UUIDs, domain, LAN settings and media-compatible PUID/PGID.')


def configure_users(c, dry=False):
    """Create only missing service identities, without modifying existing identities."""
    import grp
    import pwd

    def lookup(fn, value):
        try:
            return fn(value)
        except KeyError:
            return None

    uid, gid = int(c['PUID']), int(c['PGID'])
    username, groupname = c['MEDIA_USER'], c['MEDIA_GROUP']
    for name in (username, groupname):
        require(re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', name), 'Invalid media account/group name')
    by_name = lookup(pwd.getpwnam, username)
    group_by_name = lookup(grp.getgrnam, groupname)
    require(by_name is None or by_name.pw_uid == uid,
            'Requested media username already has another UID; do not reassign it')
    require(group_by_name is None or group_by_name.gr_gid == gid,
            'Requested media group name already has another GID; do not reassign it')
    user = lookup(pwd.getpwuid, uid)
    group = lookup(grp.getgrgid, gid)
    if not dry:
        root_only()
    if group is None:
        run(['groupadd', '--gid', str(gid), groupname], dry=dry)
    else:
        print(f'GID {gid} already maps to {group.gr_name}; retaining it unchanged.')
    if user is None:
        run(['useradd', '--uid', str(uid), '--gid', str(gid), '--no-create-home',
             '--home-dir', '/nonexistent', '--shell', '/usr/sbin/nologin', username], dry=dry)
    else:
        print(f'UID {uid} already maps to {user.pw_name}; retaining it unchanged.')
    print('No existing memberships, passwords, sudo policy or media permissions changed.')


def probe_ext4_uuid(uuid):
    device = Path('/dev/disk/by-uuid') / uuid
    require(device.exists() and stat.S_ISBLK(device.stat().st_mode),
            f'Expected UUID block device unavailable: {uuid}')
    require(device.stat().st_rdev != Path('/').stat().st_dev, 'Cannot use the OS filesystem as data/media')
    info = dict(line.split('=', 1) for line in run(
        ['blkid', '-o', 'export', str(device)], capture=True).splitlines() if '=' in line)
    require(info.get('UUID') == uuid and info.get('TYPE') == 'ext4',
            'Expected an existing ext4 filesystem with the exact UUID')


def mount_storage(c, dry=False, confirmed=False):
    """Mount identified existing filesystems; never create or repair a filesystem."""
    require(c['MEDIA_UUID'] != c['DATA_UUID'], 'Media and secondary data need different UUIDs')
    todo = []
    for prefix in ('DATA', 'MEDIA'):
        uuid, path = c[prefix + '_UUID'], safe_path(c[prefix + '_ROOT'])
        require(uuid != 'SET_ME', 'Set discovered storage UUIDs first')
        probe_ext4_uuid(uuid)
        try:
            run(['findmnt', '--json', '--mountpoint', str(path)], capture=True)
        except subprocess.CalledProcessError as exc:
            if exc.returncode != 1:
                raise
        else:
            mounted(str(path), uuid, media=True)
            print(f'{prefix} already mounted and verified; no change.')
            continue
        require(not path.exists() or path.is_dir() and not any(path.iterdir()),
                f'Refusing to hide a nonempty mount-point directory: {path}')
        try:
            elsewhere = run(['findmnt', '--json', '--source', 'UUID=' + uuid], capture=True)
        except subprocess.CalledProcessError as exc:
            if exc.returncode != 1:
                raise
        else:
            raise SafetyError(f'{uuid} is mounted elsewhere; review before mounting: {elsewhere.strip()}')
        todo.append((uuid, path))
    for uuid, path in todo:
        print(f'Proposed mount: UUID={uuid} -> {path} (existing ext4, read/write)')
    if dry or not todo:
        return
    require(confirmed, 'Mounting requires --confirm-storage after reviewing both disk identities')
    root_only()
    # Finish discovery for both devices before creating mount points or mounting anything.
    for uuid, path in todo:
        mkdir_new(path, mode=0o755)
        run(['mount', '-t', 'ext4', '-o', 'rw', 'UUID=' + uuid, str(path)])
        mounted(str(path), uuid, media=True)
    print('Mounted existing filesystems. Persist verified mounts separately with configure-storage.')


def configure_network(c, dry=False):
    for key in ('PROXY_NETWORK', 'AI_NETWORK', 'TUNNEL_NETWORK'):
        try:
            run(['docker', 'network', 'inspect', c[key]], capture=True)
        except subprocess.CalledProcessError:
            run(['docker', 'network', 'create', c[key]], dry=dry)


def setup_fresh(c, dry=False):
    """Prepare new application state; recorded partial preparation can be resumed."""
    require(c['REPO_ROOT'] == str(ROOT), 'REPO_ROOT must match checkout')
    mounted(c['DATA_ROOT'], c['DATA_UUID'])
    check_media(c)
    runtime, appdata = Path(c['RUNTIME_ROOT']), Path(c['APPDATA_ROOT'])
    marker = runtime / 'install-state.json'
    identity = {key: c[key] for key in ('REPO_ROOT', 'APPDATA_ROOT', 'RUNTIME_ROOT',
                'SECRETS_ROOT', 'DATA_ROOT', 'DATA_UUID', 'MEDIA_ROOT', 'MEDIA_UUID', 'PUID', 'PGID')}

    def check_state():
        if marker.exists():
            require(not marker.is_symlink(), 'Setup marker must not be a symlink')
            state = json.loads(marker.read_text())
            require(state.get('schema') == 1 and state.get('identity') == identity,
                    'Setup identity changed; review paths, UUIDs and service IDs before continuing')
            require(state.get('status') in ('preparing', 'prepared'), 'Unrecognized setup state')
        else:
            for path in (appdata, Path(c['SECRETS_ROOT'])):
                require(not path.exists() or path.is_dir() and not any(path.iterdir()),
                        f'Fresh setup requires empty state directories; never delete or import existing state: {path}')

    check_state()
    run(['docker', 'info'], capture=True)
    run(['docker', 'compose', 'version'], capture=True)
    configure_users(c, dry=True)  # Detect identity collisions before writing installation state.

    def prepare_resources():
        if not dry:
            configure_users(c)
        generate_secrets(c, dry=dry)
        prepare(c, dry=dry)
        configure_network(c, dry=dry)

    if dry:
        prepare_resources()
    else:
        root_only()
        mkdir_new(runtime, mode=0o755)
        import fcntl
        with open(runtime / 'setup.lock', 'a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SafetyError('Another fresh setup is running')
            check_state()  # Recheck under the setup lock, including on the first installation.
            state = {'schema': 1, 'identity': identity, 'status': 'preparing'}
            atomic(marker, json.dumps(state, indent=2) + '\n', 0o600)
            prepare_resources()
            state.update(status='prepared', prepared_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
            atomic(marker, json.dumps(state, indent=2) + '\n', 0o600)
    print('Fresh setup prepared; no application containers, firewall or timers started.')
    print('Provision external credentials, then run make check-install before deploy-foundation.')


def _main(locks):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command')
    p.add_argument('--stack',choices=STACKS)
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--confirm-storage',action='store_true')
    p.add_argument('--snapshot')
    p.add_argument('--target')
    a=p.parse_args()
    if a.command=='init': initialize_config(a.dry_run);return
    if a.command=='preflight': preflight();return
    if a.command=='validate': validate();return
    c=config()
    lock=None
    if not a.dry_run and a.command not in ('guard','status','logs','healthcheck','media-validate','check-install'):
        runtime=Path(c['RUNTIME_ROOT'])
        if runtime.is_dir():
            import fcntl
            lock=locks.enter_context(open(runtime/'operation.lock','a'))
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise SafetyError('Another HomeLab operation is running')
    if a.command=='mount-storage': mount_storage(c,a.dry_run,a.confirm_storage)
    elif a.command=='configure-network': configure_network(c,a.dry_run)
    elif a.command=='configure-users': configure_users(c,a.dry_run)
    elif a.command=='setup': setup_fresh(c,a.dry_run)
    elif a.command=='check-install':
        stack=a.stack or 'infrastructure'
        gates(c,stack)
        compose(c,stack,'config','--quiet')
        print('Pre-start configuration checks passed for '+stack+'; no application containers started.')
    elif a.command=='prepare': prepare(c,a.dry_run)
    elif a.command=='secrets': generate_secrets(c,a.dry_run)
    elif a.command=='render':
        if a.dry_run: print('DRY RUN: render proxy, tunnel and dashboard configuration')
        else: render(c)
    elif a.command=='configure-storage': configure_storage(c,a.dry_run,a.confirm_storage)
    elif a.command=='media-validate': check_media(c)
    elif a.command=='guard': guard(c,a.dry_run)
    elif a.command=='deploy':
        require(a.stack,'Specify --stack');deploy(c,a.stack,a.dry_run)
    elif a.command in ('deploy-foundation','deploy-core','deploy-all'):
        selected = ('infrastructure','identity') if a.command=='deploy-foundation' else (CORE if a.command=='deploy-core' else tuple(n for n in STACKS if n not in ('ai','remote-access')))
        for n in selected:
            gates(c,n)
        for n in selected:
            deploy(c,n,a.dry_run)
    elif a.command=='healthcheck': health(c,a.stack)
    elif a.command in ('status','logs','stop'):
        require(a.stack or a.command=='status','Specify --stack')
        for n in ([a.stack] if a.stack else STACKS):
            compose(c,n,*({'status':['ps','--all'],'logs':['logs','--tail','100'],'stop':['stop']}[a.command]),dry=a.dry_run)
    elif a.command=='update':
        require(a.stack,'Specify --stack');gates(c,a.stack)
        compose(c,a.stack,'config','--quiet')
        backup(c,dry=a.dry_run)
        compose(c,a.stack,'pull',dry=a.dry_run)
        deploy(c,a.stack,a.dry_run)
    elif a.command.startswith('backup'):
        backup(c,a.dry_run,check=a.command=='backup-check',prune=a.command=='backup-retention',init=a.command=='backup-init')
    elif a.command=='restore': restore(c,a.snapshot,a.target,a.dry_run)
    else:
        raise SafetyError('Unknown command: '+a.command)


def main():
    with contextlib.ExitStack() as locks:
        _main(locks)


if __name__=='__main__':
    try:
        main()
    except (SafetyError,OSError,ValueError,KeyError,subprocess.CalledProcessError) as exc:
        sys.exit('ERROR: '+str(exc))
