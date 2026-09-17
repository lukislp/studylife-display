# Contributing to StudyLife Display

Thanks for taking the time. StudyLife is a single-maintainer project, so the process is deliberately
small - but it is the same for every change, including the maintainer's own.

## How changes get in

1. Open an issue first for anything bigger than a typo or an obvious bug fix, so the direction can
   be agreed before you spend time on it. Use the templates under `.github/ISSUE_TEMPLATE/`.
2. Fork the repository (or branch, if you have write access) and make your change on a branch.
3. Open a pull request against `main`. The pull-request template asks for what changed and why.
4. `main` is protected: a PR merges only after the `lint` and `test` jobs of
   [`.github/workflows/ci.yml`](.github/workflows/ci.yml) are green and the branch is up to date
   with `main` (enable auto-merge and it lands on its own once that is the case). Nobody pushes
   to `main` directly, not even the maintainer.

## What a pull request needs

- **Conventional Commits.** The version and the changelog are generated from the commit messages
  (`feat:` = minor release, `fix:` = patch release, `build:`/`ci:`/`docs:`/`test:` = no release).
  Squash-merge keeps the PR title as the commit message, so give the PR a Conventional Commit
  title.
- **Tests for new functionality.** New features and bug fixes come with tests under `tests/`.
  The model and the renderer are pure functions and are tested directly; `main.py` is tested
  against a mocked API with `respx`.
- **Wire format.** Every StudyLife JSON field the code reads is listed in `model.USED_FIELDS`
  and checked by `tests/test_wire_fields.py` against the verified field names. Do not add a
  field name you have not verified against the server: StudyLife silently ignores unknown
  fields, so a typo renders as zero forever instead of failing.
- **Goldens.** A deliberate layout change regenerates `tests/golden/*.png` with
  `uv run pytest --update-goldens` and `docs/preview.png` with
  `uv run studylife-display preview --sample --out docs/preview.png`; commit both.
- **Formatting, lint, types.** `ruff check`, `ruff format --check` and `mypy` (strict) run as
  required checks; run them before pushing.
- **Read-only.** The display only ever reads. A change that needs a write scope on the API key
  belongs in a different add-on.

## Running things locally

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv run studylife-display preview --sample --out frame.png
```

Nothing here needs the Raspberry Pi or the panel; the `pi` extra is only installed by
`deploy/install.sh`.

## Security issues

Please do not open a public issue for a vulnerability - use the private reporting path described
in [SECURITY.md](SECURITY.md).
