# Export-expiry prototype

This fixture accompanies the original [rule watcher](watch-rule.mjs). For a current installation, use the [CI coordinator guide](../README.md).

To reproduce the original fixture, copy [the demo files](demo/) into a dedicated Git repository, including the workflow under `.github/workflows/`. The workflow takes the PR commit and a pinned rule revision from the watcher. It reads the export configuration at that commit and publishes the legacy `openguildkit/export-link-expiry` status.

## Inspect a Windows restart

Run [the restart verifier](windows/verify-reboot.ps1) after a reboot in the legacy prototype environment. It uses the original watcher state and reports, the Windows System event log and the Jenkins timer build. The JSON output records separately whether GitHub published the rule before boot and after Windows logged the shutdown.

Publish the rule with a direct push that makes the rule commit the tip of the watched branch. The verifier matches the branch and commit SHA against GitHub repository activity and records the push timestamp and activity ID in its receipt. It reports an error when the matching activity is missing or ambiguous. The GitHub CLI credentials must be able to read repository activity.

```powershell
.\windows\verify-reboot.ps1 -ProbeConfig C:\pilot-runtime\reboot-probe.json
```

Put the configuration beside the legacy watcher's `state.json` and `reports/` directory. Provide `githubCli`, `previousBoot`, `armedAt`, `previousPolicyCommit`, `expectedVersion`, `publisherRun`, `output` and `targets`. Use ISO 8601 timestamps and record `repo`, `number`, `commit` and the previous `result.state` for each open PR. The legacy fixture uses a 30-minute threshold and the `poll-central-rule` Jenkins job on port 52278.

The verifier writes a local JSON receipt by default. Add `-PublishStatus` and supply `controlCommit` to write the optional GitHub witness status. Add `-RemoveProbeTask` to remove the one-off verifier task after success.
