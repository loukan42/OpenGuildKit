# Coordinate checks on open pull requests

The CI coordinator is an optional Node.js integration for OpenGuildKit. It reconciles open pull requests against active rule and profile revisions, runs the checks in Jenkins and GitHub Actions, and keeps historical reports separate from the verdict applicable to the current commit. The Python CLI handles project instructions and specialist reviews.

## Install

Use the [verified archive from a GitHub release](https://github.com/Sandbox-podcast/OpenGuildKit/blob/v0.16.1/docs/install.md) for a team deployment.

From the root of a trusted checkout, with Node.js 22+, npm and Git:

```bash
npm pack ./integrations/ci
npm install --global ./openguildkit-0.16.1.tgz
openguildkit-ci --help
```

The package is marked private and is not published to npm. Install it from the verified archive path, never by package name: a package named `openguildkit` on the public registry does not come from this project. The coordinator installs `openguildkit-ci` and `openguildkit-ci-agent`. GitHub operations also use GitHub CLI with access to the selected repositories. The coordinator state directory must support hard links. Put it on persistent storage and restrict access to the operators of the integration.

## Share the policy

Copy the [example policy repository files](https://github.com/Sandbox-podcast/OpenGuildKit/tree/v0.16.1/examples/export-expiry/policy) into a central Git repository. Both the Python project and the CI coordinator read `packs/export-link-expiry.json`. The CI catalog index provides ownership and selects the file. The approved CI profile, at `openguildkit.json` on a trusted target branch, selects the same path. The two project profiles serve different workflows and can be reviewed independently.

```json
{
  "schemaVersion": 1,
  "owner": "@platform",
  "ruleId": "export-link-expiry",
  "policyPath": "packs/export-link-expiry.json",
  "targetPath": "export.json"
}
```

Start in observation mode. Use `openguildkit-ci onboard` to create a profile proposal, and `openguildkit-ci github-approve-profile` to verify the maintainer's approval. Older numeric-version policy files and existing coordinator profiles remain supported.

The GitHub commands (`github`, `watch`, `gate --github` and `merge`) accept a profile with an `adoption` block only after verifying its approval. Set `profileMaintainer` for the project. The active profile must equal the proposal PR merged by that maintainer into `profileRef`, as returned by `github-approve-profile`, and that merge must be in the branch history. Without `profileMaintainer`, the project is refused. The local commands (`check`, `reconcile`, `gate` without `--github` and `brief`) read the `adoption` block as committed and rely on the access rules of the branch they read.

## Recheck one or more repositories

Provide the organization and repository IDs, corresponding local Git checkouts, approved branch references and the GitHub workflow that can run a recheck. The paths in the configuration are relative to the configuration file.

```json
{
  "policyRepo": {
    "id": "example/policies",
    "path": "./policies",
    "ref": "refs/heads/main"
  },
  "projects": [
    {
      "id": "example/portal",
      "path": "./portal",
      "profileRef": "refs/heads/main",
      "workflow": {
        "file": "openguildkit.yml",
        "ref": "pilot/recheck-base",
        "evidenceBranch": "openguildkit-evidence"
      }
    }
  ]
}
```

Use `openguildkit-ci github --config integration.json --state ./state --run` to inspect the resulting checks. For continuous operation, copy the [GitHub Actions recheck template](pilot/recheck-workflow.yml) to `.github/workflows/openguildkit.yml` on a trusted workflow branch. Set `pilot/recheck.json` with `policyRepository`, `policyRef` and `profileRef`, and put the archive's SHA-256 followed by its filename in `pilot/recheck-kit.sha256`. Host the pinned archive as a GitHub Release asset of that project, as expected by the template. Provision read-only policy access if the policy repository is private.

The coordinator and the template must compute the same work key. Before dispatching a recheck, `watch` reads both files from the workflow branch and stops with an explanation if they differ from the configuration: `policyRepository` and `policyRef` must match `policyRepo.id` and `policyRepo.ref`, `profileRef` must match the project's `profileRef`, and the pinned archive must be the coordinator's own version. The key names the last commit that changed the profile and the policy file, so the local clones need their full history. A shallow clone is refused with the command that completes it. If the template still computes another key, its run fails and lists each differing input.

Before scheduling the job, run the preflight as the Jenkins account with the same path you will pass to `watch`.

```sh
openguildkit-ci check-state --state ./state
```

On Windows, put the state directory on NTFS. An exFAT volume does not support hard links. If the preflight reports exFAT, select a persistent NTFS path and repeat the check. A failed preflight exits with code 2.

For a local Jenkins on Windows, [install-jenkins-watch.ps1](prototype/windows/install-jenkins-watch.ps1) registers a scheduled task that starts Jenkins and a job that runs the Jenkins launcher every minute. Pass `-JavaDirectory`, `-JenkinsWar`, `-WatchConfig integration.json` and `-KitManifest kit.json`, a file with the kit's `repository`, `version` and `sha256`. Add `-StateDirectory` for an NTFS path other than the default and `-Publish` to publish reports. The configuration and the manifest stay where they are. The installer writes UTF-8 files without byte order mark, as Jenkins requires on Windows PowerShell 5.1. The first start gives the generated administrator password to Jenkins through a file in its home directory, which the startup script deletes, so builds never inherit it. If the account cannot be created, Jenkins stops instead of running unsecured.

Schedule `openguildkit-ci watch --config integration.json --state ./state` on a trusted Jenkins agent with concurrent builds disabled. The command repeats the preflight before contacting GitHub. It holds `watch.lock` in the state directory while it runs, and a second watcher on the same directory exits with code 2. A lock left by a stopped process on the same host is replaced. The [Jenkins launcher](pilot/run-watch-jenkins.mjs) also checks the directory before downloading or installing the kit. Add `--publish` when the Jenkins identity has permission to write to the configured evidence branch and publish statuses. Set up that branch in each project.

A recheck that fails, stays queued past `watch.runTimeoutMinutes` (default 360), disappears or loses its artifact is dispatched again after `watch.retryDelayMinutes` (default 15, doubled after each attempt), up to `watch.maxAttempts` (default 3). Each cycle reports the attempt in `errors` and exits with code 2. After the last attempt, or when the two results disagree (`mismatch`), the work stays in error until an operator fixes the cause and runs `watch` with `--reset WORK`, or `--reset all` for every failed or mismatched work. The previous record is kept in `actions/history/` in the state directory. Every cycle prints its report, even when GitHub cannot be read again before publication.

A run that executes new checks refreshes the active references afterward. If they changed during evaluation, the report still links to the evidence just produced in `executedEvidence`. The `current` entries track the newly active references.

The workflow templates retain their pilot branch names and configuration filenames. Adjust them for the deployment. Keep checkout, validation and report publication on trusted revisions. The project keeps its normal merge review and approval settings.

### Run a single check in Jenkins

For a job that evaluates one target commit, use the [Jenkins check launcher](pilot/run-kit.mjs). The agent needs Node.js 22 or later, Git, GitHub CLI and `tar`. Run the launcher from the root of the target repository with `HEAD` on a trusted revision containing the approved `openguildkit.json` profile and the `openguildkit-kit.json` manifest. Fetch the target commit into that checkout and the policy commit into the policy checkout before running the job.

The manifest pins the release archive by repository, version and SHA-256. For the OpenGuildKit v0.16.1 release, put this in `openguildkit-kit.json`.

```json
{
  "repository": "Sandbox-podcast/OpenGuildKit",
  "version": "0.16.1",
  "sha256": "7057f7c47d54f5fe48252c8a57f09ac36ceb3d127f51d07a6eef76908b235e78"
}
```

When changing versions, use the SHA-256 for the Node archive from that release's `SHA256SUMS.txt`. Set `OPENGUILDKIT_SOURCE` to the absolute path of a trusted OpenGuildKit checkout, and set `POLICY_SHA` and `TARGET_SHA` to the full commit SHAs.

```sh
node "$OPENGUILDKIT_SOURCE/integrations/ci/pilot/run-kit.mjs" ../policies "$POLICY_SHA" "$TARGET_SHA" openguildkit-result
```

The launcher verifies the archive and reads the manifest and profile from the trusted `HEAD`. It evaluates the files at `TARGET_SHA`. Archive `openguildkit-result/AGENTS.md` and `openguildkit-result/evidence.json` when produced, including when a required rule fails.

## Results and errors

`check`, `gate` and the Jenkins helpers in `pilot/` share the exit codes of `openguildkit ci-check`: 0 when the commit conforms or the profile only observes, 1 when the rule fails with `--enforce` or a required profile, and 2 when the evaluation could not run. `reconcile`, `github` and `watch` exit with 2 when their report lists errors.

The coordinator reads `export.json`, the policy pack, the profile and the waiver registry like `openguildkit ci-check`. A UTF-8 byte order mark is accepted and a duplicate key is refused. Commit SHAs are full and lowercase, from SHA-1 or SHA-256 repositories, and must name a commit, not a tree or a tag. Without `--profile-commit`, `check` reads `--profile` from the working tree of `--target-repo`. Both test suites judge the shared cases of `integrations/ci/test/fixtures/parity.json` in the repository.

During a synchronization, an error limited to one PR, such as a head pushed during the read or a malformed PR, or to one project, such as an invalid profile, a failed fetch or an unverified approval, is listed in `errors` with the project and PR. The other projects and PRs are still evaluated. An error on the policy repository or in the waiver registry stops the whole synchronization: one invalid waiver could otherwise remove an exception without notice, so the coordinator fails closed. `gate` and `merge` stop on any error about the selected PR.

## Configuration reference

Paths are relative to the configuration file.

| Key | Commands | Meaning |
| --- | --- | --- |
| `policyRepo.id` | GitHub commands | `OWNER/REPO` of the policy repository. |
| `policyRepo.path` | All | Local clone of the policy repository. |
| `policyRepo.ref` | All | Full branch reference of the active policy, `refs/heads/…`. |
| `policyRepo.waiversPath` | All, optional | Path of the waiver registry in the policy repository. |
| `projects[].id` | All | `OWNER/REPO`, distinct for each project. |
| `projects[].path` | All | Local clone of the project with its full history. |
| `projects[].profileRef` | All | Trusted branch that holds `openguildkit.json`. |
| `projects[].pullRequests` | `reconcile`, `gate` | Local PRs to evaluate, `[{"number": 7, "ref": "refs/…", "sha": "…"}]`. The GitHub commands list open PRs themselves. |
| `projects[].prNumbers` | GitHub commands, optional | Limits the evaluation to these open PRs. |
| `projects[].profileMaintainer` | GitHub commands | GitHub login that merges profile proposals. Required when the profile has an `adoption` block. |
| `projects[].waiverApprovers` | All, optional | Approvers whose waivers apply to the project, for instance `["@security"]`. |
| `projects[].workflow.file`, `.ref` | `watch` | Recheck workflow file and the branch that holds it, `pilot/recheck.json` and `pilot/recheck-kit.sha256`. |
| `projects[].workflow.evidenceBranch` | `watch --publish` | Branch that receives the Markdown reports. |
| `watch.maxAttempts`, `watch.retryDelayMinutes`, `watch.runTimeoutMinutes` | `watch`, optional | Recheck limits, by default 3, 15 and 360. |

## Required checks and waivers

`openguildkit-ci gate` derives the merge condition from the current PR, profile and policy. `openguildkit-ci merge` can recheck a time-limited waiver immediately before submitting the exact PR SHA to GitHub. Use the [required check](pilot/required-workflow.yml) and [protected merge](pilot/broker-workflow.yml) templates to adapt branch enforcement to a pilot. Their GitHub Apps and credentials are provisioned separately by the repository owner.

The required check template evaluates a PR when it is opened or updated, and every open PR of the base branch four times an hour, so an expired waiver or a stricter rule changes the status within 15 minutes. A manual run without inputs reevaluates them at once. GitHub runs schedules from the default branch, so keep the template there too. A scheduled run adds a status only when the verdict changed. The template posts `openguildkit/required-pilot` with a dedicated status application (`OPENGUILDKIT_STATUS_APP_CLIENT_ID` variable and `OPENGUILDKIT_STATUS_APP_PRIVATE_KEY` secret, with the commit statuses write permission). In the branch ruleset, require that check with this application as its source. A status posted with `GITHUB_TOKEN`, including by another workflow with `statuses: write`, then cannot satisfy it.

Both templates read the `OPENGUILDKIT_PROFILE_MAINTAINER` variable. The protected merge template runs only for the logins listed in the `OPENGUILDKIT_MERGE_ACTORS` variable, a JSON array such as `["alice", "release-bot"]`, which works for organization repositories. Protect its `openguildkit-merge-pilot` environment with required reviewers if the request needs a second person. Before merging, it checks with `--app-id` that the merge application (`OPENGUILDKIT_APP_ID`, `OPENGUILDKIT_APP_CLIENT_ID`, `OPENGUILDKIT_APP_PRIVATE_KEY`) is the only bypass actor of ruleset `OPENGUILDKIT_RULESET_ID`. Give that application the administration read permission so it can read the ruleset's bypass list. Otherwise the check stops the merge.

See `openguildkit-ci --help` for the profile, agent brief, release discovery, recheck and merge subcommands. The [tests](https://github.com/Sandbox-podcast/OpenGuildKit/tree/v0.16.1/integrations/ci/test) exercise rule updates, stale evidence, recovery and scoped merge decisions.
