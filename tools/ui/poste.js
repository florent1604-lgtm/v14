/* ═══════════════════════════════════════════════════════════════════════
   Poste de contrôle Titanium V14.

   Aucune décision n'est prise ici : la page LIT. Elle n'envoie pas d'ordre,
   ne modifie pas de stop, n'appelle pas de LLM. Toute logique métier vit
   côté Python — dupliquer un seuil ici le ferait diverger du seul qui
   compte, celui qui décide.
   ═══════════════════════════════════════════════════════════════════════ */

const $ = (s) => document.querySelector(s);
const el = (t, c, txt) => {
  const n = document.createElement(t);
  if (c) n.className = c;
  if (txt != null) n.textContent = txt;
  return n;
};

const nb = (v, d = 2) =>
  (v == null || Number.isNaN(v)) ? '—' : Number(v).toFixed(d);
const signe = (v, d = 2) =>
  (v == null) ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(d);
const classeSigne = (v) => (v == null ? 'eteint' : v > 0 ? 'pos' : v < 0 ? 'neg' : '');

let ETAT = {};
let TF = 'M15';
let CHART = null;

/* ── Anomalies ────────────────────────────────────────────────────────
   Une page de supervision doit hurler quand quelque chose cloche, pas
   attendre qu'on parcoure douze blocs. Chaque anomalie est dérivée d'un
   fait mesuré, jamais d'une impression.                                */

function anomalies(d) {
  const out = [];
  const w = d.wall || {}, l = d.loop || {}, r = d.risque || {},
        a = d.account || {}, an = d.analystes || {}, p = d.promotion || {};

  if (a.connected === false)
    out.push(['grave', 'MT5', 'terminal injoignable — la boucle ne peut ni lire ni gérer']);
  if (a.connected && a.is_demo === false)
    out.push(['grave', 'COMPTE', `compte RÉEL ${a.login} connecté`]);

  if (l.stale)
    out.push(['grave', 'BOUCLE', `figée depuis ${nb(l.age_s, 0)} s — plus aucune gestion de stop`]);
  else if (l.running === false)
    out.push(['attention', 'BOUCLE', 'arrêtée — aucune accumulation en cours']);

  if (w.armed && !w.open)
    out.push(['attention', 'MUR', `${w.code} ${w.detail || ''}`.trim()]);

  if (r.disponible && r.occupation >= 90)
    out.push(['grave', 'RISQUE', `${nb(r.engage_pct)} % engagés sur ${nb(r.budget_pct, 0)} % — plus de marge`]);
  else if (r.disponible && r.occupation >= 70)
    out.push(['attention', 'RISQUE', `${nb(r.occupation, 0)} % du budget consommé`]);

  if (an.actif && an.en_attente > 8)
    out.push(['attention', 'ANALYSTES', `${an.en_attente} demandes en attente — le travailleur décroche`]);

  if (p.total_trades > 0 && p.sans_classe > 0)
    out.push(['attention', 'JOURNAL',
      `${p.sans_classe} trade(s) sans classe d'actif — ils ne compteront jamais pour la promotion`]);
  if (p.total_trades > 0 && p.cout_exact < p.total_trades)
    out.push(['info', 'COÛT',
      `${p.total_trades - p.cout_exact} trade(s) sans coût mesuré — espérance optimiste`]);

  const c = $('#anomalies');
  c.innerHTML = '';
  if (!out.length) {
    c.append(el('div', 'anomalie rien', 'Aucune anomalie détectée.'));
    return;
  }
  // Le plus grave d'abord : l'ordre de lecture doit suivre l'urgence.
  const rang = { grave: 0, attention: 1, info: 2 };
  out.sort((x, y) => rang[x[0]] - rang[y[0]]);
  for (const [sev, tag, txt] of out) {
    const n = el('div', 'anomalie ' + sev);
    n.append(el('span', 'sev', tag), el('span', null, txt));
    c.append(n);
  }
}

/* ── Constantes vitales ────────────────────────────────────────────── */

function vitaux(d) {
  const a = d.account || {}, w = d.wall || {}, l = d.loop || {},
        r = d.risque || {}, p = d.positions || {};

  $('#v-compte').textContent = a.login ? `${a.login} ${a.mode_label || ''}` : '—';
  $('#v-compte').className = 'v n ' + (a.is_demo === false ? 'mal' : '');

  $('#v-equite').textContent = a.equity != null
    ? `${nb(a.equity)} ${a.currency || ''}` : '—';

  const occ = r.occupation || 0;
  $('#v-risque').textContent = r.disponible
    ? `${nb(r.engage_pct)} % / ${nb(r.budget_pct, 0)} %` : '—';
  $('#v-risque').className = 'v n ' + (occ >= 90 ? 'mal' : occ >= 70 ? 'att' : '');

  const n = (p.positions || []).length;
  $('#v-positions').textContent = `${n} / ${l.max_positions ?? '—'}`;

  const mur = $('#v-mur');
  mur.innerHTML = '';
  const pm = el('span', 'etat-pastille', w.open ? 'OUVERT' : (w.code || '—'));
  // Un mur ouvert ET armé est l'état où des ordres partent : on le signale
  // en rouge. Le vert dirait « tout va bien » alors que c'est l'état le
  // plus engageant du système.
  pm.style.color = w.open && w.armed ? 'var(--grave)'
    : w.open ? 'var(--alerte)' : 'var(--encre-3)';
  mur.append(pm);

  const b = $('#v-boucle');
  b.innerHTML = '';
  const etat = l.stale ? 'FIGÉE' : l.running ? (l.armed ? 'ARMÉE' : 'observation') : 'arrêtée';
  const pb = el('span', 'etat-pastille', etat);
  pb.style.color = l.stale ? 'var(--grave)'
    : l.running ? (l.armed ? 'var(--grave)' : 'var(--long)') : 'var(--encre-3)';
  b.append(pb);

  $('#v-heure').textContent = new Date().toLocaleTimeString('fr-FR');
}

/* ── Exécution ────────────────────────────────────────────────────── */

function execution(d) {
  const w = d.wall || {}, l = d.loop || {}, r = d.risque || {};
  const c = $('#execution');
  c.innerHTML = '';
  const dl = el('dl', 'kv');
  const ligne = (k, v, cls) => {
    dl.append(el('dt', null, k));
    dl.append(el('dd', cls || null, v));
  };
  ligne('armement', w.armed ? 'ARMÉ' : 'désarmé', w.armed ? 'mal' : 'ok');
  ligne('login attendu', String(w.expected_login ?? '—'));
  ligne('compte réel', w.real_allowed ? 'AUTORISÉ' : 'interdit',
        w.real_allowed ? 'mal' : 'ok');
  ligne('positions max', String(l.max_positions ?? '—'));
  ligne('budget de risque', `${nb(l.max_risque_cumule_pct, 0)} %`);
  ligne('gestion', `BE +${l.breakeven_r ?? '—'} R · trail +${l.trail_start_r ?? '—'} R`);
  ligne('dernier battement', l.age_s == null ? '—' : `il y a ${nb(l.age_s, 0)} s`,
        l.stale ? 'mal' : null);
  const s = l.stats || {};
  ligne('activité', s.tours ? `${s.tours} tours · ${s.enter || 0} ENTER · ${s.envoyes || 0} ordres` : '—');
  c.append(dl);

  if (r.disponible) {
    const j = el('div', 'jauge' + (r.occupation >= 90 ? ' brulant'
      : r.occupation >= 70 ? ' chaud' : ''));
    j.append(Object.assign(el('i'), { style: `width:${Math.min(100, r.occupation)}%` }));
    c.append(j);
    const lg = el('div', 'eteint', `exposition ${nb(r.engage_pct)} % — plafond par setup `
      + `${r.echelle?.plancher} % → ${r.echelle?.plafond_modulation} % selon les piliers`);
    lg.style.cssText = 'font-size:10.5px;margin-top:5px';
    c.append(lg);
  }
}

/* ── Positions ────────────────────────────────────────────────────── */

function positions(d) {
  const p = d.positions || {};
  const lignes = p.positions || [];
  $('#pos-compte').textContent = lignes.length ? `${lignes.length} ouverte(s)` : '';
  const c = $('#positions');
  c.innerHTML = '';
  if (!lignes.length) {
    c.append(el('div', 'vide', 'Aucune position portée par V14.'));
    return;
  }
  const t = el('table');
  t.innerHTML = `<thead><tr>
    <th>Ticket</th><th>Actif</th><th>Sens</th><th class="n">Lot</th>
    <th class="n">Entrée</th><th class="n">Stop</th>
    <th class="n">R courant</th><th class="n">P&amp;L</th><th>Phase</th>
  </tr></thead>`;
  const tb = el('tbody');
  for (const x of lignes) {
    const tr = el('tr');
    const sens = x.side > 0 ? 'LONG' : 'SHORT';
    tr.innerHTML = `
      <td class="n eteint">${x.ticket ?? ''}</td>
      <td>${x.symbol ?? ''}</td>
      <td class="${x.side > 0 ? 'long' : 'short'}">${sens}</td>
      <td class="n">${nb(x.volume, 2)}</td>
      <td class="n">${nb(x.entry, 5)}</td>
      <td class="n">${x.sl ? nb(x.sl, 5) : '—'}</td>
      <td class="n ${classeSigne(x.fav_r)}">${signe(x.fav_r, 2)}</td>
      <td class="n ${classeSigne(x.profit)}">${signe(x.profit, 2)}</td>
      <td class="eteint">${x.phase ?? ''}</td>`;
    tb.append(tr);
  }
  t.append(tb);
  c.append(t);
}

/* ── Promotion ────────────────────────────────────────────────────── */

async function promotion() {
  let p;
  try { p = await (await fetch('/api/promotion')).json(); }
  catch { return; }
  ETAT.promotion = p;

  $('#prom-compte').textContent =
    `${p.total_trades} trade(s) · ${p.strate_haute} en strate S≥3`;
  const c = $('#promotion');
  c.innerHTML = '';

  if (!p.cellules?.length) {
    const v = el('div', 'vide');
    v.append(el('div', null,
      p.total_trades
        ? "Aucune cellule : les trades clos ne sont pas encore en strate S≥3 sur un actif classé."
        : "Aucun trade clos. Le compteur démarre à la première clôture."));
    const s = el('div', 'eteint');
    s.style.cssText = 'margin-top:6px;font-size:11px';
    s.textContent = `Seuil d'ouverture : ${p.requis} trades par cellule (classe d'actif × S≥3).`;
    v.append(s);
    c.append(v);
    return;
  }

  const t = el('table');
  t.innerHTML = `<thead><tr>
    <th>Cellule</th><th class="n">Trades</th><th class="n">Espérance</th>
    <th class="n">Borne 95 %</th><th class="n">PF</th><th>État</th>
  </tr></thead>`;
  const tb = el('tbody');
  for (const x of p.cellules) {
    const tr = el('tr');
    const pct = Math.min(100, (x.n / p.requis) * 100);
    const bloc = x.eligible ? 'ÉLIGIBLE'
      : (x.bloquants?.[0] || '').split('(')[0] || '—';
    tr.innerHTML = `
      <td>${x.cell}</td>
      <td class="n">${x.n} / ${p.requis}</td>
      <td class="n ${classeSigne(x.expectancy_r)}">${signe(x.expectancy_r, 3)}</td>
      <td class="n">${x.lower_95_r == null ? '—' : signe(x.lower_95_r, 3)}</td>
      <td class="n">${x.profit_factor == null ? '∞' : nb(x.profit_factor)}</td>
      <td class="${x.eligible ? 'ok' : 'eteint'}">${bloc}</td>`;
    tb.append(tr);
    const barre = el('tr');
    const td = el('td');
    td.colSpan = 6;
    td.style.padding = '0 10px 6px';
    const j = el('div', 'jauge');
    j.append(Object.assign(el('i'), { style: `width:${pct}%` }));
    td.append(j);
    barre.append(td);
    tb.append(barre);
  }
  t.append(tb);
  c.append(t);
}

/* ── Analystes, mode strict, edge, organes ────────────────────────── */

function analystes(d) {
  const a = d.analystes || {};
  const c = $('#analystes');
  c.innerHTML = '';
  $('#ana-compte').textContent = a.actif ? `${a.n_avis} avis` : '';
  if (!a.actif) {
    c.append(el('div', 'vide', 'Aucune demande déposée.'));
    return;
  }
  const dl = el('dl', 'kv');
  dl.append(el('dt', null, 'demandes'), el('dd', null, String(a.n_demandes)));
  dl.append(el('dt', null, 'avis rendus'), el('dd', null, String(a.n_avis)));
  dl.append(el('dt', null, 'en attente'),
            el('dd', a.en_attente > 8 ? 'att' : null, String(a.en_attente)));
  c.append(dl);

  for (const x of (a.recents || []).slice(0, 6)) {
    const l = el('div');
    l.style.cssText = 'display:flex;justify-content:space-between;gap:10px;'
      + 'font-size:11.5px;margin-top:4px';
    l.append(el('span', 'eteint', x.symbol));
    // Un désaccord de direction ABAISSE la taille ; il n'annule rien.
    const cls = x.accord === false ? 'att' : x.accord ? 'ok' : 'eteint';
    l.append(el('span', cls + ' n',
      `${x.rating || 'sans note'} · ${nb(x.conviction, 2)}`));
    c.append(l);
  }
  const note = el('div', 'eteint');
  note.style.cssText = 'margin-top:8px;font-size:10.5px;line-height:1.4';
  note.textContent = 'Un avis module la taille de ±25 % au plus. '
    + "Il n'ouvre, ne ferme et n'empêche aucune position.";
  c.append(note);
}

function fantome(d) {
  const f = d.prod_fantome || {};
  const c = $('#fantome');
  c.innerHTML = '';
  if (!f.actif) {
    c.append(el('div', 'vide', 'Aucune observation.'));
    return;
  }
  const dl = el('dl', 'kv');
  dl.append(el('dt', null, 'observations'), el('dd', null, String(f.lignes)));
  dl.append(el('dt', null, 'PROD aurait entré'),
            el('dd', f.prod_aurait_entre ? 'ok' : 'att',
               String(f.prod_aurait_entre)));
  dl.append(el('dt', null, 'EXPLORE seul'),
            el('dd', 'eteint', String(f.explore_seul)));
  c.append(dl);
  const m = f.motifs_de_refus || {};
  for (const k of Object.keys(m).slice(0, 4)) {
    const l = el('div');
    l.style.cssText = 'display:flex;justify-content:space-between;'
      + 'font-size:11px;margin-top:3px';
    l.append(el('span', 'eteint', k), el('span', 'n', String(m[k])));
    c.append(l);
  }
}

function edge(d) {
  const e = d.edge || {};
  const ctx = e.contexts || [];
  $('#edge-compte').textContent = ctx.length ? `${ctx.length} contexte(s)` : '';
  const c = $('#edge');
  c.innerHTML = '';
  if (!ctx.length) {
    c.append(el('div', 'vide',
      `Registre vide — seuil de mesure ${e.min_samples ?? 20} trades.`));
    return;
  }
  const t = el('table');
  t.innerHTML = `<thead><tr><th>Contexte</th><th class="n">n</th>
    <th class="n">Espérance</th><th>Verdict</th></tr></thead>`;
  const tb = el('tbody');
  for (const x of ctx.slice(0, 30)) {
    const tr = el('tr');
    tr.innerHTML = `<td style="font-size:11px">${x.context}</td>
      <td class="n">${x.samples}</td>
      <td class="n ${classeSigne(x.expectancy_r)}">${signe(x.expectancy_r, 3)}</td>
      <td class="${x.edge_ok === true ? 'ok' : x.edge_ok === false ? 'neg' : 'eteint'}">
        ${x.edge_ok === true ? 'prouvé' : x.edge_ok === false ? 'négatif' : 'inconnu'}</td>`;
    tb.append(tr);
  }
  t.append(tb);
  c.append(t);
}

function organes(d) {
  const b = d.bricks || [], t = d.tests || {};
  const c = $('#organes');
  c.innerHTML = '';
  const tab = el('table');
  tab.innerHTML = `<thead><tr><th>Organe</th><th class="n">Tests</th>
    <th>État</th></tr></thead>`;
  const tb = el('tbody');
  for (const x of b) {
    const tr = el('tr');
    tr.innerHTML = `<td>${x.name}</td><td class="n">${x.tests || 0}</td>
      <td class="${x.exists ? 'ok' : 'mal'}">${x.exists ? 'présent' : 'ABSENT'}</td>`;
    tb.append(tr);
  }
  tab.append(tb);
  c.append(tab);
  if (t.total != null) {
    const p = el('div', 'eteint');
    p.style.cssText = 'padding:7px 10px;font-size:10.5px';
    p.textContent = `${t.total} tests · ${t.failed || 0} échec(s) · relevé ${t.at || '—'}`;
    c.append(p);
  }
}

function vendeurs(d) {
  const v = d.vendors || {};
  const c = $('#vendeurs');
  c.innerHTML = '';
  const dl = el('dl', 'kv');
  for (const [k, chaine] of Object.entries(v.chains || {})) {
    dl.append(el('dt', null, k));
    dl.append(el('dd', null, (chaine || []).join(' → ')));
  }
  c.append(dl);
}

/* ── Graphique ────────────────────────────────────────────────────────
   Canvas plutôt que SVG : quelques centaines de bougies redessinées à
   chaque changement d'actif, c'est exactement le cas où le DOM coûte
   cher pour rien.                                                      */

const COULEURS = {
  sr: '#77d8ff', ote: '#3de68c', fvg: '#ff5c72', vpoc: '#ffc857',
  entree: '#e8eef5', sl: '#c2483f', tp: '#2fa37a',
  hausse: '#2fa37a', baisse: '#c2483f', grille: '#16202a', axe: '#64788a',
};

function dessiner(d) {
  const cv = $('#chart');
  const dpr = window.devicePixelRatio || 1;
  const larg = cv.clientWidth || 900;
  const haut = 440;
  cv.width = larg * dpr;
  cv.height = haut * dpr;
  cv.style.height = haut + 'px';
  const g = cv.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, larg, haut);

  const bougies = d.candles || [];
  if (!bougies.length) {
    g.fillStyle = COULEURS.axe;
    g.font = '13px system-ui';
    g.textAlign = 'center';
    g.fillText('Aucune donnée pour cet actif.', larg / 2, haut / 2);
    return;
  }

  const MG = 62, MD = 12, MH = 12, MB = 22;
  const W = larg - MG - MD, H = haut - MH - MB;

  // L'échelle englobe les bougies ET le plan : un stop hors cadre serait
  // le seul élément qu'on a besoin de voir.
  let lo = Math.min(...bougies.map(b => b.l));
  let hi = Math.max(...bougies.map(b => b.h));
  const p = d.plan || {};
  for (const v of [p.entry, p.sl, p.tp]) if (v) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  const marge = (hi - lo) * 0.06 || 1;
  lo -= marge; hi += marge;

  const y = (v) => MH + H - ((v - lo) / (hi - lo)) * H;
  const pas = W / bougies.length;
  const corps = Math.max(1, Math.min(9, pas * 0.62));

  // Grille et échelle de prix
  g.font = '10px ui-monospace, Consolas, monospace';
  g.textBaseline = 'middle';
  for (let i = 0; i <= 5; i++) {
    const v = lo + (hi - lo) * (i / 5);
    const yy = Math.round(y(v)) + 0.5;
    g.strokeStyle = COULEURS.grille;
    g.lineWidth = 1;
    g.beginPath(); g.moveTo(MG, yy); g.lineTo(MG + W, yy); g.stroke();
    g.fillStyle = COULEURS.axe;
    g.textAlign = 'right';
    g.fillText(v.toFixed(v > 500 ? 1 : 5), MG - 7, yy);
  }

  // Zones — dessinées AVANT les bougies : ce sont des arrière-plans, et
  // les recouvrir masquerait le prix qu'on veut lire.
  for (const z of (d.zones || [])) {
    const c = COULEURS[z.kind] || COULEURS.sr;
    const y1 = y(z.haut), y2 = y(z.bas);
    // Une zone de quelques pips fait moins d'un pixel : on lui impose une
    // hauteur minimale, sinon elle existe sans être visible.
    const h = Math.max(2, Math.abs(y2 - y1));
    g.globalAlpha = 0.16 + 0.12 * (z.force || 0.4);
    g.fillStyle = c;
    g.fillRect(MG, Math.min(y1, y2), W, h);
    g.globalAlpha = 0.75;
    g.strokeStyle = c;
    g.lineWidth = (z.force || 0) > 0.66 ? 1.4 : 0.8;
    g.strokeRect(MG + 0.5, Math.min(y1, y2) + 0.5, W - 1, h);
    g.globalAlpha = 1;
    g.fillStyle = c;
    g.font = '9px ui-monospace, Consolas, monospace';
    g.textAlign = 'left';
    g.fillText(z.kind.toUpperCase(), MG + 4, Math.min(y1, y2) - 4);
  }

  // Bougies
  bougies.forEach((b, i) => {
    const x = MG + i * pas + pas / 2;
    const hausse = b.c >= b.o;
    const c = hausse ? COULEURS.hausse : COULEURS.baisse;
    g.strokeStyle = c;
    g.lineWidth = 1;
    g.beginPath();
    g.moveTo(Math.round(x) + 0.5, y(b.h));
    g.lineTo(Math.round(x) + 0.5, y(b.l));
    g.stroke();
    const yo = y(b.o), yc = y(b.c);
    g.fillStyle = c;
    g.fillRect(x - corps / 2, Math.min(yo, yc), corps, Math.max(1, Math.abs(yc - yo)));
  });

  // Plan de trade
  const trait = (v, c, tiret, libelle) => {
    if (!v) return;
    const yy = Math.round(y(v)) + 0.5;
    g.save();
    g.setLineDash(tiret);
    g.strokeStyle = c; g.lineWidth = 1.2;
    g.beginPath(); g.moveTo(MG, yy); g.lineTo(MG + W, yy); g.stroke();
    g.restore();
    g.fillStyle = c;
    g.font = '10px ui-monospace, Consolas, monospace';
    g.textAlign = 'right';
    g.fillText(`${libelle} ${v.toFixed(v > 500 ? 1 : 5)}`, MG + W - 5, yy - 6);
  };
  trait(p.entry, COULEURS.entree, [], 'entrée');
  trait(p.sl, COULEURS.sl, [5, 4], 'stop');
  trait(p.tp, COULEURS.tp, [5, 4], 'objectif');

  // Rapport risque/gain, en surface : la proportion se lit d'un coup d'œil.
  if (p.entry && p.sl && p.tp) {
    g.globalAlpha = 0.10;
    g.fillStyle = COULEURS.sl;
    g.fillRect(MG, Math.min(y(p.entry), y(p.sl)), W, Math.abs(y(p.sl) - y(p.entry)));
    g.fillStyle = COULEURS.tp;
    g.fillRect(MG, Math.min(y(p.entry), y(p.tp)), W, Math.abs(y(p.tp) - y(p.entry)));
    g.globalAlpha = 1;
  }
}

function lecture(d) {
  $('#lect-titre').textContent = d.symbol
    ? `${d.symbol} · ${d.timeframe} · ${d.asset_class || 'non classé'}` : '';
  const c = $('#lecture');
  c.innerHTML = '';
  if (d.error) {
    c.append(el('div', 'vide mal', d.error));
    return;
  }

  const haut = el('div');
  haut.style.cssText = 'display:flex;gap:10px;align-items:center;flex-wrap:wrap;'
    + 'margin-bottom:10px';
  const verdict = el('span', 'etat-pastille', d.verdict || '—');
  verdict.style.color = d.verdict === 'ENTER' ? 'var(--long)'
    : d.verdict === 'BLOCK' ? 'var(--short)' : 'var(--alerte)';
  haut.append(verdict);
  if (d.side) haut.append(el('span', d.side > 0 ? 'long' : 'short',
    d.side > 0 ? 'LONG' : 'SHORT'));
  haut.append(el('span', 'eteint n',
    `${d.support_passed}/${(d.pillars || []).filter(p =>
      ['fair_value', 'liquidity', 'ote_ob', 'candle_confirmed'].includes(p.name)).length}`
    + ` piliers · quorum ${d.quorum}`));
  haut.append(el('span', 'eteint n', d.code || ''));
  c.append(haut);

  const pil = el('div', 'piliers');
  for (const p of (d.pillars || [])) {
    // `data_valid` et `trend_sr` ne sont pas des piliers de confluence :
    // les afficher au même rang ferait croire à 6 piliers sur 6.
    const hors = ['data_valid', 'trend_sr'].includes(p.name);
    const n = el('span', 'pilier' + (p.passed ? ' ok' : '') + (hors ? ' hors' : ''),
      p.name);
    n.title = p.reason || '';
    pil.append(n);
  }
  c.append(pil);

  const dl = el('dl', 'kv');
  dl.style.marginTop = '10px';
  const l = (k, v, cls) => { dl.append(el('dt', null, k), el('dd', cls, v)); };
  l('contexte', d.context || '—');
  l('famille', d.family || '—');
  l('prix', nb(d.price, 5));
  const p = d.plan || {};
  if (p.entry) {
    l('entrée', nb(p.entry, 5));
    l('stop', nb(p.sl, 5), 'neg');
    l('objectif', `${nb(p.tp, 5)}  (R:R ${nb(p.rr, 1)})`, 'pos');
  }
  c.append(dl);

  const ind = d.indicators || {};
  const cles = Object.keys(ind).slice(0, 12);
  if (cles.length) {
    const g = el('div');
    g.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fill,'
      + 'minmax(140px,1fr));gap:2px 14px;margin-top:10px;font-size:11px';
    for (const k of cles) {
      const v = ind[k];
      if (typeof v !== 'number') continue;
      const n = el('div');
      n.style.cssText = 'display:flex;justify-content:space-between;gap:8px';
      n.append(el('span', 'eteint', k), el('span', 'n', nb(v, 4)));
      g.append(n);
    }
    c.append(g);
  }
}

/* ── Anatomie du flux ─────────────────────────────────────────────────
   Un schéma d'organes reliés par des artères. Ce n'est pas décoratif :
   la taille d'un organe porte son débit, la couleur d'une artère dit si
   le flux passe. Une artère bouchée s'affiche en croix immobile, ce qui
   fait remonter à la CAUSE plutôt qu'au symptôme.                     */

const TEINTE = {
  sain: '#2fa37a', inconnu: '#3d7a99', ralenti: '#d98324', bouche: '#d64545',
};

const COLONNES = [
  ['perception', 'PERCEPTION'],
  ['decision',   'DECISION'],
  ['risque',     'RISQUE'],
  ['action',     'ACTION'],
  ['memoire',    'MEMOIRE'],
];

let MED = null;
let PLAN = [];
let PULSE = 0;

function placer(med, larg, haut) {
  const MG = 14, MH = 30, MB = 16;
  const W = larg - MG * 2, H = haut - MH - MB;
  const pas = W / COLONNES.length;
  const plan = [];
  COLONNES.forEach(([groupe], ci) => {
    const dedans = (med.organes || []).filter(o => o.groupe === groupe);
    const cx = MG + pas * ci + pas / 2;
    dedans.forEach((o, i) => {
      const cy = MH + (H / (dedans.length + 1)) * (i + 1);
      plan.push(Object.assign({}, o, { x: cx, y: cy, r: 20 }));
    });
  });
  return plan;
}

function dessinerAnatomie() {
  const cv = document.querySelector('#anatomie');
  if (!cv || !MED) return;
  const dpr = window.devicePixelRatio || 1;
  const larg = cv.clientWidth || 900, haut = 330;
  cv.width = larg * dpr; cv.height = haut * dpr;
  cv.style.height = haut + 'px';
  const g = cv.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, larg, haut);

  PLAN = placer(MED, larg, haut);
  const par = {};
  for (const o of PLAN) par[o.id] = o;

  const pas = (larg - 28) / COLONNES.length;
  g.font = '9px system-ui';
  g.textAlign = 'center';
  g.fillStyle = '#64788a';
  COLONNES.forEach(function (c, i) {
    g.fillText(c[1], 14 + pas * i + pas / 2, 17);
  });

  for (const a of (MED.arteres || [])) {
    const d = par[a.de], v = par[a.vers];
    if (!d || !v) continue;
    const c = TEINTE[a.etat] || TEINTE.inconnu;
    const bouche = a.etat === 'bouche';

    g.save();
    g.strokeStyle = c;
    g.globalAlpha = bouche ? 0.85 : 0.45;
    g.lineWidth = bouche ? 1.8 : 1.1;
    if (bouche) g.setLineDash([3, 3]);
    const mx = (d.x + v.x) / 2;
    g.beginPath();
    g.moveTo(d.x, d.y);
    g.bezierCurveTo(mx, d.y, mx, v.y, v.x, v.y);
    g.stroke();
    g.restore();

    if (bouche) {
      g.strokeStyle = c; g.lineWidth = 1.7;
      const cx = (d.x + v.x) / 2, cy = (d.y + v.y) / 2;
      g.beginPath();
      g.moveTo(cx - 4, cy - 4); g.lineTo(cx + 4, cy + 4);
      g.moveTo(cx + 4, cy - 4); g.lineTo(cx - 4, cy + 4);
      g.stroke();
    } else {
      const deb = Math.max(0.15, (d.debit + v.debit) / 2);
      const t = ((PULSE * deb) % 100) / 100;
      g.fillStyle = c; g.globalAlpha = 0.9;
      g.beginPath();
      g.arc(d.x + (v.x - d.x) * t, d.y + (v.y - d.y) * t, 2.2, 0, Math.PI * 2);
      g.fill();
      g.globalAlpha = 1;
    }
  }

  for (const o of PLAN) {
    const c = TEINTE[o.etat] || TEINTE.inconnu;
    if (o.debit > 0.05) {
      const gr = g.createRadialGradient(o.x, o.y, o.r * 0.5, o.x, o.y, o.r * 2.1);
      gr.addColorStop(0, c + '44');
      gr.addColorStop(1, c + '00');
      g.fillStyle = gr;
      g.beginPath(); g.arc(o.x, o.y, o.r * 2.1, 0, Math.PI * 2); g.fill();
    }
    g.fillStyle = '#0c1116';
    g.strokeStyle = c;
    g.lineWidth = o.etat === 'bouche' ? 2.2 : 1.4;
    g.beginPath(); g.arc(o.x, o.y, o.r, 0, Math.PI * 2);
    g.fill(); g.stroke();

    if (o.debit > 0.02) {
      g.strokeStyle = c; g.lineWidth = 3;
      g.beginPath();
      g.arc(o.x, o.y, o.r - 4, -Math.PI / 2,
            -Math.PI / 2 + Math.PI * 2 * Math.min(1, o.debit));
      g.stroke();
    }

    g.fillStyle = o.etat === 'bouche' ? c : '#e8eef5';
    g.font = '9.5px system-ui';
    g.textAlign = 'center';
    const mots = String(o.nom).split(' ');
    let ligne = '', lignes = [];
    for (const m of mots) {
      if ((ligne + ' ' + m).trim().length > 13) { lignes.push(ligne.trim()); ligne = m; }
      else ligne += ' ' + m;
    }
    lignes.push(ligne.trim());
    lignes.slice(0, 2).forEach(function (l, i) {
      g.fillText(l, o.x, o.y + o.r + 12 + i * 10);
    });
  }
}

function survolAnatomie(ev) {
  const cv = document.querySelector('#anatomie');
  const b = cv.getBoundingClientRect();
  const x = ev.clientX - b.left, y = ev.clientY - b.top;
  const o = PLAN.find(p => Math.hypot(p.x - x, p.y - y) <= p.r + 4);
  const info = document.querySelector('#anat-info');
  if (!o) { info.hidden = true; return; }
  info.hidden = false;
  info.innerHTML = '';
  const t = el('b', null, o.nom);
  t.style.color = TEINTE[o.etat];
  info.append(t, el('span', 'm', o.mesure || '—'));
  if (o.detail) info.append(el('span', 'd', o.detail));
  info.style.left = Math.min(b.width - 290, Math.max(6, x + 14)) + 'px';
  info.style.top = Math.max(6, y - 10) + 'px';
}

async function chargerMedecin() {
  try { MED = await (await fetch('/api/medecin')).json(); }
  catch { return; }
  const r = document.querySelector('#med-resume');
  r.textContent = MED.resume || '';
  r.className = 'compte ' + (MED.verdict === 'bouche' ? 'mal'
    : MED.verdict === 'ralenti' ? 'att' : 'ok');
  dessinerAnatomie();
}

/* ── Compte à rebours de bougie ───────────────────────────────────────
   Une bougie en cours n'est PAS une bougie close, et le bot ne décide
   qu'à la clôture. Afficher le temps restant évite de lire un signal
   qui peut encore changer entièrement.                                */

const DUREE_TF = { M1: 60, M5: 300, M15: 900, M30: 1800, H1: 3600, H4: 14400 };

function rebours() {
  const n = document.querySelector('#rebours');
  if (!n) return;
  const d = DUREE_TF[TF];
  if (!d) { n.textContent = ''; return; }
  const reste = d - (Math.floor(Date.now() / 1000) % d);
  const mm = String(Math.floor(reste / 60)).padStart(2, '0');
  const ss = String(reste % 60).padStart(2, '0');
  n.textContent = 'bougie ' + TF + ' — ' + mm + ':' + ss;
  n.className = 'rebours' + (reste <= 30 ? ' chaud' : '');
}


/* ── Carte du code ────────────────────────────────────────────────────
   Même toile que l'anatomie, autre lecture. Les pannes de V14 avaient
   toutes la même signature — un module présent, importé, testé, et
   pourtant inerte. Ça ne se voit pas en lisant un fichier ; ça se voit
   sur un graphe.                                                       */

let CARTE = null;
let VUE = 'flux';
let PLAN_CODE = [];

async function chargerCarte() {
  try { CARTE = await (await fetch('/api/carte')).json(); }
  catch { return; }
  if (VUE === 'code') dessinerCarte();
}

function dessinerCarte() {
  const cv = document.querySelector('#anatomie');
  if (!cv || !CARTE) return;
  const dpr = window.devicePixelRatio || 1;
  const larg = cv.clientWidth || 900, haut = 330;
  cv.width = larg * dpr; cv.height = haut * dpr;
  cv.style.height = haut + 'px';
  const g = cv.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, larg, haut);

  const mods = CARTE.modules || {};
  const noms = Object.keys(mods);
  if (!noms.length) return;

  // Colonnes par paquet : la structure du dépôt EST une information.
  const paquets = {};
  for (const n of noms) {
    const p = n.split('.').slice(0, 2).join('.');
    (paquets[p] = paquets[p] || []).push(n);
  }
  const cles = Object.keys(paquets).sort();
  const MG = 16, MH = 30, MB = 16;
  const pas = (larg - MG * 2) / cles.length;
  const H = haut - MH - MB;

  PLAN_CODE = [];
  cles.forEach((p, ci) => {
    const dedans = paquets[p].sort();
    const cx = MG + pas * ci + pas / 2;
    dedans.forEach((n, i) => {
      const m = mods[n];
      // Le rayon suit la taille : un gros module fragile pèse plus lourd
      // qu'un petit, et doit se voir en premier.
      const r = Math.max(5, Math.min(17, 4 + Math.sqrt(m.lignes || 1) * 0.42));
      PLAN_CODE.push({
        nom: n, m, x: cx, y: MH + (H / (dedans.length + 1)) * (i + 1), r,
      });
    });
  });
  const par = {};
  for (const o of PLAN_CODE) par[o.nom] = o;

  g.font = '9px system-ui';
  g.textAlign = 'center';
  g.fillStyle = '#64788a';
  cles.forEach((p, i) =>
    g.fillText(p, MG + pas * i + pas / 2, 17));

  // Dépendances
  g.lineWidth = 0.7;
  for (const o of PLAN_CODE) {
    for (const cible of (o.m.importe || [])) {
      const v = par[cible];
      if (!v || v === o) continue;
      g.strokeStyle = '#1e2b37';
      g.globalAlpha = 0.9;
      const mx = (o.x + v.x) / 2;
      g.beginPath();
      g.moveTo(o.x, o.y);
      g.bezierCurveTo(mx, o.y, mx, v.y, v.x, v.y);
      g.stroke();
    }
  }
  g.globalAlpha = 1;

  for (const o of PLAN_CODE) {
    const f = o.m.faiblesses || [];
    const orphelin = f.includes('orphelin');
    const nu = f.includes('non couvert');
    const c = orphelin ? '#d64545' : nu ? '#d98324' : '#2fa37a';
    g.fillStyle = '#0c1116';
    g.strokeStyle = c;
    g.lineWidth = orphelin ? 2 : 1.2;
    g.beginPath(); g.arc(o.x, o.y, o.r, 0, Math.PI * 2);
    g.fill(); g.stroke();
    // Un point central marque un symbole inerte : défini, jamais appelé.
    if (f.some(x => String(x).startsWith('symbole'))) {
      g.fillStyle = '#ffc857';
      g.beginPath(); g.arc(o.x, o.y, 2.4, 0, Math.PI * 2); g.fill();
    }
  }

  const r = CARTE.resume || {};
  const t = document.querySelector('#med-resume');
  t.textContent = `${r.modules} modules · ${r.lignes} lignes · `
    + `${r.orphelins} orphelin(s) · ${r.non_couverts} non couvert(s) · `
    + `${r.inertes} symbole(s) inerte(s)`;
  t.className = 'compte ' + (r.orphelins ? 'att' : 'ok');
}

function survolCarte(ev) {
  const cv = document.querySelector('#anatomie');
  const b = cv.getBoundingClientRect();
  const x = ev.clientX - b.left, y = ev.clientY - b.top;
  const o = PLAN_CODE.find(p => Math.hypot(p.x - x, p.y - y) <= p.r + 3);
  const info = document.querySelector('#anat-info');
  if (!o) { info.hidden = true; return; }
  info.hidden = false;
  info.innerHTML = '';
  const t = el('b', null, o.nom);
  const f = o.m.faiblesses || [];
  t.style.color = f.includes('orphelin') ? '#d64545'
    : f.includes('non couvert') ? '#d98324' : '#2fa37a';
  info.append(t, el('span', 'm',
    `${o.m.lignes} lignes · ${o.m.tests} test(s) · `
    + `${(o.m.importe_par || []).length} dépendant(s)`));
  for (const x2 of f.slice(0, 4)) info.append(el('span', 'd', x2));
  info.style.left = Math.min(b.width - 290, Math.max(6, x + 14)) + 'px';
  info.style.top = Math.max(6, y - 10) + 'px';
}

function basculerVue(v) {
  VUE = v;
  for (const b of document.querySelectorAll('.vues button'))
    b.setAttribute('aria-pressed', String(b.dataset.vue === v));
  document.querySelector('#leg-flux').hidden = v !== 'flux';
  document.querySelector('#leg-code').hidden = v !== 'code';
  document.querySelector('#anat-info').hidden = true;
  if (v === 'code') { if (!CARTE) chargerCarte(); else dessinerCarte(); }
  else if (MED) dessinerAnatomie();
}

/* ── Chargements ──────────────────────────────────────────────────── */

async function chargerEtat() {
  try {
    const d = await (await fetch('/api/state')).json();
    ETAT = d;
    vitaux(d); anomalies(d); execution(d); positions(d);
    analystes(d); fantome(d); edge(d); organes(d); vendeurs(d);
    const m = d.meta || {};
    $('#pied-llm').textContent =
      `analystes : ${m.provider || '?'} · ${m.deep_model || '?'}`;
    $('#pied-maj').textContent = `relevé ${new Date().toLocaleTimeString('fr-FR')}`;
  } catch (e) {
    const c = $('#anomalies');
    c.innerHTML = '';
    const n = el('div', 'anomalie grave');
    n.append(el('span', 'sev', 'SERVEUR'),
             el('span', null, 'tableau de bord injoignable — ' + e));
    c.append(n);
  }
}

async function chargerUnivers() {
  try {
    const d = await (await fetch('/api/univers')).json();
    const s = $('#sel-actif');
    const courant = s.value;
    s.innerHTML = '';
    for (const sym of (d.symbols || [])) s.append(new Option(sym, sym));
    if (courant) s.value = courant;
  } catch { /* le sélecteur reste vide : sans conséquence */ }
}

async function tracer() {
  const sym = $('#sel-actif').value;
  if (!sym) return;
  const e = $('#etat-chart');
  e.textContent = 'lecture MT5…';
  try {
    const d = await (await fetch(
      `/api/chart?symbol=${encodeURIComponent(sym)}&tf=${TF}&bars=180`)).json();
    CHART = d;
    dessiner(d); lecture(d);
    e.textContent = d.error ? '' : `${(d.candles || []).length} bougies`;
  } catch (err) {
    e.textContent = 'échec : ' + err;
  }
}

/* ── Amorçage ─────────────────────────────────────────────────────── */

$('#btn-tracer').addEventListener('click', tracer);
$('#sel-actif').addEventListener('change', tracer);
for (const b of document.querySelectorAll('.tf button')) {
  b.addEventListener('click', () => {
    TF = b.dataset.tf;
    for (const o of document.querySelectorAll('.tf button'))
      o.setAttribute('aria-pressed', String(o === b));
    tracer();
  });
}
window.addEventListener('resize', () => {
  if (CHART) dessiner(CHART);
  if (VUE === 'code') dessinerCarte();
  else if (MED) dessinerAnatomie();
});

document.querySelector('#anatomie').addEventListener('mousemove',
  (e) => (VUE === 'code' ? survolCarte(e) : survolAnatomie(e)));
for (const b of document.querySelectorAll('.vues button'))
  b.addEventListener('click', () => basculerVue(b.dataset.vue));
document.querySelector('#anatomie').addEventListener('mouseleave',
  () => { document.querySelector('#anat-info').hidden = true; });

chargerEtat();
chargerUnivers();
chargerMedecin();
chargerCarte();
promotion();
rebours();
setInterval(rebours, 1000);
setInterval(chargerMedecin, 12000);
// 20 images/s suffisent pour lire un débit ; au-delà on chauffe pour rien.
setInterval(() => {
  PULSE += 1.6;
  if (VUE === 'flux' && MED) dessinerAnatomie();
}, 50);
setInterval(chargerEtat, 10000);
setInterval(promotion, 30000);
setInterval(chargerUnivers, 120000);
