# Migration Plan

The goal is to keep every `jslee02/awesome-*` repository consistent without
copying workflow and maintenance logic between repositories.

## Current Baseline

- All 18 `awesome-*` repositories use `main` as the default branch.
- Public `awesome-*` repositories have branch protection on `main`.
- Private `awesome-*` repositories cannot use branch protection on the current
  GitHub plan unless they become public or the account upgrades to GitHub Pro.
- `awesome-collision-detection`, `awesome-entity-component-system`, and
  `awesome-robotics-libraries` use reusable validation and link-check workflows.
- Six older public Markdown-only repositories use the shared link-check workflow
  and common `.lychee.toml`, but still need data-driven README conversion.

## Phases

1. Keep `repositories.yaml` current as the source of truth for repo state.
2. Migrate public repositories that already have generated README/data
   infrastructure to the reusable workflows.
3. Keep link checking enabled on older Markdown-only repositories while they are
   waiting for conversion.
4. For active lists, introduce the data/schema/generator layout used by
   `awesome-robotics-libraries`.
5. Promote common generator and validator behavior into this Python package only
   after at least two more repositories share the same shape.

## Migration Criteria

A repository can use the reusable workflows immediately when it has:

- `.lychee.toml`
- `data/*.yaml`
- `schema/entry.schema.json`
- `scripts/validate_entries.py`
- `scripts/generate_readme.py`

Repositories that do not have those files should first be converted from manual
Markdown to data-driven generation.
