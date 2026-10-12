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
- Version ranges constrain the declared packages to supported major-version ranges, but do not pin exact versions or fully lock transitive dependencies.
- No committed lockfile was found among the conventional paths checked in this review. Reproducibility therefore depends on package resolution at installation time.
- At the time of the original review, no Dependabot configuration was found at the conventional `.github/dependabot.yml` path. PR #126 subsequently added weekly Python dependency updates, capped at five open PRs and labeled `dependencies`. This automates update PR creation; it is not itself a vulnerability scan and does not establish whether repository-level security settings or other monitoring services are enabled.

**Implication:** version ranges help avoid unreviewed major-version upgrades but do not by themselves provide reproducible installs or automated vulnerability remediation. Before a release, decide whether to add a lockfile or another repeatable dependency-resolution mechanism, and enable/verify an appropriate dependency-alert/update process.

### Secrets and local configuration

- Runtime credentials are read from environment variables rather than intentionally embedded in application source.
- `.gitignore` excludes `.env` and `.env.*`, with an explicit exception for `.env.example`.
- The checked-in `.env.example` contains placeholders and instructs users to generate distinct random agent and owner tokens.
- The runtime guide recommends loopback binding, warns against exposing the development API to untrusted networks, and cautions against committing credential-bearing RPC URLs.
- The application uses static bearer tokens supplied through the environment. This is a development control, not a production secret-management system; rotation, centralized secret storage, identity management, and deployment hardening are out of scope.

**Implication:** these conventions reduce accidental commits but cannot guarantee that secrets were never committed, copied into shell history, exposed through process inspection, or included in local backups. Repository history and secret-scanning configuration were not exhaustively audited as part of this file-level review.

## Follow-up checklist

- [ ] Verify repository-level secret scanning and push protection settings; do not infer their status from application files.
- [ ] Decide whether the project needs a committed lockfile or another reproducible dependency-resolution workflow before release.
- [ ] Establish a routine for reviewing dependency alerts and updating vulnerable packages.
- [ ] If a credential is ever exposed, revoke/rotate it and follow the relevant provider's incident guidance; removing it from the latest commit alone is insufficient.
- [ ] Revisit these findings before any production-readiness scope change.

## Scope boundary

This review does not add runtime dependencies or change application behavior. Transaction execution remains simulation-only, no private keys are loaded or transactions signed/submitted, and Base Sepolia integration remains read-only.
