import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { reconcileGithub } from '../lib/github.mjs';
import { mergeGithub } from '../lib/merge.mjs';
import { reconcile } from '../lib/reconcile.mjs';

function git(cwd, ...args) {
  return execFileSync('git', ['-c', 'user.name=OpenGuildKit Test',
    '-c', 'user.email=openguildkit@example.invalid', '-c', 'commit.gpgsign=false',
    '-C', cwd, ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
}

function commitJson(repo, file, value) {
  writeFileSync(join(repo, file), JSON.stringify(value));
  git(repo, 'add', '--', file);
  git(repo, 'commit', '-qm', `Update ${file}`);
  return git(repo, 'rev-parse', 'HEAD');
}

// A GitHub repository with an approved profile, served to the coordinator through local Git and a fake API.
function githubFixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'openguildkit-sync-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const origin = join(root, 'origin');
  mkdirSync(join(origin, 'policy'), { recursive: true });
  mkdirSync(join(origin, '.openguildkit'));
  git(origin, 'init', '-q', '-b', 'main');
  const policyPath = 'policy/export-link.json';
  const policyCommit = commitJson(origin, policyPath, { id: 'export-link-expiry', version: 1, maxMinutes: 30 });
  const proposal = {
    schemaVersion: 1, owner: '@lead', policyPath, targetPath: 'export.json', ruleId: 'export-link-expiry',
    adoption: { state: 'proposed', mode: 'required', inventoryCommit: policyCommit, catalogCommit: policyCommit,
      answers: { project: 'team/repo' } },
  };
  const mergeCommit = commitJson(origin, '.openguildkit/proposal.json', proposal);
  const proposalBytes = readFileSync(join(origin, '.openguildkit/proposal.json'));
  const approved = {
    ...proposal,
    adoption: {
      ...proposal.adoption, state: 'approved', approver: '@lead',
      approval: {
        provider: 'github', repository: 'team/repo', pullRequest: 3, base: 'main', actor: 'lead',
        proposalCommit: mergeCommit, mergeCommit,
        proposalSha256: createHash('sha256').update(proposalBytes).digest('hex'),
      },
    },
  };
  commitJson(origin, 'openguildkit.json', approved);
  const pulls = [];
  const addPull = (number, minutes) => {
    git(origin, 'switch', '-q', '-c', `pr-${number}`, 'main');
    const head = commitJson(origin, 'export.json', { downloadLinkMinutes: minutes });
    git(origin, 'update-ref', `refs/pull/${number}/head`, head);
    git(origin, 'switch', '-q', 'main');
    pulls.push({ number, state: 'open', head: { sha: head }, base: { ref: 'main', repo: { full_name: 'team/repo' } } });
    return head;
  };
  execFileSync('git', ['clone', '-q', origin, join(root, 'clone')]);
  const api = {
    mergedBy: 'lead',
    responses: new Map(),
  };
  const request = (...args) => {
    const endpoint = args.at(-1);
    if (api.responses.has(endpoint)) return JSON.stringify(api.responses.get(endpoint)());
    if (endpoint === 'repos/team/repo/pulls?state=open&base=main&per_page=100') return JSON.stringify([pulls]);
    if (endpoint === 'repos/team/repo/pulls/3') {
      return JSON.stringify({
        merged: true, merged_by: { login: api.mergedBy }, merge_commit_sha: mergeCommit,
        base: { ref: 'main', repo: { full_name: 'team/repo' } }, head: { sha: mergeCommit },
      });
    }
    if (endpoint === 'repos/team/repo/pulls/3/files?per_page=100') {
      return JSON.stringify([[{ filename: '.openguildkit/proposal.json', status: 'added', sha: 'b'.repeat(40) }]]);
    }
    if (endpoint === `repos/team/repo/contents/.openguildkit/proposal.json?ref=${mergeCommit}`) {
      return JSON.stringify({ type: 'file', encoding: 'base64', sha: 'b'.repeat(40), content: proposalBytes.toString('base64') });
    }
    throw new Error(`Unexpected GitHub request: ${args.join(' ')}`);
  };
  const fetchRefs = (repo, _id, refspecs) => git(repo, 'fetch', '--quiet', '--atomic', '--no-tags', origin, ...refspecs);
  const configPath = join(root, 'integration.json');
  const configure = (project, { others = [], policy = {} } = {}) => writeFileSync(configPath, JSON.stringify({
    policyRepo: { id: 'team/policies', path: 'clone', ref: 'refs/heads/main', ...policy },
    projects: [{ id: 'team/repo', path: 'clone', profileRef: 'refs/heads/main', ...project }, ...others],
  }));
  const sync = (options = {}) => reconcileGithub({
    configPath, stateDir: join(root, 'state'), kitVersion: '0.16.1', ...options,
  }, { request, fetchRefs });
  return { root, origin, api, addPull, pulls, configure, sync, request, fetchRefs, configPath, approved };
}

function refused(result, pattern) {
  assert.deepEqual(result.current, []);
  assert.equal(result.errors.length, 1);
  assert.equal(result.errors[0].project, 'team/repo');
  assert.match(result.errors[0].message, pattern);
}

test('the Jenkins coordinator evaluates only the configured open PR numbers', t => {
  const { addPull, configure, sync } = githubFixture(t);
  const target = addPull(7, 45);
  addPull(8, 20);
  configure({ profileMaintainer: 'lead', prNumbers: [7] });

  const result = sync({ run: true });
  assert.deepEqual(result.errors, []);
  assert.deepEqual(result.current.map(row => [row.pullRequest, row.commit, row.status]), [[7, target, 'failure']]);
});

test('the GitHub flows accept an adopted profile only after the maintainer approval is verified', t => {
  const { origin, api, addPull, configure, sync, approved } = githubFixture(t);
  addPull(7, 45);
  configure({});
  refused(sync(), /Set profileMaintainer for team\/repo/);

  configure({ profileMaintainer: 'lead' });
  const [current] = sync().current;
  assert.equal(current.mode, 'required');
  assert.equal(current.status, 'pending');

  api.mergedBy = 'someone-else';
  refused(sync(), /fusionnée par le mainteneur désigné/);
  api.mergedBy = 'lead';

  commitJson(origin, 'openguildkit.json', { ...approved, owner: '@intruder',
    adoption: { ...approved.adoption, approver: '@intruder' } });
  refused(sync(), /diffère de la proposition approuvée/);
});

test('an error limited to one project or one PR is reported without stopping the others', t => {
  const { root, origin, api, addPull, pulls, configure, sync } = githubFixture(t);
  const head = addPull(7, 45);
  const moved = addPull(8, 20);
  pulls.push({ number: 9, state: 'open', head: { sha: 'not-a-commit' }, base: { ref: 'main', repo: { full_name: 'team/repo' } } });
  api.responses.set('repos/team/repo/pulls?state=open&base=main&per_page=100',
    () => [pulls.map(pr => pr.number === 8 ? { ...pr, head: { sha: '0'.repeat(40) } } : pr)]);
  git(origin, 'switch', '-q', '-c', 'broken');
  writeFileSync(join(origin, 'openguildkit.json'),
    '{"schemaVersion": 1, "owner": "@x", "owner": "@y", "policyPath": "policy/export-link.json", "targetPath": "export.json"}');
  git(origin, 'commit', '-qam', 'Duplicate a key');
  git(origin, 'switch', '-q', 'main');
  api.responses.set('repos/team/broken/pulls?state=open&base=broken&per_page=100',
    () => [[{ number: 7, state: 'open', head: { sha: head }, base: { ref: 'broken', repo: { full_name: 'team/broken' } } }]]);
  configure({ profileMaintainer: 'lead' }, { others: [{ id: 'team/broken', path: 'clone', profileRef: 'refs/heads/broken' }] });

  const result = sync({ run: true });
  assert.deepEqual(result.current.map(row => [row.project, row.pullRequest, row.status]), [['team/repo', 7, 'failure']]);
  assert.deepEqual(result.errors.map(error => [error.project, error.pullRequest]),
    [['team/repo', 9], ['team/repo', 8], ['team/broken', undefined]]);
  assert.match(result.errors[1].message, /a évolué pendant le chargement/);
  assert.match(result.errors[2].message, /Duplicate JSON key: owner/);
  assert.throws(() => sync({ selection: { project: 'team/repo', pullRequest: 8, commit: moved, mode: 'required' } }),
    /a évolué pendant le chargement/);

  const local = join(root, 'local.json');
  writeFileSync(local, JSON.stringify({
    policyRepo: { path: 'origin', ref: 'refs/heads/main' },
    projects: [
      { id: 'local/broken', path: 'origin', profileRef: 'refs/heads/broken', pullRequests: [{ number: 1, ref: 'refs/heads/pr-7' }] },
      { id: 'local/plain', path: 'origin', profileRef: 'refs/heads/main', pullRequests: [
        { number: 1, ref: 'refs/heads/pr-7' }, { number: 2, ref: 'refs/heads/missing' }] },
    ],
  }));
  const plain = reconcile({ configPath: local, stateDir: join(root, 'local-state'), kitVersion: '0.16.1' });
  assert.deepEqual(plain.current.map(row => [row.project, row.pullRequest]), [['local/plain', 1]]);
  assert.deepEqual(plain.errors.map(error => [error.project, error.pullRequest]), [['local/broken', undefined], ['local/plain', 2]]);

  writeFileSync(join(origin, 'policy/waivers.json'), JSON.stringify({ schemaVersion: 1, waivers: [{ id: 'incomplete' }] }));
  git(origin, 'add', 'policy/waivers.json');
  git(origin, 'commit', '-qm', 'Add an invalid waiver');
  configure({ profileMaintainer: 'lead' }, { policy: { waiversPath: 'policy/waivers.json' } });
  assert.throws(() => sync(), /Dérogation invalide : incomplete/);
});

test('only the PRs into the profile branch are evaluated, and their statuses are in English', t => {
  const { root, addPull, pulls, configure, request, fetchRefs, configPath } = githubFixture(t);
  const head = addPull(7, 45);
  addPull(8, 20);
  pulls[1] = { ...pulls[1], base: { ...pulls[1].base, ref: 'release/1' } };
  configure({ profileMaintainer: 'lead' });
  const endpoints = [];
  const posted = [];
  const spy = (...args) => {
    endpoints.push(args.at(-1));
    if (args[1] === `repos/team/repo/statuses/${head}`) {
      posted.push(args.find(arg => arg.startsWith('description=')));
      return '{}';
    }
    if (args.at(-1) === 'repos/team/repo/pulls/7') return JSON.stringify({ state: 'open', head: { sha: head } });
    if (args.at(-1) === `repos/team/repo/commits/${head}/status?per_page=100`) return JSON.stringify([{ statuses: [] }]);
    return request(...args);
  };
  const result = reconcileGithub({ configPath, stateDir: join(root, 'state'), kitVersion: '0.16.1', run: true, publish: true },
    { request: spy, fetchRefs });

  assert.ok(endpoints.includes('repos/team/repo/pulls?state=open&base=main&per_page=100'));
  assert.deepEqual(result.current.map(row => [row.pullRequest, row.status]), [[7, 'failure']]);
  assert.deepEqual(result.errors, []);
  assert.equal(posted.length, 1);
  assert.match(posted[0], /^description=Deviation \(obs\.\) \| rule [a-f0-9]{12} \| profile [a-f0-9]{12} \| [a-f0-9]{12}$/);
});

test('the merge rechecks that the merge application is the only bypass actor', () => {
  const head = '1'.repeat(40);
  const api = endpoint => {
    if (endpoint === 'repos/team/project/pulls/3') {
      return { state: 'open', draft: false, head: { sha: head },
        base: { ref: 'main', sha: '2'.repeat(40), repo: { full_name: 'team/project' } } };
    }
    if (endpoint.includes('/rules/branches/')) {
      return [[{ ruleset_id: 40, ruleset_source_type: 'Repository', ruleset_source: 'team/project', type: 'update' }]];
    }
    if (endpoint === 'repos/team/project/rulesets/40?includes_parents=false') {
      return { enforcement: 'active', rules: [{ type: 'update' }], bypass_actors: [
        { actor_id: 7, actor_type: 'Integration', bypass_mode: 'pull_request' },
        { actor_id: 1, actor_type: 'RepositoryRole', bypass_mode: 'always' },
      ] };
    }
    assert.fail(`Unexpected request ${endpoint}`);
  };
  const reconcile = () => ({ current: [{ decision: { conditionMet: true, effectiveStatus: 'success', waiver: null } }] });
  const result = mergeGithub({
    base: 'main', ruleset: 40, appId: 7, execute: true, selection: { project: 'team/project', pullRequest: 3, commit: head },
  }, { api, reconcile });
  assert.equal(result.exitCode, 2);
  assert.equal(result.reason, 'inactive-branch-protection');
});

test('the enforcement templates guard organization repositories and verifiable sources', () => {
  const read = name => readFileSync(new URL(`../pilot/${name}`, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
  const broker = read('broker-workflow.yml');
  assert.doesNotMatch(broker, /repository_owner/);
  assert.match(broker, /contains\(fromJSON\(vars\.OPENGUILDKIT_MERGE_ACTORS \|\| '\[\]'\), github\.actor\)/);
  assert.match(broker, /contains\(fromJSON\(vars\.OPENGUILDKIT_MERGE_ACTORS \|\| '\[\]'\), github\.triggering_actor\)/);
  assert.match(broker, /merge-protection \\\n\s+--repo "\$GITHUB_REPOSITORY" --base "\$BASE" --ruleset "\$NATIVE_RULESET" --app-id "\$EXPECTED_APP_ID"/);
  assert.match(broker, /--ruleset "\$NATIVE_RULESET" --app-id "\$EXPECTED_APP_ID" \\\n\s+--execute/);
  assert.match(broker, /profileMaintainer:\$maintainer/);

  const required = read('required-workflow.yml');
  assert.match(required, /\n {2}schedule:\n {4}- cron: /);
  assert.match(required, /profileMaintainer:\$maintainer/);
  const posts = [...required.matchAll(/(.*)gh api "repos\/\$GITHUB_REPOSITORY\/statuses\//g)];
  assert.equal(posts.length, 2);
  assert.ok(posts.every(([, prefix]) => prefix.includes('GH_TOKEN="$STATUS_TOKEN"')));
  assert.match(required, /description='OpenGuildKit is checking the current commit'/);
  assert.match(required, /description='OpenGuildKit check incomplete'/);
  assert.match(required, /effectiveStatus \+ " \| rule " \+/);
  for (const name of ['agent-workflow.yml', 'broker-workflow.yml', 'recheck-workflow.yml', 'required-workflow.yml']) {
    assert.doesNotMatch(read(name), /[^\x00-\x7f]/, name);
  }
});
