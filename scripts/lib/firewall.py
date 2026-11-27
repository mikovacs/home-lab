"""Dedicated iptables chains; Docker owns its own chains. Never flush host rules."""
import argparse
import ipaddress
import re
from homelab import config, require, root_only, run

p=argparse.ArgumentParser()
p.add_argument('--dry-run',action='store_true')
p.add_argument('--apply',action='store_true')
a=p.parse_args()
c=config()
require(a.dry_run or a.apply,'Use --dry-run to review or --apply to configure firewall from a verified SSH session')
lan=str(ipaddress.IPv4Network(c['LAN_CIDR']))
require(re.fullmatch(r'[a-zA-Z0-9_.-]+',c['LAN_INTERFACE']) and c['LAN_INTERFACE']!='SET_ME','Configure LAN_INTERFACE from preflight')
admins=[str(ipaddress.ip_network(x.strip())) for x in c['ADMIN_CIDRS'].split(',')]
require(all(ipaddress.ip_network(x).prefixlen>0 for x in admins),'Administrative allowlist cannot contain a default route')
require(re.fullmatch(r'[a-zA-Z0-9_-]{1,15}',c['TERMINAL_BRIDGE']) and c['TERMINAL_BRIDGE']!=c['LAN_INTERFACE'],'Invalid dedicated terminal bridge name')
require(c['LAN_IP']!='192.0.2.10','Configure actual LAN addresses first')
if not a.dry_run:
    root_only()
    run(['iptables','-nL','DOCKER-USER'],capture=True)
for binary in ('iptables','ip6tables'):
    def rule(*args):
        run([binary,'-w',*args],dry=a.dry_run)
    for chain in ('HL-INPUT','HL-DOCKER'):
        if not a.dry_run:
            import subprocess
            try:run([binary,'-w','-N',chain],capture=True,stderr=subprocess.DEVNULL)
            except subprocess.CalledProcessError:pass
        else:rule('-N',chain)
        rule('-F',chain)
    rule('-A','HL-INPUT','-i','lo','-j','ACCEPT')
    rule('-A','HL-INPUT','-m','conntrack','--ctstate','ESTABLISHED,RELATED','-j','ACCEPT')
    for cidr in admins:
        if (':' in cidr)==(binary=='ip6tables'):
            rule('-A','HL-INPUT','-s',cidr,'-p','tcp','--dport','22','-j','ACCEPT')
    if binary=='iptables':
        rule('-A','HL-INPUT','-i',c['TERMINAL_BRIDGE'],'-p','tcp','--dport','22','-j','ACCEPT')
        rule('-A','HL-INPUT','-s',lan,'-p','udp','--sport','67','--dport','68','-j','ACCEPT')
    else:
        rule('-A','HL-INPUT','-s','fe80::/10','-p','udp','--sport','547','--dport','546','-j','ACCEPT')
    rule('-A','HL-INPUT','-p','icmp' if binary=='iptables' else 'ipv6-icmp','-j','ACCEPT')
    rule('-A','HL-INPUT','-p','udp','--dport','41641','-j','ACCEPT')
    rule('-A','HL-INPUT','-j','DROP')
    # Only inspect inbound NEW flows whose original destination is a published host port.
    # Internal container egress and bridge-to-bridge flows are returned untouched.
    if binary=='iptables':
        for proto,ports in [('tcp','53,80,443,8096,6881'),('udp','53,6881')]:
            rule('-A','HL-DOCKER','-s',lan,'-p',proto,'-m','multiport','--dports',ports,'-j','RETURN')
        for cidr in admins:
            if ':' not in cidr:
                rule('-A','HL-DOCKER','-s',cidr,'-p','tcp','-m','multiport','--dports','53,80,443','-j','RETURN')
                rule('-A','HL-DOCKER','-s',cidr,'-p','udp','--dport','53','-j','RETURN')
    rule('-A','HL-DOCKER','-j','DROP')
    def ensure_jump(parent, args):
        import subprocess
        if a.dry_run:
            rule('-I',parent,'1',*args);return
        try:run([binary,'-w','-C',parent,*args],capture=True,stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:rule('-I',parent,'1',*args)
    ensure_jump('INPUT',['-j','HL-INPUT'])
    # IPv6 Docker may have no DOCKER-USER chain when IPv6 bridge networking is disabled.
    if binary=='iptables':
        ensure_jump('DOCKER-USER',['-m','conntrack','--ctstate','NEW','--ctdir','ORIGINAL','--ctorigdst',c['LAN_IP'],'-j','HL-DOCKER'])
    else:
        # This repository publishes no IPv6 container ports; block unsolicited forwarding from physical interfaces.
        ensure_jump('FORWARD',['-i',c['LAN_INTERFACE'],'-m','conntrack','--ctstate','NEW','-j','HL-DOCKER'])
print('Host input restricted. Docker IPv4 published ports restricted by original destination. IPv6 container publishing is prohibited; see docs/security.md.')
