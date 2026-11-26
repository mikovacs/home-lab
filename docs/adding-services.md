# Adding a service

Select a maintained official/reputable image, review license/release/advisories and verify an explicit tag/architecture. Add it to the appropriate stack or create one module, with persistent appdata outside the checkout, long bind mounts disabling host-path creation, resource limits, bounded logs, graceful stop and a supported health check. Run non-root/drop capabilities/read-only root where the image supports it. Do not copy hardening settings that break initialization.

Decide access class first. Public web requires an explicit renderer allowlist entry, authentication policy and remote acceptance test. Private administration requires local entrypoint, source-IP restriction and independent basic auth. Databases/internal APIs have no host ports. Attach only necessary networks; grant full Docker socket access only to explicitly trusted administration services and document its scope in the security guide. Add backup quiescence/dump rules if the app needs special consistency.

Declare new appdata directories/UIDs in APPS, required files in STACK_SECRETS, new stack in STACKS, and keep CORE/AI opt-in behavior deliberate. Update the [image sources](images.md), storage guards and systemd dependencies, README service list and configuration reference. Add meaningful safety tests for changed controls. Run make validate, make test and CI linters. Install/test on hardware only after administrator authorization.

Fresh setup is the default lifecycle. New modules must initialize their own empty state and must not require imported application databases. Keep setup free of application startup or data deletion, preserve its installation identity checks, and document first-run local accounts separately from host service identities.
