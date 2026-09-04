const path = require('node:path');

const HOST = '127.0.0.1';
const PORT = 8095;

function dashboardUrl(route = '/') {
  return `http://${HOST}:${PORT}${route}`;
}

function backendExecutable({ packaged, resourcesPath, projectRoot }) {
  return path.join(projectRoot, '.venv', 'Scripts', 'python.exe');
}

function resolveProjectRoot({ packaged, fallbackRoot, configuredRoot }) {
  if (packaged && configuredRoot) return path.normalize(configuredRoot);
  return path.normalize(fallbackRoot);
}

async function isDashboardReady(fetchImpl = fetch) {
  try {
    const response = await fetchImpl(dashboardUrl('/api/state'), {
      signal: AbortSignal.timeout(1200),
      cache: 'no-store',
    });
    return response.ok;
  } catch {
    return false;
  }
}

module.exports = { backendExecutable, dashboardUrl, isDashboardReady, resolveProjectRoot };
