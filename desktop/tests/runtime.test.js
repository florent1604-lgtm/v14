const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const {
  backendExecutable,
  dashboardUrl,
  isDashboardReady,
  resolveProjectRoot,
} = require('../lib/runtime');

test('dashboardUrl stays loopback-only', () => {
  assert.equal(dashboardUrl(), 'http://127.0.0.1:8095/');
});

test('backendExecutable resolves the project venv for the local packaged app', () => {
  const actual = backendExecutable({
    packaged: true,
    resourcesPath: 'C:\\Program Files\\Titanium\\resources',
    projectRoot: 'C:\\repo',
  });
  assert.equal(actual, path.join('C:\\repo', '.venv', 'Scripts', 'python.exe'));
});

test('backendExecutable resolves project venv in development', () => {
  const actual = backendExecutable({
    packaged: false,
    resourcesPath: '',
    projectRoot: 'C:\\repo',
  });
  assert.equal(actual, path.join('C:\\repo', '.venv', 'Scripts', 'python.exe'));
});

test('resolveProjectRoot uses the packaged local application config', () => {
  assert.equal(
    resolveProjectRoot({
      packaged: true,
      fallbackRoot: 'C:\\wrong',
      configuredRoot: 'C:\\Users\\flore\\Desktop\\V14',
    }),
    path.normalize('C:\\Users\\flore\\Desktop\\V14'),
  );
});

test('isDashboardReady accepts only a successful state response', async () => {
  assert.equal(await isDashboardReady(async () => ({ ok: true })), true);
  assert.equal(await isDashboardReady(async () => ({ ok: false })), false);
  assert.equal(await isDashboardReady(async () => { throw new Error('offline'); }), false);
});
