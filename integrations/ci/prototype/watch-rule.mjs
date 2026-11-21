import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { setTimeout } from 'node:timers/promises';
import { evaluate } from './demo/check.mjs';

const [configPath, statePath] = process.argv.slice(2);
if (!configPath || !statePath) {
  throw new Error('Usage: node prototype/watch-rule.mjs CONFIG_JSON STATE_JSON');
}

const { policyRepo, policyRef, repositories, pullRequests } = readJson(configPath);
if (pullRequests !== undefined && (
  !pullRequests || typeof pullRequests !== 'object' || Array.isArray(pullRequests) ||
  Object.keys(pullRequests).length !== repositories.length ||
  repositories.some(repo => {
    const numbers = pullRequests[repo];
    return !Array.isArray(numbers) || numbers.length === 0 ||
      numbers.some(number => !Number.isSafeInteger(number) || number < 1) ||
      new Set(numbers).size !== numbers.length;
  })
)) {
  throw new Error('pullRequests doit contenir une liste de numéros de PR positifs et distincts pour chaque dépôt surveillé.');
}
const reportPath = 'result.json';
rmSync(reportPath, { force: true });

function watchesPullRequest(repo, number) {
  return pullRequests === undefined || pullRequests[repo]?.includes(number);
}

function readJson(path) {
  return JSON.parse(readFileSync(path, 'utf8'));
}

function gh(args, input) {
  return execFileSync('gh', args, { encoding: 'utf8', input, timeout: 30_000 }).trim();
}

function readGithubJson(path) {
  return JSON.parse(gh(['api', path]));
}

function readFileAt(repo, commit, path) {
  const { content } = readGithubJson(`repos/${repo}/contents/${path}?ref=${commit}`);
  return JSON.parse(Buffer.from(content, 'base64').toString('utf8'));
}

const policyPath = 'policy/export-link.json';
const policyCommit = gh([
  'api', '--method', 'GET', `repos/${policyRepo}/commits`,
  '-f', `sha=${policyRef}`, '-f', `path=${policyPath}`, '-F', 'per_page=1', '--jq', '.[0].sha',
]);
const previous = existsSync(statePath) ? readJson(statePath) : {};
if (previous.policyCommit === policyCommit && previous.done) {
  console.log(`Règle inchangée : ${policyCommit}`);
  process.exit(0);
}

const rule = readFileAt(policyRepo, policyCommit, policyPath);
const batch = previous.policyCommit === policyCommit ? {
  ...previous,
  targets: previous.targets.filter(({ repo, number }) => watchesPullRequest(repo, number)),
} : {
  policyCommit,
  targets: repositories.flatMap(repo => {
    const pages = JSON.parse(gh(['api', '--paginate', '--slurp', `repos/${repo}/pulls?state=open&per_page=100`]));
    return pages.flat().filter(pr => watchesPullRequest(repo, pr.number)).map(pr => {
      if (pr.head.repo?.full_name !== repo) {
        throw new Error(`Le prototype prend en charge les PR internes au dépôt : ${repo}#${pr.number}`);
      }
      return { repo, number: pr.number, commit: pr.head.sha };
    });
  }),
};

function saveBatch() {
  const temporary = `${statePath}.tmp`;
  writeFileSync(temporary, JSON.stringify(batch, null, 2) + '\n');
  renameSync(temporary, statePath);
}

for (const target of batch.targets) {
  const { repo, number, commit } = target;
  const result = evaluate(rule, policyCommit, commit, readFileAt(repo, commit, 'export.json'));
  target.result = result;
  if (!target.githubRun) {
    const url = gh(['workflow', 'run', 'openguildkit.yml', '--repo', repo, '--ref', 'main', '--json'],
      JSON.stringify({ target_commit: commit, policy_commit: policyCommit, policy_json: JSON.stringify(rule) }));
    const runId = /\/actions\/runs\/(\d+)$/.exec(url)?.[1];
    if (!runId) {
      throw new Error(`GitHub n'a pas retourné l'identifiant d'exécution pour ${repo}#${number}.`);
    }
    target.githubRun = { id: runId, url };
    saveBatch();
  }
  if (!target.jenkinsPublished) {
    const description = `v${result.version} | règle ${policyCommit}`;
    gh(['api', `repos/${repo}/statuses/${commit}`, '-f', 'context=openguildkit/jenkins',
      '-f', `state=${result.state}`, '-f', `description=${description}`]);
    target.jenkinsPublished = true;
    saveBatch();
  }
}

for (const target of batch.targets) {
  const { repo, commit, result, githubRun } = target;
  const deadline = Date.now() + 180_000;
  do {
    const run = JSON.parse(gh(['run', 'view', githubRun.id, '--repo', repo, '--json', 'status,conclusion']));
    if (run.status === 'completed') {
      const statuses = readGithubJson(`repos/${repo}/commits/${commit}/statuses?per_page=100`);
      const status = statuses.find(item => item.context === 'openguildkit/export-link-expiry' &&
        item.target_url === githubRun.url && item.description.includes(policyCommit));
      if (run.conclusion !== result.state || status?.state !== result.state) {
        delete target.githubRun;
        saveBatch();
        throw new Error(`Verdict GitHub Actions absent ou différent : ${githubRun.url}`);
      }
      break;
    }
    if (Date.now() >= deadline) throw new Error(`GitHub Actions est encore en cours : ${githubRun.url}`);
    await setTimeout(3_000);
  } while (true);
}

const report = { policyRepo, policyRef, policyCommit, jenkinsBuild: process.env.BUILD_NUMBER, targets: batch.targets };
const evidence = JSON.stringify(report, null, 2) + '\n';
const history = join(dirname(statePath), 'reports');
mkdirSync(history, { recursive: true });
writeFileSync(join(history, `${policyCommit}.json`), evidence);
writeFileSync(reportPath, evidence);
process.stdout.write(evidence);
batch.done = true;
saveBatch();
process.exitCode = batch.targets.some(target => target.result.state === 'failure') ? 1 : 0;
