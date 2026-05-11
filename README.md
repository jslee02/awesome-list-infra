# Awesome List Infra

Shared infrastructure for `jslee02/awesome-*` repositories.

This repository centralizes the parts that should not drift across individual
awesome lists:

- reusable GitHub Actions workflows
- workflow templates for list repositories
- shared maintenance utilities
- migration notes for keeping list repositories consistent

Each awesome list should still own its domain content:

- `data/*.yaml`
- `schema/entry.schema.json`
- `scripts/generate_readme.py`
- `scripts/validate_entries.py`
- repository-specific issue and submission automation

## Reusable Workflows

### Validate Data

Use this from an awesome-list repository:

```yaml
name: Validate Data

on:
  pull_request:
    paths:
      - "data/**"
      - "schema/**"
      - "scripts/**"

jobs:
  validate:
    uses: jslee02/awesome-list-infra/.github/workflows/validate-data.yml@main
```

The called workflow installs Python dependencies, runs the repository's local
validator, regenerates the README into a temporary file, and fails if the
committed README is stale.

### Check Links

Use this from an awesome-list repository:

```yaml
name: Check Links

on:
  schedule:
    - cron: "0 8 * * 1"
  push:
    branches: [main]
    paths: ["**.md"]
  pull_request:
    paths: ["**.md"]
  workflow_dispatch:

permissions:
  contents: write
  pull-requests: write
  issues: write

jobs:
  check-links:
    uses: jslee02/awesome-list-infra/.github/workflows/check-links.yml@main
```

Pull requests fail only on definitive broken links. Scheduled runs try to
replace redirects and dead links with suggested or archived URLs, then open a
maintenance PR for review.

## Migration Checklist

1. Rename the default branch to `main`.
2. Protect `main` by disabling force pushes and branch deletion.
3. Keep list-specific data, schema, and generator scripts in the target repo.
4. Replace copied workflow bodies with calls to the reusable workflows here.
5. Remove copied shared utilities such as `.github/scripts/fix-links.py`.
6. Run the local validator/generator and link workflow before migrating the next
   repository.

## Local Validation

In a consumer repository:

```sh
pip install pyyaml jsonschema
python3 scripts/validate_entries.py
python3 scripts/generate_readme.py -o /tmp/generated-readme.md
diff -u README.md /tmp/generated-readme.md
```

