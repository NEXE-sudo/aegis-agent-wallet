# Autonomous development workflow

The repository can use GitHub Actions and Copilot CLI to implement one roadmap item at a time, open a pull request, wait for checks, and request squash auto-merge. When an autonomous PR merges, its close event can start the next bounded task. Failed Python checks can trigger one repair on the same PR branch.

## Required repository secrets

In Settings → Secrets and variables → Actions, create these two repository secrets:

1. **COPILOT_GITHUB_TOKEN** — a fine-grained personal access token with the Copilot Requests permission, used only to authenticate Copilot CLI and bill usage to the associated Copilot account.
2. **AUTOMATION_TOKEN** — a fine-grained token restricted to this repository with Contents: read/write, Pull requests: read/write, Actions: read, and Metadata: read. It is used by trusted workflow steps to push the generated branch, open/update PRs, inspect CI, and request auto-merge. Do not pass this token into the Copilot process.

Use the minimum permissions available in the token UI. Never put either token in a repository file, issue, PR comment, or log. If your account or organisation supports Copilot CLI billed to GitHub Actions, you may later choose the documented GITHUB_TOKEN approach for Copilot authentication instead of COPILOT_GITHUB_TOKEN; this workflow currently expects the explicit secret.

## Repository settings

- Enable GitHub Actions for the repository.
- Ensure the Python checks workflow runs for pull requests and pushes to main.
- Configure branch protection/rulesets for main to require the relevant Python checks and any review requirements you want. Auto-merge honours these protections; it does not bypass them.
- Enable Allow auto-merge in repository settings.
- Review Copilot usage and set an appropriate spending/budget alert in GitHub billing settings. The workflow also limits each Copilot session to 100 AI credits, three continuation messages, 60 minutes per run, at most one same-branch CI repair, and at most three merged autonomous task PRs per rolling 24 hours. These are guardrails, not a guarantee of total account spend; billing settings remain the authoritative financial control.

## Starting and stopping the loop

1. Merge the setup PR after reviewing its workflow and instructions.
2. In Actions → Autonomous development, choose Run workflow to start the first task.
3. The workflow reads docs/chatbot-handoff.md, selects one unfinished roadmap task, checks that no other PR is open against main, runs Copilot, validates the change scope, and requires a handoff update in the same PR.
4. It opens one PR with a hidden marker, waits for PR checks, then requests squash auto-merge. Required reviews and branch protections still apply.
5. After an automatically generated PR merges, the merged-PR event can start the next task. If Python checks fail, one repair attempt runs on that same branch; a second failure leaves the PR open for human intervention.
6. To pause the loop, disable the Autonomous development workflow in Actions or remove/disable its trigger. To stop it permanently, remove the workflow and revoke the two tokens.

## Safety limits and limitations

- A task must stay within the existing testnet-only scope, with simulated execution as the default and owner approval/policy enforcement preserved.
- Routine autonomous tasks must not modify workflow/security configuration, dependency manifests/lockfiles, contracts, or deployment settings. Changes requiring those paths need a separately reviewed PR.
- One open PR at a time; one task per run; no more than three autonomous task PR merges in 24 hours.
- CI failures can be repaired once on the same branch. After that, the PR remains open and the loop stops for that task.
- Copilot is instructed to run Ruff and pytest, and GitHub Actions is the merge gate. A successful RPC read is not proof of contract security or transaction finality.
- A task may be skipped or the run may stop if the roadmap is ambiguous, Copilot produces no valid changes, required credentials are missing, usage limits are reached, checks fail repeatedly, or repository permissions/branch protections prevent the operation.
- The workflow consumes Copilot AI credits. Its per-session limit is a soft limit enforced by Copilot CLI; check your plan and billing settings before enabling recurring autonomous work.
- Never use mainnet keys, real funds, or production credentials in this prototype.

## Manual recovery

If the workflow stops, inspect the latest run under Actions → Autonomous development. The log should identify whether it stopped due to a missing secret, the one-PR guard, the daily cap, protected-path changes, failed checks, or a permissions/auto-merge setting. Resolve the underlying issue before manually rerunning it. Do not delete local untracked files such as .forge/, contracts/lib/, or aegis-agent-wallet-handoff.md as part of recovery.
