# ModuleActivator install-target seam

`ModuleActivator` now accepts `install_python` and `install_constraints` from
its caller. With neither argument, it retains the existing `sys.executable`
target and emits no constraints option.

This is foundation's additive half of the per-`AMPLIFIER_HOME` environment
change. The two-homes DTU verification in xq95 remains blocked until the
app-cli follow-up release passes its home-specific target and pinned
constraints through when it constructs `ModuleActivator`. Running that
verification now would measure only a partial installation path and could
mislead.

Landing stage: this change is shipped as a draft PR; merging is the manager's
next stage.