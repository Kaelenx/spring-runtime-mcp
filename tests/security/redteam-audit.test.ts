// Node 22+ TypeScript entry; production code remains Python, with no npm dependency.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
for (const vector of ['network', 'readonly', 'leakage', 'injection', 'stdio']) {
  test(`red-team ${vector}: actual Python production paths`, () => {
    const result = spawnSync('uv', [
      'run', '--offline', '--no-project', '--with', 'pytest==8.4.2',
      '--with', 'jsonschema==4.25.1', 'python', '-m', 'pytest', '-q',
      `tests/security/test_redteam_${vector}.py`,
    ], { cwd: root, encoding: 'utf8', timeout: 60000, maxBuffer: 2 * 1024 * 1024 });
    assert.ifError(result.error);
    assert.equal(result.status, 0, `${vector} regression failed:\n${result.stdout}\n${result.stderr}`);
  });
}
