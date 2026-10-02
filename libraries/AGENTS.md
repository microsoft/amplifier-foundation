# Optional library boundaries

Read this directory's README and the affected library's README, provenance and
qualification notes before changing it. Own one library directory per worktree;
coordinate shared CI/release files through an integration owner.

- Keep packages independently installable. Do not add them to the base Foundation
  wheel, root imports or required dependencies merely because their sources live here.
- Keep application policy, host/client authority and native execution outside.
  Public mechanisms take explicit storage paths and scoped inputs from callers.
- Preserve native history and unknown effect receipts. Do not replay work to
  recover a projection or infer success from an interrupted transport.
- Build wheels and test imports in an owned installed-consumer environment.
  Explicitly select its Python executable for installers; never target an
  inherited active environment or running application's worker environment.
- Run the affected library's meaningful contract tests and any consumers whose
  public contracts changed. Record unverified integration/deployment boundaries.
