const { app, BrowserWindow, dialog, Menu } = require('electron');
const { spawn, execFile } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const {
  backendExecutable,
  dashboardUrl,
  isDashboardReady,
  resolveProjectRoot,
} = require('./lib/runtime');

const FALLBACK_ROOT = path.resolve(__dirname, '..');
const localConfig = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'app-config.json'), 'utf8'),
);
const PROJECT_ROOT = resolveProjectRoot({
  packaged: app.isPackaged,
  fallbackRoot: FALLBACK_ROOT,
  configuredRoot: localConfig.projectRoot,
});
let backend = null;
let ownsBackend = false;

function loadingPage() {
  const html = `<!doctype html><meta charset="utf-8"><title>Titanium V14</title>
  <style>html,body{height:100%;margin:0;background:#05080c;color:#dfe8ee;font:14px "Segoe UI",sans-serif}
  body{display:grid;place-items:center}.box{text-align:center}.mark{width:34px;height:34px;border:1px solid #64d8e8;transform:rotate(45deg);margin:0 auto 24px;animation:p 1.4s ease-in-out infinite}
  h1{font-size:18px;letter-spacing:.12em}p{color:#738895;font:11px Consolas,monospace}@keyframes p{50%{opacity:.35;transform:rotate(45deg) scale(.86)}}</style>
  <div class="box"><div class="mark"></div><h1>TITANIUM V14</h1><p>INITIALISATION DU POSTE DE CONTRÔLE LOCAL…</p></div>`;
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
}

async function startBackend() {
  if (await isDashboardReady()) return;

  const executable = backendExecutable({
    packaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    projectRoot: PROJECT_ROOT,
  });
  const args = [path.join(PROJECT_ROOT, 'tools', 'dashboard.py')];

  backend = spawn(executable, args, {
    cwd: PROJECT_ROOT,
    windowsHide: true,
    stdio: 'ignore',
  });
  ownsBackend = true;
  backend.once('exit', () => { backend = null; });

  for (let attempt = 0; attempt < 80; attempt += 1) {
    if (await isDashboardReady()) return;
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw new Error('Le backend V14 ne répond pas sur 127.0.0.1:8095.');
}

function stopBackend() {
  if (!ownsBackend || !backend?.pid) return;
  execFile('taskkill.exe', ['/pid', String(backend.pid), '/t', '/f'], { windowsHide: true });
  backend = null;
  ownsBackend = false;
}

async function createWindow() {
  Menu.setApplicationMenu(null);
  const win = new BrowserWindow({
    title: 'Titanium V14 Control Center',
    width: 1600,
    height: 980,
    minWidth: 1180,
    minHeight: 700,
    backgroundColor: '#05080c',
    show: false,
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      devTools: !app.isPackaged,
    },
  });

  await win.loadURL(loadingPage());
  win.show();

  try {
    await startBackend();
    await win.loadURL(dashboardUrl('/poste'));
  } catch (error) {
    dialog.showErrorBox('Titanium V14 — démarrage impossible', String(error.message || error));
  }
}

app.whenReady().then(createWindow);
app.on('before-quit', stopBackend);
app.on('window-all-closed', () => app.quit());
