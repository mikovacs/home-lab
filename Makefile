.DEFAULT_GOAL := help
export PYTHONDONTWRITEBYTECODE := 1
SHELL := /bin/bash
CTL := python3 scripts/lib/homelab.py
ARGS ?=
STACK_ARG = $(if $(STACK),--stack $(STACK),)
.PHONY: installer help init mount-storage configure-users setup check-install deploy-foundation preflight validate test bootstrap configure-storage configure-network configure-firewall secrets prepare render install-systemd deploy-core deploy-all status healthcheck logs backup backup-init backup-check backup-retention restore update stop media-validate
installer:
	./install.sh $(ARGS)
help:
	@echo 'Install: sudo ./install.sh (release asset: sudo bash install.sh --repo OWNER/REPO)'
	@echo 'Checks: make validate; make test'
	@echo 'Operate: make status; make logs STACK=media; make backup; make update STACK=media'
	@echo 'Managed host: select HOMELAB_ENV_FILE=/etc/homelab/host.env for manual commands.'
	@echo 'Storage changes require ARGS=--confirm-storage; modifying commands support --dry-run.'
init mount-storage configure-users setup:
	$(CTL) $@ $(ARGS)
check-install:
	$(CTL) $@ $(STACK_ARG) $(ARGS)
preflight validate:
	$(CTL) $@ $(ARGS)
test:
	python3 -m unittest discover -s tests -v
bootstrap:
	./scripts/bootstrap.sh $(ARGS)
configure-firewall:
	python3 scripts/lib/firewall.py $(ARGS)
configure-storage configure-network secrets prepare render:
	$(CTL) $@ $(ARGS)
install-systemd:
	python3 scripts/lib/install_systemd.py $(ARGS)
deploy-foundation deploy-core deploy-all:
	$(CTL) $@ $(ARGS)
deploy-%:
	$(CTL) deploy --stack $* $(ARGS)
status healthcheck logs update stop:
	$(CTL) $@ $(STACK_ARG) $(ARGS)
backup backup-init backup-check backup-retention:
	$(CTL) $@ $(ARGS)
restore:
	$(CTL) restore $(if $(SNAPSHOT),--snapshot $(SNAPSHOT),) $(if $(TARGET),--target $(TARGET),) $(ARGS)
media-validate:
	$(CTL) media-validate $(ARGS)
