import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

const watcher = fileURLToPath(new URL('../prototype/watch-rule.mjs', import.meta.url));
const repositories = ['team/portal', 'team/service'];
const policyCommit = 'a'.repeat(40);
const targets = repositories.map((repo, index) => ({
  repo, number: 1, commit: String(index + 1).repeat(40),
}));
const excluded = { repo: repositories[0], number: 2, commit: 'b'.repeat(40) };
const rule = { id: 'export-link-expiry', version: 7, maxMinutes: 30 };
const config = {
  policyRepo: repositories[0], policyRef: 'main', repositories,
  pullRequests: Object.fromEntries(repositories.map(repo => [repo, [1]])),
};

function runWatcher(t, settings, { resume = false } = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'openguildkit-prototype-watch-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  writeFileSync(join(dir, 'watch.json'), JSON.stringify(settings));
  if (resume) writeFileSync(join(dir, 'state.json'), JSON.stringify({ policyCommit, targets: [excluded, ...targets] }));

  const calls = {};
  const stub = (args, result) => { calls[args.join(' ')] = result; };
  const file = value => JSON.stringify({ content: Buffer.from(JSON.stringify(value)).toString('base64') });
  const pull = ({ number, commit }, repo) => ({ number, head: { sha: commit, repo: { full_name: repo } } });
  stub(['api', '--method', 'GET', `repos/${config.policyRepo}/commits`, '-f', 'sha=main', '-f',
    'path=policy/export-link.json', '-F', 'per_page=1', '--jq', '.[0].sha'], policyCommit);
  stub(['api', `repos/${config.policyRepo}/contents/policy/export-link.json?ref=${policyCommit}`], file(rule));
  for (const [index, target] of targets.entries()) {
    const { repo, commit } = target;
    const state = index === 0 ? 'failure' : 'success';
    const runId = String(index + 1);
    const url = `https://github.com/${repo}/actions/runs/${runId}`;
    if (!resume) stub(['api', '--paginate', '--slurp', `repos/${repo}/pulls?state=open&per_page=100`],
      JSON.stringify([[pull(excluded, 'team/fork')], [pull(target, repo)]]));
    stub(['api', `repos/${repo}/contents/export.json?ref=${commit}`], file({ downloadLinkMinutes: index === 0 ? 45 : 30 }));
    stub(['workflow', 'run', 'openguildkit.yml', '--repo', repo, '--ref', 'main', '--json'], url);
    stub(['api', `repos/${repo}/statuses/${commit}`, '-f', 'context=openguildkit/jenkins',
      '-f', `state=${state}`, '-f', `description=v7 | règle ${policyCommit}`], '');
    stub(['run', 'view', runId, '--repo', repo, '--json', 'status,conclusion'],
      JSON.stringify({ status: 'completed', conclusion: state }));
    stub(['api', `repos/${repo}/commits/${commit}/statuses?per_page=100`], JSON.stringify([{
      context: 'openguildkit/export-link-expiry', target_url: url, description: policyCommit, state,
    }]));
  }

  const preload = `
    import childProcess from 'node:child_process';
    import { syncBuiltinESMExports } from 'node:module';
    const calls = ${JSON.stringify(calls)};
    childProcess.execFileSync = (command, args) => {
      const key = args.join(' ');
      if (command !== 'gh' || !Object.hasOwn(calls, key)) throw new Error('Unexpected call: ' + key);
      return calls[key];
    };
    syncBuiltinESMExports();
  `;
  return spawnSync(process.execPath, ['--import', `data:text/javascript;base64,${Buffer.from(preload).toString('base64')}`,
    watcher, 'watch.json', 'state.json'], { cwd: dir, encoding: 'utf8' });
}

for (const resume of [false, true]) {
  test(`the prototype targets the two configured PRs ${resume ? 'when retrying' : 'on a new policy'}`, t => {
    const run = runWatcher(t, config, { resume });
    assert.equal(run.status, 1, run.stderr);
    assert.equal(run.stderr, '');
    const report = JSON.parse(run.stdout);
    assert.equal(report.policyCommit, policyCommit);
    assert.deepEqual(report.targets.map(({ repo, number, commit }) => ({ repo, number, commit })), targets);
    assert.deepEqual(report.targets.map(({ result }) => result.state), ['failure', 'success']);
  });
}

test('the prototype rejects a scope that omits a watched repository', t => {
  const run = runWatcher(t, { ...config, pullRequests: { [repositories[0]]: [1] } });
  assert.equal(run.status, 1);
  assert.match(run.stderr, /pullRequests doit contenir/);
  assert.doesNotMatch(run.stderr, /Unexpected call/);
});
