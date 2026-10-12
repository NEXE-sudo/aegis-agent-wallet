# Security Review: Dependencies and Secret Management

This is a focused repository-level review of dependency declarations and secret-handling conventions. It is not a vulnerability scan, a proof that the repository has never contained a secret, or an independent security audit. Findings describe files inspected on `main` during the review; re-check them when the repository configuration changes.

## Scope inspected

- `pyproject.toml` for direct runtime and development dependencies.
- `.gitignore` and `.env.example` for local-secret conventions.
- `docs/runtime-configuration.md` and the setup instructions in `README.md`.
- The standard paths `.github/dependabot.yml`, `requirements.txt`, `uv.lock`, and `poetry.lock` for dependency-update configuration and lockfiles.

## Findings

### Dependency declarations

- Runtime dependencies are declared in `pyproject.toml`: FastAPI, Uvicorn, Pydantic, and HTTPX.
- Development dependencies are pytest and Ruff.
- `pyproject.toml` declares supported version ranges, while the committed `uv.lock` records the resolved dependency versions for reproducible installation through the project's `uv` workflow.
- PR #141 added `uv.lock` and updated `.github/workflows/python-checks.yml` to install dependencies with `uv sync --locked --extra dev` and run tests and Ruff using `uv run --locked`. The `--locked` option makes the workflow fail if the lockfile needs to be updated.
- At the time of the original review, no Dependabot configuration was found at the conventional `.github/dependabot.yml` path. PR #126 subsequently added weekly Python dependency updates, capped at five open PRs and labeled `dependencies`. This automates update PR creation; it is not itself a vulnerability scan and does not establish whether repository-level security settings or other monitoring services are enabled.

**Implication:** the committed lockfile and locked CI installation improve dependency reproducibility, while pip-audit checks the installed environment for known vulnerabilities at the time of each run. These controls do not replace reviewing dependency-update PRs, repository dependency alerts, secret scanning, or push protection. Dependency updates should keep uv.lock synchronized with pyproject.toml and should pass the locked CI workflow.

### Secrets and local configuration

- Runtime credentials are read from environment variables rather than intentionally embedded in application source.
- `.gitignore` excludes `.env` and `.env.*`, with an explicit exception for `.env.example`.
- The checked-in `.env.example` contains placeholders and instructs users to generate distinct random agent and owner tokens.
- The runtime guide recommends loopback binding, warns against exposing the development API to untrusted networks, and cautions against committing credential-bearing RPC URLs.
- The application uses static bearer tokens supplied through the environment. This is a development control, not a production secret-management system; rotation, centralized secret storage, identity management, and deployment hardening are out of scope.

**Implication:** these conventions reduce accidental commits but cannot guarantee that secrets were never committed, copied into shell history, exposed through process inspection, or included in local backups. On 2026-10-12, the repository owner checked GitHub's repository security settings and confirmed that secret scanning and push protection are enabled. This records the settings-page confirmation; it is not an exhaustive audit of repository history and does not prove that no secret was ever committed.

## Follow-up checklist

- [x] Verify repository-level secret scanning and push protection settings; the repository owner confirmed both are enabled in GitHub settings on 2026-10-12.
- [x] Commit `uv.lock` and enforce locked dependency installation in CI (PR #141).
- [x] Add a CI dependency-audit step for the resolved Python environment using `pip-audit`; continue reviewing Dependabot PRs and any repository dependency alerts.
- [ ] If a credential is ever exposed, revoke/rotate it and follow the relevant provider's incident guidance; removing it from the latest commit alone is insufficient.
- [ ] Revisit these findings before any production-readiness scope change.

## Scope boundary

This review does not add runtime dependencies or change application behavior. Transaction execution remains simulation-only, no private keys are loaded or transactions signed/submitted, and Base Sepolia integration remains read-only.
