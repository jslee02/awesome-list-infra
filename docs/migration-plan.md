# Migration Plan

The goal is to keep every `jslee02/awesome-*` repository consistent without
copying workflow and maintenance logic between repositories.

## Current Baseline

- All 18 `awesome-*` repositories use `main` as the default branch.
- Public `awesome-*` repositories have branch protection on `main`.
- Private `awesome-*` repositories cannot use branch protection on the current
  GitHub plan unless they become public or the account upgrades to GitHub Pro.
- `awesome-collision-detection`, `awesome-entity-component-system`, and
  `awesome-robotics-libraries` use reusable validation and link-check workflows
  with their existing schema-driven data layouts.
- Six older public repositories now use the generic generated README layout from
  this package: `awesome-cpp-python-binding-generator`, `awesome-gpgpu`,
  `awesome-graphics-libraries`, `awesome-multibody-dynamics-simulation`,
  `awesome-projects`, and `awesome-robotics`.
- The remaining consistency work is in the private repositories. They can use
  the same generated README, issue template, PR template, Dependabot, and
  workflow templates, but branch protection remains blocked by the current
  GitHub plan.

## Phases

1. Keep `repositories.yaml` current as the source of truth for repo state.
2. Keep the public repositories on either the domain-specific reusable workflows
   or the generic generated README workflow.
3. Convert private repositories to the generic generated README layout.
4. Promote new shared behavior into this package when at least two repositories
   need the same behavior.

## Migration Criteria

A repository can use the reusable workflows immediately when it has:

- `.lychee.toml`
- `data/*.yaml`
- `schema/entry.schema.json`
- `scripts/validate_entries.py`
- `scripts/generate_readme.py`

Repositories that do not have those files should either use the generic
generated README template from this package or be converted to a domain-specific
data/schema layout when they need stricter validation.
