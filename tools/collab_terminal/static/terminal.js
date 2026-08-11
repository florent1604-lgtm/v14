/* ═══════════════════════════════════════════════════════════
   V14 Collab Terminal v2 — Client avec dispatch multi-canal
   ═══════════════════════════════════════════════════════════ */

const API = '';
const POLL_INTERVAL = 5000;
let STATE = null;
let CURRENT_FILTER = 'all';

const AGENT_COLORS = {
  prime: '#10b981', claude: '#8b5cf6', codex: '#3b82f6',
  hermes: '#f59e0b', copilot: '#6366f1', florent: '#ef4444',
  team: '#94a3b8', system: '#64748b',
};
const AGENT_ICONS = {
  prime: '⚡', claude: '🟣', codex: '🔵',
  hermes: '🟡', copilot: '🟢', florent: '🔴',
  team: '👥', system: '⚙️',
};

// ─── Navigation ───
document.querySelectorAll('.nav-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const panel = btn.dataset.panel;
    document.querySelectorAll('.content > .panel').forEach(p => {
      p.classList.toggle('hidden', p.id !== `panel-${panel}`);
    });
  });
});

// ─── Fetch state ───
async function fetchState() {
  try {
    const r = await fetch(`${API}/api/state`);
    STATE = await r.json();
    render();
    document.getElementById('last-update').textContent =
      new Date().toLocaleTimeString('fr-FR');
  } catch (e) {
    console.error('Fetch failed:', e);
  }
}

function render() {
  if (!STATE) return;
  renderWorker();
  renderPresence();
  renderKPIs();
  renderServices();
  renderAgentDistribution();
  renderStatusDistribution();
  renderHubActivity();
  renderChat();
  renderTasks();
  renderScoring();
  renderValidation();
  renderAgents();
  renderHeartbeat();
}

// ─── Worker ───
function renderWorker() {
  const w = STATE.worker || {};
  const dot = document.getElementById('worker-dot');
  const label = document.getElementById('worker-label');
  const btn = document.getElementById('worker-toggle');
  const stats = document.getElementById('worker-stats');

  if (w.running) {
    dot.className = 'worker-dot running';
    label.textContent = 'Actif';
    btn.textContent = 'Stop';
    btn.className = 'btn-worker btn-worker-stop';
    btn.onclick = stopWorker;
  } else {
    dot.className = 'worker-dot stopped';
    label.textContent = 'Arrete';
    btn.textContent = 'Lancer';
    btn.className = 'btn-worker btn-worker-start';
    btn.onclick = startWorker;
  }

  let info = `Polls: ${w.polls || 0} | Offset: ${w.last_offset || 0}`;
  if (w.last_poll) {
    info += ` | ${getTimeAgo(w.last_poll)}`;
  }

  // Notifications en attente
  const pending = w.pending || {};
  const pendingList = Object.entries(pending).filter(([,n]) => n > 0);
  if (pendingList.length) {
    info += '<br>' + pendingList.map(([a, n]) => {
      const color = AGENT_COLORS[a] || '#94a3b8';
      return `<span style="color:${color}">${a}:${n}</span>`;
    }).join(' ');
  }

  stats.innerHTML = info;
}

async function startWorker() {
  try {
    const r = await fetch(`${API}/api/worker/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ interval: 300 }),
    });
    const data = await r.json();
    if (data.ok) {
      showNotification('Worker demarre (poll toutes les 5 min, 0 token)', 'success');
    } else {
      showNotification(data.error || 'Erreur', 'error');
    }
    fetchState();
  } catch (e) {
    showNotification('Erreur: ' + e.message, 'error');
  }
}

async function stopWorker() {
  try {
    const r = await fetch(`${API}/api/worker/stop`, { method: 'POST' });
    const data = await r.json();
    showNotification('Worker arrete', 'info');
    fetchState();
  } catch (e) {
    showNotification('Erreur: ' + e.message, 'error');
  }
}

// ─── Presence ───
function renderPresence() {
  const presence = STATE.presence || {};
  const bars = ['presence-bar', 'presence-bar-dash'];

  bars.forEach(barId => {
    const el = document.getElementById(barId);
    if (!el) return;

    el.innerHTML = Object.entries(presence).map(([id, p]) => {
      const color = AGENT_COLORS[id] || '#94a3b8';
      const icon = AGENT_ICONS[id] || '?';
      const status = p.online ? 'online' : 'offline';
      const statusDot = p.online ? '●' : '○';
      const lastAgo = p.last_activity ? getTimeAgo(p.last_activity) : 'jamais';

      return `<div class="presence-agent ${status}" title="${p.name}: ${p.channel}\nDernier msg: ${lastAgo}">
        <span class="presence-dot" style="color:${p.online ? color : '#64748b'}">${statusDot}</span>
        <span class="presence-icon">${icon}</span>
        <span class="presence-name" style="color:${p.online ? color : '#64748b'}">${p.name}</span>
        <span class="presence-info">${p.online ? p.channel : 'hors ligne'}</span>
        <span class="presence-last">${lastAgo}</span>
      </div>`;
    }).join('');
  });
}

function getTimeAgo(iso) {
  if (!iso) return 'jamais';
  try {
    const ms = Date.now() - new Date(iso).getTime();
    if (ms < 60000) return 'a l\'instant';
    if (ms < 3600000) return Math.floor(ms/60000) + 'min';
    if (ms < 86400000) return Math.floor(ms/3600000) + 'h';
    return Math.floor(ms/86400000) + 'j';
  } catch { return '?'; }
}

// ─── KPIs ───
function renderKPIs() {
  const c = STATE.tasks.counts;
  const total = Object.values(c).reduce((a, b) => a + b, 0);
  document.getElementById('kpi-total').textContent = total;
  document.getElementById('kpi-done').textContent = c.done || 0;
  document.getElementById('kpi-progress').textContent = c.in_progress || 0;
  document.getElementById('kpi-review').textContent = c.review || 0;
  document.getElementById('kpi-blocked').textContent = c.blocked || 0;
  document.getElementById('kpi-backlog').textContent = c.backlog || 0;
}

// ─── Services ───
function renderServices() {
  const el = document.getElementById('service-list');
  const s = STATE.services;
  el.innerHTML = Object.entries(s).map(([name, info]) => {
    const status = info.running ? 'up' : '';
    const label = info.running ? 'EN LIGNE' : 'HORS LIGNE';
    return `<div class="service-dot ${status}">
      <span>${name}</span>
      <span class="service-port">:${info.port || '—'}</span>
    </div>`;
  }).join('');
}

// ─── Heartbeat ───
function renderHeartbeat() {
  const hb = STATE.heartbeat || {};
  const badge = document.getElementById('hb-badge');
  if (hb.at) {
    const ago = Math.round((Date.now() - new Date(hb.at).getTime()) / 60000);
    badge.textContent = `♥ ${ago}min | Eq: ${hb.equity || '—'} | Portables: ${hb.portables || '—'}`;
    badge.className = 'heartbeat-badge' + (ago < 5 ? ' hb-live' : '');
  } else {
    badge.textContent = 'Pas de heartbeat';
  }
}

// ─── Agent Distribution ───
function renderAgentDistribution() {
  const el = document.getElementById('agent-distribution');
  const tasks = STATE.tasks.items;
  const counts = {};
  tasks.forEach(t => { counts[t.owner] = (counts[t.owner] || 0) + 1; });
  const max = Math.max(...Object.values(counts), 1);
  el.innerHTML = Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([agent, count]) => {
    const color = AGENT_COLORS[agent] || '#94a3b8';
    const pct = (count / max * 100).toFixed(0);
    return `<div class="dist-row">
      <span class="dist-label">${AGENT_ICONS[agent] || '?'} ${agent}</span>
      <div class="dist-bar-bg"><div class="dist-bar" style="width:${pct}%;background:${color}"></div></div>
      <span class="dist-count">${count}</span>
    </div>`;
  }).join('');
}

// ─── Status Distribution ───
function renderStatusDistribution() {
  const el = document.getElementById('status-distribution');
  const c = STATE.tasks.counts;
  const colors = { in_progress: '#3b82f6', done: '#10b981', review: '#f59e0b', blocked: '#ef4444', backlog: '#64748b' };
  const max = Math.max(...Object.values(c), 1);
  el.innerHTML = Object.entries(c).sort((a, b) => b[1] - a[1]).map(([status, count]) => {
    const pct = (count / max * 100).toFixed(0);
    return `<div class="dist-row">
      <span class="dist-label">${status}</span>
      <div class="dist-bar-bg"><div class="dist-bar" style="width:${pct}%;background:${colors[status] || '#94a3b8'}"></div></div>
      <span class="dist-count">${count}</span>
    </div>`;
  }).join('');
}

// ─── Hub Activity (remplace bus activity) ───
function renderHubActivity() {
  const el = document.getElementById('hub-activity');
  const hubMsgs = STATE.hub_activity || [];
  const busMsgs = STATE.bus_activity || [];

  // Combiner hub et bus
  let all = [];
  hubMsgs.forEach(m => all.push({...m, channel: 'hub'}));
  busMsgs.slice(-10).forEach(m => all.push({...m, channel: 'bus'}));
  all.sort((a, b) => (b.at || '').localeCompare(a.at || ''));

  if (!all.length) {
    el.innerHTML = '<div class="hint">Aucune activite recente.</div>';
    return;
  }

  el.innerHTML = all.slice(0, 25).map(m => {
    const fromColor = AGENT_COLORS[m.from] || '#94a3b8';
    const icon = AGENT_ICONS[m.from] || '?';
    const channelBadge = m.channel === 'hub'
      ? '<span class="channel-badge hub">HUB</span>'
      : '<span class="channel-badge bus">BUS</span>';
    return `<div class="activity-item">
      ${channelBadge}
      <span class="activity-from" style="color:${fromColor}">${icon} ${m.from || '—'}</span>
      <span class="activity-arrow">-></span>
      <span class="activity-to">${m.to || '—'}</span>
      <span class="activity-content">${escHtml(m.content || '')}</span>
      <span class="activity-time">${formatTime(m.at)}</span>
    </div>`;
  }).join('');
}

// ─── Chat ───
function renderChat() {
  const el = document.getElementById('chat-messages');
  const msgs = STATE.chat || [];

  el.innerHTML = msgs.map(m => {
    const color = AGENT_COLORS[m.from] || '#94a3b8';
    const icon = AGENT_ICONS[m.from] || '?';
    const routes = m.routed || [];
    const source = m.source || '';

    let routeBadges = '';
    if (routes.length) {
      routeBadges = routes.map(r => `<span class="route-badge">${r}</span>`).join('');
    }
    if (source) {
      routeBadges += `<span class="route-badge source">${source}</span>`;
    }

    return `<div class="chat-msg" style="border-left-color:${color}">
      <div class="chat-msg-header">
        <span class="chat-msg-from" style="color:${color}">${icon} ${m.from}</span>
        <span class="chat-msg-to">-> ${m.to || 'all'}</span>
        <div class="chat-msg-routes">${routeBadges}</div>
        <span class="chat-msg-time">${formatTime(m.at)}</span>
      </div>
      <div class="chat-msg-body">${escHtml(m.content)}</div>
    </div>`;
  }).join('') || '<div class="hint">Aucun message. Les messages seront routes vers le Hub MCP et le Bus.</div>';
  el.scrollTop = el.scrollHeight;
}

document.getElementById('chat-send').addEventListener('click', sendChat);
document.getElementById('chat-input').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
});

async function sendChat() {
  const input = document.getElementById('chat-input');
  const content = input.value.trim();
  if (!content) return;

  const from = document.getElementById('chat-as').value;
  const to = document.getElementById('chat-to').value;
  const sendBtn = document.getElementById('chat-send');

  sendBtn.disabled = true;
  sendBtn.textContent = 'Envoi...';

  try {
    const resp = await fetch(`${API}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from, to, content, type: 'message' }),
    });
    const result = await resp.json();

    // Afficher le feedback de routage
    if (result.routes) {
      const routes = result.routes.join(', ');
      showNotification(`Message route vers : ${routes}`, 'success');
    }

    input.value = '';
    fetchState();
  } catch (e) {
    showNotification('Erreur d\'envoi: ' + e.message, 'error');
  } finally {
    sendBtn.disabled = false;
    sendBtn.textContent = 'Envoyer';
  }
}

// Notification toast
function showNotification(text, type = 'info') {
  const el = document.createElement('div');
  el.className = `notification notification-${type}`;
  el.textContent = text;
  document.body.appendChild(el);
  setTimeout(() => el.classList.add('show'), 10);
  setTimeout(() => {
    el.classList.remove('show');
    setTimeout(() => el.remove(), 300);
  }, 4000);
}

// ─── Tasks ───
function renderTasks() {
  const el = document.getElementById('task-list');
  let tasks = STATE.tasks.items || [];
  if (CURRENT_FILTER !== 'all') tasks = tasks.filter(t => t.status === CURRENT_FILTER);

  el.innerHTML = tasks.map(t => {
    const color = AGENT_COLORS[t.owner] || '#94a3b8';
    const scoreColor = t.score >= 70 ? '#10b981' : t.score >= 40 ? '#f59e0b' : '#ef4444';
    const scorePct = Math.min(t.score || 0, 100);
    return `<div class="task-card">
      <div class="task-card-header">
        <span class="task-title">${escHtml(t.title)}</span>
        <div class="task-meta">
          <span class="badge badge-${t.status}">${t.status}</span>
          <span class="badge badge-${(t.priority||'p2').toLowerCase()}">${t.priority}</span>
          <span class="badge-agent" style="background:${color}20;color:${color}">${AGENT_ICONS[t.owner] || '?'} ${t.owner}</span>
        </div>
      </div>
      ${t.description ? `<div class="task-description">${escHtml(t.description)}</div>` : ''}
      ${t.note ? `<div class="task-note">${escHtml(t.note)}</div>` : ''}
      <div class="task-footer">
        <span>MAJ: ${AGENT_ICONS[t.updated_by] || ''} ${t.updated_by || '—'} . ${formatTime(t.updated_at)}</span>
        <div class="task-score">
          <span style="color:${scoreColor}">${t.score}/100</span>
          <div class="score-bar"><div class="score-fill" style="width:${scorePct}%;background:${scoreColor}"></div></div>
        </div>
      </div>
    </div>`;
  }).join('') || '<div class="hint">Aucune tache dans ce filtre.</div>';
}

document.querySelectorAll('.filter-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    CURRENT_FILTER = btn.dataset.filter;
    renderTasks();
  });
});

document.getElementById('new-task-btn').addEventListener('click', () => {
  document.getElementById('new-task-form').classList.toggle('hidden');
});
document.getElementById('tf-cancel').addEventListener('click', () => {
  document.getElementById('new-task-form').classList.add('hidden');
});
document.getElementById('new-task-form').addEventListener('submit', async e => {
  e.preventDefault();
  try {
    await fetch(`${API}/api/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: document.getElementById('tf-title').value,
        description: document.getElementById('tf-desc').value,
        owner: document.getElementById('tf-owner').value,
        priority: document.getElementById('tf-priority').value,
        actor: 'florent', status: 'backlog',
      }),
    });
    document.getElementById('tf-title').value = '';
    document.getElementById('tf-desc').value = '';
    document.getElementById('new-task-form').classList.add('hidden');
    showNotification('Tache creee et publiee', 'success');
    fetchState();
  } catch (e) { showNotification('Erreur: ' + e.message, 'error'); }
});

// ─── Scoring ───
function renderScoring() {
  const perf = STATE.performance || {};
  const grid = document.getElementById('scoring-grid');

  grid.innerHTML = Object.entries(perf).sort((a, b) => b[1].avg_score - a[1].avg_score).map(([id, p]) => {
    const color = AGENT_COLORS[id] || '#94a3b8';
    const icon = AGENT_ICONS[id] || '?';
    return `<div class="score-card" style="border-top: 3px solid ${color}">
      <div class="score-card-icon">${icon}</div>
      <div class="score-card-name" style="color:${color}">${p.name}</div>
      <div class="score-card-role">${p.role}</div>
      <dl class="score-card-stats">
        <dt>Taches</dt><dd>${p.total_tasks}</dd>
        <dt>Done</dt><dd style="color:var(--green)">${p.done}</dd>
        <dt>Active</dt><dd style="color:var(--blue)">${p.in_progress}</dd>
        <dt>Bloquees</dt><dd style="color:var(--red)">${p.blocked}</dd>
        <dt>Completion</dt><dd>${p.completion_rate}%</dd>
        <dt>Score moy.</dt><dd style="color:${color}">${p.avg_score}</dd>
      </dl>
    </div>`;
  }).join('');

  const lb = document.getElementById('leaderboard');
  const sorted = Object.entries(perf).filter(([, p]) => p.total_tasks > 0)
    .sort((a, b) => b[1].avg_score - a[1].avg_score);
  const ranks = ['gold', 'silver', 'bronze'];
  lb.innerHTML = sorted.map(([id, p], i) => {
    const color = AGENT_COLORS[id] || '#94a3b8';
    return `<div class="leader-row">
      <span class="leader-rank ${ranks[i] || ''}">#${i + 1}</span>
      <span class="leader-name" style="color:${color}">${AGENT_ICONS[id] || '?'} ${p.name}</span>
      <div class="leader-bar"><div class="leader-bar-fill" style="width:${p.avg_score}%;background:${color}"></div></div>
      <span class="leader-score" style="color:${color}">${p.avg_score}</span>
    </div>`;
  }).join('') || '<div class="hint">Aucun agent avec des taches.</div>';

  const ts = document.getElementById('task-scores');
  const tasks = (STATE.tasks.items || []).sort((a, b) => (b.score || 0) - (a.score || 0));
  ts.innerHTML = tasks.map(t => {
    const color = t.score >= 70 ? '#10b981' : t.score >= 40 ? '#f59e0b' : '#ef4444';
    const ownerColor = AGENT_COLORS[t.owner] || '#94a3b8';
    return `<div class="tscore-row">
      <span class="badge-agent" style="background:${ownerColor}20;color:${ownerColor}">${AGENT_ICONS[t.owner]||'?'} ${t.owner}</span>
      <span class="tscore-title">${escHtml(t.title)}</span>
      <span class="tscore-factors">${(t.score_factors || []).join(' . ')}</span>
      <span class="tscore-score" style="color:${color}">${t.score}</span>
    </div>`;
  }).join('');
}

// ─── Validation Matrix ───
function renderValidation() {
  const matrix = STATE.validation_matrix || {};
  const agents = Object.keys(STATE.agents || {});
  const el = document.getElementById('validation-matrix');

  let html = '<table class="matrix-table"><thead><tr><th>Owner / Valideur -></th>';
  agents.forEach(a => {
    html += `<th style="color:${AGENT_COLORS[a]||'#94a3b8'}">${AGENT_ICONS[a]||'?'} ${a}</th>`;
  });
  html += '</tr></thead><tbody>';
  agents.forEach(owner => {
    html += `<tr><th style="color:${AGENT_COLORS[owner]||'#94a3b8'}">${AGENT_ICONS[owner]||'?'} ${owner}</th>`;
    agents.forEach(validator => {
      const val = (matrix[owner] || {})[validator] || 0;
      html += `<td class="${val > 0 ? 'matrix-cell-pos' : 'matrix-cell-0'}">${val}</td>`;
    });
    html += '</tr>';
  });
  html += '</tbody></table>';
  el.innerHTML = html;

  const vl = document.getElementById('validation-levels');
  const tasks = STATE.tasks.items || [];
  vl.innerHTML = tasks.map(t => {
    const validators = t.validators || [];
    const dots = agents.map(a => {
      const validated = validators.includes(a);
      return `<div class="vlevel-dot ${validated ? 'validated' : ''}" title="${a}">${AGENT_ICONS[a]||'?'}</div>`;
    }).join('');
    const color = t.score >= 70 ? '#10b981' : t.score >= 40 ? '#f59e0b' : '#ef4444';
    return `<div class="vlevel-row">
      <span class="badge badge-${t.status}">${t.status}</span>
      <span class="vlevel-title">${escHtml(t.title)}</span>
      <div class="vlevel-validators">${dots}</div>
      <span class="vlevel-score" style="color:${color}">${validators.length}/${agents.length}</span>
    </div>`;
  }).join('');
}

// ─── Agents ───
function renderAgents() {
  const perf = STATE.performance || {};
  const agents = STATE.agents || {};
  const grid = document.getElementById('agents-grid');

  grid.innerHTML = Object.entries(agents).map(([id, a]) => {
    const p = perf[id] || {};
    const color = AGENT_COLORS[id] || '#94a3b8';
    const icon = AGENT_ICONS[id] || '?';
    return `<div class="agent-card" style="border-top: 3px solid ${color}">
      <div class="agent-header">
        <span class="agent-icon">${icon}</span>
        <div>
          <div class="agent-name" style="color:${color}">${a.name}</div>
          <div class="agent-role">${a.role}</div>
        </div>
      </div>
      <div class="agent-stats">
        <div><span class="agent-stat-val" style="color:var(--green)">${p.done || 0}</span><span class="agent-stat-label">Done</span></div>
        <div><span class="agent-stat-val" style="color:var(--blue)">${p.in_progress || 0}</span><span class="agent-stat-label">Active</span></div>
        <div><span class="agent-stat-val" style="color:${color}">${p.completion_rate || 0}%</span><span class="agent-stat-label">Completion</span></div>
      </div>
    </div>`;
  }).join('');

  const reports = STATE.prime_reports || [];
  document.getElementById('prime-reports').innerHTML = reports.map(r =>
    `<div class="report-item">
      <div class="report-title">${escHtml(r.task_id)}</div>
      <div class="report-path">${escHtml(r.path)}</div>
      <div class="report-content">${escHtml(r.content)}</div>
    </div>`
  ).join('') || '<div class="hint">Aucun rapport Prime disponible.</div>';
}

// ─── Helpers ───
function escHtml(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function formatTime(iso) {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleString('fr-FR', { hour:'2-digit', minute:'2-digit', day:'2-digit', month:'2-digit' }); }
  catch { return iso.slice(0, 16); }
}

// ─── Refresh ───
document.getElementById('refresh-btn').addEventListener('click', fetchState);
fetchState();
setInterval(fetchState, POLL_INTERVAL);
