# Scoped admission of prepared sources

Foundation exposes a resolver mechanism; the application owns trusted manifests,
release approval, selected code roots, file digests, and source-selection intent.

```python
from amplifier_foundation.sources import (
    SOURCE_RESOLUTION_POLICY_VERSION, SourceResolutionDenied,
    source_resolution_policy,
)
from amplifier_foundation.paths.resolution import ResolvedSource

def admit(uri: str, *, base_path):
    # Look up the requested URI or admitted effective local path in a verified
    # immutable host map. Verify only needed bindings and scoped file digests.
    # Never fetch, install, refresh, or create a binding here.
    entry = reviewed_binding(uri, base_path=base_path)
    if entry is None:
        raise SourceResolutionDenied("Source is outside the reviewed closure")
    return ResolvedSource(entry.active_path, entry.source_root)

assert SOURCE_RESOLUTION_POLICY_VERSION == 1
with source_resolution_policy(admit):
    # Registry, Bundle.prepare, activation, lazy activation and joined cleanup.
    ...
```

The synchronous callback runs before URI parsing or any scheme handler.
None and malformed, missing, relative or canonically escaping paths deny without
fallback. Resource files are valid active paths; source_root must be a directory.
The same callback may nest; replacing or disabling an active callback is refused.
Asyncio tasks inherit their context. Every worker/probe process and independently
started thread needs its own trusted scope. Pair this with the existing scoped
dependency-installation prohibition and prepare with install_deps=False; source
admission does not independently prohibit installers or registry persistence.

Cached Registry root paths are checked for agreement with the current binding,
including pending loads. Prepared module paths are checked on sync/async resolver
reuse, module-source path return, and ID-only get_module_source. A read-only
prepared_sources() tuple includes (module_id, None, path) ID paths and
(module_id, effective_source_hint, path) provider variants. It is a census,
not an import attestation. Nested/lazy modules enter it after activation.

Use a fresh registry/preparation for a newly qualified manifest. A cached composed
bundle contains include objects: root equality alone does not attest that graph.
Hosts must qualify all effective include/module paths and measured Python
package/module origins against their approved manifest, including already
importable or sys.modules-cached packages. Preserve requested @main URIs and
exception approvals separately from the effective local prepared paths.

## Inspected mechanisms and acceptance boundary

- SimpleSourceResolver: file, Git (shared and legacy), HTTP, ZIP and custom handlers
  are behind the common admission gate; no handler is entered when admission runs.
- BundleRegistry creates its own resolver; root/includes inherit the context.
  Root cached/pending reuse compares remembered ResolvedSource values.
- ModuleActivator creates its own resolver; first and repeated activation both
  resolve through admission. Direct bundle-package activation admits its path
  before package inspection/installation; the dependency policy remains separate.
- BundleModuleResolver returns prepared paths without handler resolution:
  those paths and requested hints are checked for agreement. Lazy activation
  continues through ModuleActivator.
- subprocess_runner restores serialized sys.path in a new process; grpc_adapter
  has a direct path import entrypoint. These are not SimpleSourceResolver calls.
  The launching host must install policy in new processes and attest imported
  code; this API is not an arbitrary-Python import sandbox.
- SharedSourceStore.verify is read-only identity/clean-check inspection. Direct
  shared-store resolution/update calls remain mutable staging mechanisms and do
  not inherit this gate. Immutable hosts must not call them to bypass admission.

Installed-consumer tests exercise actual Registry, Activator and Bundle.prepare
with all handler resolution forbidden; no source fetch or cache writes occur.
Negative cases cover unbound schemes, invalid results, path/symlink escape and
cached/prepared binding disagreement. Native launch propagation, real configured
provider mounting, loaded-origin digests, and release/worker adoption require
separate integrated qualification.
