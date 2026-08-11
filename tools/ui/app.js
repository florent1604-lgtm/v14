/* Titanium V14 — interface de contrôle.
   Vanilla, aucun build, aucune dépendance distante.

   Deux règles de fond :
   · l'interface est en LECTURE SEULE, sauf le balayage — qui reste
     déterministe et n'exécute rien ;
   · un bloc en erreur affiche son erreur et n'empêche pas les autres de
     s'afficher : sur un poste de trading, une page à moitié vivante vaut mieux
     qu'une page blanche. */

'use strict';

const $ = (s) => document.querySelector(s);
const el = (t, cls, txt) => {
  const n = document.createElement(t);
  if (cls) n.className = cls;
  if (txt !== undefined) n.textContent = txt;
  return n;
};

/* Échappement systématique : toute donnée vient du serveur, jamais dans du
   innerHTML sans passer par textContent. */
const setRows = (cible, lignes) => {
  const t = el('table', 'kv');
  for (const [k, v, cls] of lignes) {
    const tr = el('tr');
    tr.append(el('td', null, k), el('td', cls || null, v == null ? '—' : String(v)));
    t.append(tr);
  }
  cible.replaceChildren(t);
};

const erreur = (cible, msg) => cible.replaceChildren(el('div', 'err', msg));
const vide = (cible, msg) => cible.replaceChildren(el('div', 'empty', msg));

const nombre = (v, d = 2) =>
  (v === null || v === undefined || Number.isNaN(v)) ? '—' : Number(v).toFixed(d);

/* ─────────────────────────────── rendu ──────────────────────────────────── */

function rendreMur(w) {
  const c = $('#wall');
  if (w.error) return erreur(c, w.error);

  const ouvert = w.open === true;
  c.replaceChildren();

  const etat = el('div', 'wall-state');
  etat.append(
    el('span', 'verdict ' + (ouvert ? 'bad' : 'ok'), ouvert ? 'OUVERT' : 'FERMÉ'),
    el('span', 'dim', w.detail || (ouvert ? 'les trois verrous sont ouverts' : w.code)),
  );
  c.append(etat);

  /* Un mur ouvert n'est PAS une bonne nouvelle : c'est l'état où des ordres
     peuvent partir. D'où le rouge. */
  const locks = el('div', 'locks');
  const ligne = (nom, ok, valeur) => {
    const d = el('div', 'lock ' + (ok ? 'on' : 'off'));
    d.append(el('span', 'label', nom), el('span', 'val ' + (ok ? 'ok' : 'bad'), valeur));
    return d;
  };
  locks.append(
    ligne('1 · armement', !w.armed, w.armed ? 'ARMÉ' : 'désarmé'),
    ligne('2 · login attendu', !!w.expected_login, w.expected_login || 'aucun'),
    ligne('3 · compte réel', !w.real_allowed, w.real_allowed ? 'AUTORISÉ' : 'interdit'),
  );
  c.append(locks);

  const note = el('div', 'faint');
  note.style.marginTop = 'var(--s3)';
  note.style.fontSize = '12px';
  note.textContent = w.min_lot_overrisk
    ? 'Sur-risque au lot minimum AUTORISÉ — un petit compte peut risquer plus que prévu.'
    : 'Sur-risque au lot minimum refusé (défaut sûr).';
  c.append(note);

  $('#pulse').classList.toggle('bad', ouvert);
  const pw = $('#pill-wall');
  pw.textContent = ouvert ? 'mur OUVERT' : 'mur fermé';
  pw.className = 'pill ' + (ouvert ? 'alert' : 'on');
}

function rendreCompte(a) {
  const c = $('#account');
  if (a.error || a.connected === false) {
    return erreur(c, a.error || 'terminal injoignable');
  }
  const demo = a.is_demo;
  setRows(c, [
    ['compte', `${a.login}  ${a.mode_label}`, demo ? 'ok' : 'bad'],
    ['serveur', a.server],
    ['balance', `${nombre(a.balance)} ${a.currency}`],
    ['equity', `${nombre(a.equity)} ${a.currency}`],
    ['marge libre', `${nombre(a.margin_free)} ${a.currency}`],
    ['trade_mode', a.trade_mode],
  ]);
}

function rendreVendeurs(v) {
  const c = $('#vendors');
  if (v.error) return erreur(c, v.error);
  const t = el('table', 'kv');
  for (const [cat, chaine] of Object.entries(v.chains || {})) {
    const tr = el('tr');
    const td = el('td');
    chaine.forEach((nom, i) => {
      if (i) td.append(el('span', 'faint', ' → '));
      td.append(el('span', i === 0 ? 'ok' : 'dim', nom));
    });
    tr.append(el('td', null, cat), td);
    t.append(tr);
  }
  c.replaceChildren(t);
}

const NOMS_PILIERS = {
  data_valid: 'D', trend_sr: '1', fair_value: '2',
  liquidity: '3', ote_ob: '4', candle_confirmed: '5',
};

function rendreBalayage(d) {
  const c = $('#scan');
  if (d.error) return erreur(c, d.error);
  if (!d.rows || !d.rows.length) return vide(c, 'aucun résultat');

  $('#scan-count').textContent = `${d.entries} ENTER / ${d.rows.length}`;
  $('#scan-meta').textContent =
    `${d.mode} · ${d.ltf}/${d.htf} · ${nombre(d.equity)} ${d.currency || ''}`;

  const t = el('table', 'grid');
  const thead = el('thead');
  const htr = el('tr');
  ['Symbole', 'Piliers', 'Verdict', 'Sens', 'Famille', 'Risque', 'Edge', 'Motif']
    .forEach((h) => htr.append(el('th', null, h)));
  thead.append(htr);
  t.append(thead);

  const tb = el('tbody');
  d.rows.forEach((r, i) => {
    const tr = el('tr', r.verdict === 'ENTER' ? 'enter' : '');
    tr.tabIndex = 0;

    tr.append(el('td', null, r.symbol));

    const tdP = el('td');
    const box = el('span', 'pillars');
    (r.pillars || []).forEach((p) => {
      const s = el('span', 'p' + (p.passed ? ' on' : ''), NOMS_PILIERS[p.name] || '?');
      s.title = `${p.name} — ${p.reason}`;
      box.append(s);
    });
    tdP.append(box);
    tr.append(tdP);

    const vcls = r.verdict === 'ENTER' ? 'ok' : (r.verdict === 'WAIT' ? 'warn' : 'dim');
    tr.append(el('td', vcls, r.verdict || '—'));
    tr.append(el('td', null, r.side === 1 ? 'LONG' : (r.side === -1 ? 'SHORT' : '—')));
    tr.append(el('td', 'dim', r.family || '—'));

    const rk = el('td', r.risk_money > 0 ? '' : 'faint',
      r.risk_money ? nombre(r.risk_money) : '—');
    if (r.risk_verdict) rk.title = `RiskGate : ${r.risk_verdict}`;
    tr.append(rk);

    const e = r.edge || {};
    const ecls = e.ok === true ? 'ok' : (e.ok === false ? 'bad' : 'faint');
    const etxt = e.ok === true ? `+${nombre(e.expectancy_r, 3)}R`
      : (e.ok === false ? `${nombre(e.expectancy_r, 3)}R` : 'inconnu');
    const tde = el('td', ecls, etxt);
    tde.title = e.reason || '';
    tr.append(tde);

    tr.append(el('td', 'dim', r.reason || r.code || '—'));

    const ouvrir = () => {
      tb.querySelectorAll('tr').forEach((x) => x.classList.remove('sel'));
      tr.classList.add('sel');
      rendreDetail(r);
    };
    tr.addEventListener('click', ouvrir);
    tr.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); ouvrir(); }
    });
    tb.append(tr);
  });
  t.append(tb);
  c.replaceChildren(t);
}

function rendreDetail(r) {
  $('#detail-section').hidden = false;
  $('#detail-sym').textContent = r.symbol;
  const c = $('#detail');
  c.replaceChildren();

  const grille = el('div');
  grille.style.display = 'grid';
  grille.style.gap = 'var(--s4)';
  grille.style.gridTemplateColumns = 'repeat(auto-fit, minmax(280px, 1fr))';

  const gauche = el('div');
  gauche.append(el('h3', null, 'Chaîne de décision'));
  gauche.querySelector('h3').style.cssText =
    'margin:0 0 var(--s2);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--txt-dim)';
  const ul = el('ul', 'trace');
  (r.trace || []).forEach((t) => ul.append(el('li', null, t)));
  if (!r.trace || !r.trace.length) ul.append(el('li', 'faint', 'aucune trace'));
  gauche.append(ul);

  const milieu = el('div');
  const n = r.notes || {};
  setRows(milieu, [
    ['prix', nombre(r.price, 5)],
    ['ATR', nombre(r.atr, 5)],
    ['tendance', r.trend === 1 ? 'haussière' : (r.trend === -1 ? 'baissière' : 'indéterminée')],
    ['stop', nombre(r.stop_distance, 5)],
    ['conviction', nombre(r.conviction)],
    ['S/R', n.sr],
    ['juste prix', n.fair_value],
    ['OTE', n.ote],
    ['bougies', (n.candles || []).join(', ') || '—'],
    ['en attente', (n.pending || []).join(', ') || '—'],
  ]);

  const droite = el('div');
  droite.append(el('h3', null, 'Piliers'));
  droite.querySelector('h3').style.cssText =
    'margin:0 0 var(--s2);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--txt-dim)';
  const lp = el('ul', 'list');
  (r.pillars || []).forEach((p) => {
    const li = el('li');
    li.append(
      el('span', p.passed ? 'ok' : 'faint', (p.passed ? '✓ ' : '· ') + p.name),
      el('span', 'right', p.reason),
    );
    lp.append(li);
  });
  droite.append(lp);

  grille.append(gauche, milieu, droite);
  c.append(grille);
}

function rendrePositions(p) {
  const c = $('#positions');
  if (p.error) return erreur(c, p.error);
  const lignes = p.positions || [];
  $('#pos-count').textContent = lignes.length
    ? `${lignes.length}`
    : `breakeven +${p.params?.breakeven_r}R · trailing +${p.params?.trail_start_r}R`;

  if (!lignes.length) {
    return vide(c, 'aucune position ouverte appartenant à V14');
  }
  const t = el('table', 'grid');
  const htr = el('tr');
  ['Ticket', 'Symbole', 'Sens', 'Lot', 'Entrée', 'SL', 'Phase', '+R', 'P&L']
    .forEach((h) => htr.append(el('th', null, h)));
  t.append(el('thead').appendChild(htr).parentNode);
  const tb = el('tbody');
  lignes.forEach((r) => {
    const tr = el('tr');
    [
      r.ticket, r.symbol, r.side === 1 ? 'LONG' : 'SHORT', nombre(r.volume),
      nombre(r.entry, 5), r.sl ? nombre(r.sl, 5) : '—', r.phase,
      r.fav_r === null ? '—' : nombre(r.fav_r, 2),
    ].forEach((v) => tr.append(el('td', null, v)));
    tr.append(el('td', r.profit >= 0 ? 'ok' : 'bad', nombre(r.profit)));
    tb.append(tr);
  });
  t.append(tb);
  c.replaceChildren(t);
}

function rendreEdge(e) {
  const c = $('#edge');
  if (e.error) return erreur(c, e.error);
  const ctx = e.contexts || [];
  $('#edge-count').textContent = `${e.total_trades || 0} trades · seuil ${e.min_samples}`;

  if (!ctx.length) {
    return vide(c,
      `Aucun trade clos. L'edge reste inconnu, donc le mode PROD bloque tout — `
      + `par conception. Il faut ${e.min_samples} trades par contexte pour un verdict.`);
  }
  const t = el('table', 'grid');
  const htr = el('tr');
  ['Contexte', 'Trades', 'Espérance', 'Réussite', 'Coût moyen', 'Coûts exacts', 'Verdict']
    .forEach((h) => htr.append(el('th', null, h)));
  t.append(el('thead').appendChild(htr).parentNode);
  const tb = el('tbody');
  ctx.forEach((v) => {
    const tr = el('tr');
    tr.append(el('td', null, v.context));
    tr.append(el('td', null, v.samples));
    tr.append(el('td', v.expectancy_r >= 0 ? 'ok' : 'bad',
      `${v.expectancy_r >= 0 ? '+' : ''}${nombre(v.expectancy_r, 3)}R`));
    tr.append(el('td', 'dim', `${nombre(v.win_rate * 100, 0)}%`));
    tr.append(el('td', 'dim', v.mean_cost_r == null ? 'inconnu' : nombre(v.mean_cost_r, 3)));
    tr.append(el('td', 'dim', `${nombre((v.exact_cost_rate || 0) * 100, 0)}%`));
    const cls = v.edge_ok === true ? 'ok' : (v.edge_ok === false ? 'bad' : 'faint');
    const td = el('td', cls,
      v.edge_ok === true ? 'porteur' : (v.edge_ok === false ? 'perdant' : 'inconnu'));
    td.title = v.reason || '';
    tr.append(td);
    tb.append(tr);
  });
  t.append(tb);
  c.replaceChildren(t);
}

function rendreBriques(bricks, tests) {
  const c = $('#bricks');
  $('#tests-count').textContent = tests && tests.total
    ? `${tests.total} tests · ${tests.echecs} échec(s)` : 'aucun relevé';

  const ul = el('ul', 'list');
  (bricks || []).forEach((b) => {
    const li = el('li');
    li.append(
      el('span', b.exists ? '' : 'faint', b.name),
      el('span', 'right ' + (b.tests ? 'ok' : 'faint'),
        b.tests ? `${b.tests} tests` : (b.exists ? 'livrée' : 'à faire')),
    );
    ul.append(li);
  });
  c.replaceChildren(ul);

  if (tests && tests.quand) {
    const p = el('div', 'faint', `relevé du ${tests.quand} — `
      + `${tests.titanium} titanium / ${tests.socle} socle`);
    p.style.cssText = 'margin-top:var(--s3);font-size:12px';
    c.append(p);
  }
}

function rendreRuns(runs) {
  const c = $('#runs');
  $('#runs-count').textContent = runs.length || '';
  if (!runs.length) return vide(c, 'aucune analyse multi-agents enregistrée');

  const ul = el('ul', 'list');
  runs.forEach((r, i) => {
    const li = el('li');
    li.style.cursor = 'pointer';
    li.tabIndex = 0;
    const g = el('span');
    g.append(el('span', null, `${r.ticker} `), el('span', 'dim', r.date));
    if (r.has_debate) g.append(el('span', 'tag ok', ' débat'));
    if (r.has_risk) g.append(el('span', 'tag warn', ' risque'));
    li.append(g, el('span', 'right', new Date(r.at).toLocaleString('fr-FR')));

    const detail = el('pre', 'report');
    detail.hidden = true;
    detail.textContent = r.decision || '(aucune décision enregistrée)';

    const bascule = () => { detail.hidden = !detail.hidden; };
    li.addEventListener('click', bascule);
    li.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); bascule(); }
    });
    ul.append(li, detail);
  });
  c.replaceChildren(ul);
}


function rendreBoucle(l) {
  const c = $('#loop');
  if (l.error) return erreur(c, l.error);
  /* Une boucle armee ET tournante est l'etat ou des ordres partent : on le
     signale en rouge, comme le mur. Le vert dirait "tout va bien" alors que
     c'est l'etat le plus engageant. */
  const actif = l.running && l.armed;
  const s = l.stats || {};
  const tunnel = s.tunnel || {};
  const flow = tunnel.flow || {};
  const supports = tunnel.support_passed || {};
  const codes = tunnel.gate_code || {};
  const etat = l.running ? 'en cours' : (l.stale ? 'FIGÉE' : 'arrêtée');
  const cls = l.running ? 'ok' : (l.stale ? 'bad' : 'faint');
  const rows = [
    ['processus', etat, cls],
    ['dernier battement', l.age_s == null ? '—' : `il y a ${nombre(l.age_s, 0)} s`,
      l.stale ? 'bad' : 'dim'],
    ['exécution', l.armed ? 'ARMÉE' : 'désarmée', l.armed ? 'bad' : 'ok'],
    ['activité', s.tours ? `${s.tours} tours · ${s.enter || 0} ENTER · ${s.envoyes || 0} ordre(s)` : '—'],
    ['plafonds', `${l.max_positions} positions · ${l.max_per_symbol}/actif`],
    ['gestion', `breakeven +${l.breakeven_r} R · trailing dès +${l.trail_start_r} R`],
  ];
  if (Object.keys(flow).length) {
    rows.push(['entonnoir', `${flow.catalogue || 0} catalogue → ${flow.selectionnes || 0} sélectionnés → ${flow.portables || 0} portables → ${s.evalues || 0} évalués`, 'dim']);
  }
  if (Object.keys(supports).length) {
    rows.push(['piliers', Object.entries(supports).sort().map(([k, v]) => `${k}:${v}`).join(' · '), 'dim']);
  }
  if (Object.keys(codes).length) {
    const principaux = Object.entries(codes).sort((a, b) => b[1] - a[1]).slice(0, 3);
    rows.push(['refus principaux', principaux.map(([k, v]) => `${k}:${v}`).join(' · '), 'dim']);
  }
  setRows(c, rows);
  if (l.reason) {
    const n = el('div', l.stale ? 'err' : 'faint', l.reason);
    n.style.cssText = 'margin-top:var(--s3);font-size:12px';
    c.append(n);
  }
  const p = $('#pill-loop');
  p.textContent = l.stale ? 'boucle FIGÉE'
    : (l.running ? (l.armed ? 'boucle ARMÉE' : 'boucle observation') : 'boucle arrêtée');
  p.className = 'pill ' + (l.stale ? 'alert' : (actif ? 'alert' : (l.running ? 'on' : 'off')));
}

function rendreRisque(r) {
  const c = $('#risque');
  if (!r || r.disponible === false) {
    return setRows(c, [['risque engagé', 'indisponible', 'faint']]);
  }
  /* C'est CE chiffre qui borne l'exposition, pas le nombre de positions :
     huit positions a 1.75 % feraient 14 %, dont une bonne part sur le meme
     pari. On l'affiche donc avant tout le reste. */
  const occ = r.occupation || 0;
  const cls = occ >= 90 ? 'bad' : (occ >= 60 ? 'warn' : 'ok');
  const e = r.echelle || {};
  setRows(c, [
    ['risque engagé', `${nombre(r.engage_pct, 2)} % sur ${nombre(r.budget_pct, 0)} %`, cls],
    ['occupation', `${nombre(occ, 0)} % du budget`, cls],
    ['positions', String(r.positions ?? 0)],
    ['échelle par setup', `${e.plancher} % → ${e.plafond_modulation} % selon les piliers`, 'dim'],
    ['plafond dur', `${e.plafond_dur} % — jamais franchi`, 'dim'],
  ]);
  const b = el('div', 'bar');
  b.style.cssText = 'margin-top:var(--s3);height:6px;border-radius:3px;background:#1e293b;overflow:hidden';
  const f = el('div');
  f.style.cssText = `height:100%;width:${Math.min(100, occ)}%;background:var(--${cls === 'bad' ? 'bad' : cls === 'warn' ? 'warn' : 'ok'})`;
  b.append(f); c.append(b);
  $('#risque-count').textContent = `${nombre(r.engage_pct, 1)} %`;
}

function rendreAnalystes(a) {
  const c = $('#analystes');
  if (!a || !a.actif) {
    setRows(c, [['flux', 'aucune demande déposée', 'faint']]);
    $('#avis-count').textContent = '';
    return;
  }
  setRows(c, [
    ['demandes', String(a.n_demandes ?? 0)],
    ['avis rendus', String(a.n_avis ?? 0), a.n_avis ? 'ok' : 'faint'],
    ['en attente', String(a.en_attente ?? 0), a.en_attente > 5 ? 'warn' : 'dim'],
  ]);
  const recents = a.recents || [];
  if (recents.length) {
    const t = el('div', 'mini');
    t.style.cssText = 'margin-top:var(--s3);display:grid;gap:4px;font-size:12px';
    recents.slice(0, 6).forEach(x => {
      const l = el('div');
      l.style.cssText = 'display:flex;gap:8px;justify-content:space-between';
      const g = el('span', 'dim', x.symbol);
      /* Un desaccord de direction ABAISSE la taille, il n'annule rien.
         On le signale sans le dramatiser. */
      const note = x.accord === false ? 'warn' : (x.accord ? 'ok' : 'faint');
      const d = el('span', note,
        `${x.rating || 'sans note'} · ${nombre(x.conviction, 2)}`);
      l.append(g, d); t.append(l);
    });
    c.append(t);
  }
  $('#avis-count').textContent = String(a.n_avis ?? 0);
}

function rendreFantome(f) {
  const c = $('#fantome');
  if (!f || !f.actif) {
    setRows(c, [['observation', 'aucune donnée', 'faint']]);
    $('#fantome-count').textContent = '';
    return;
  }
  /* Repond a la question qui n'avait aucune reponse : a quelle frequence le
     quorum strict se declenche-t-il ? Sans ce chiffre, tout calendrier
     d'ouverture du mode PROD est une invention. */
  const entre = f.prod_aurait_entre || 0;
  setRows(c, [
    ['observations', String(f.lignes ?? 0)],
    ['PROD aurait entré', String(entre), entre ? 'ok' : 'warn'],
    ['EXPLORE seul', String(f.explore_seul ?? 0), 'dim'],
  ]);
  const motifs = f.motifs_de_refus || {};
  const cles = Object.keys(motifs);
  if (cles.length) {
    const t = el('div', 'mini');
    t.style.cssText = 'margin-top:var(--s3);display:grid;gap:4px;font-size:12px';
    cles.slice(0, 4).forEach(k => {
      const l = el('div');
      l.style.cssText = 'display:flex;gap:8px;justify-content:space-between';
      l.append(el('span', 'faint', k), el('span', 'dim', String(motifs[k])));
      t.append(l);
    });
    c.append(t);
  }
  $('#fantome-count').textContent = entre ? `${entre} ENTER` : '0 ENTER';
}

function rendrePont(b, f) {
  const c = $('#bridge');
  if (b.error) return erreur(c, b.error);
  if (!b.available) return vide(c, b.reason || 'terminal MT5 introuvable');

  const lignes = [
    ['indicateur', b.indicator_installed ? 'installé' : 'absent',
      b.indicator_installed ? 'ok' : 'bad'],
    ['compilé', b.indicator_compiled ? 'oui' : 'non — faire F7 dans MetaEditor',
      b.indicator_compiled ? 'ok' : 'warn'],
  ];
  if (b.payload_exists) {
    lignes.push(
      ['dernier envoi', `${b.symbol || '—'}  ${b.verdict || ''}`],
      ['piliers', b.pillars_line || '—'],
      ['zones', (b.zones || []).join(', ') || '—'],
      ['R:R', b.rr == null ? '—' : nombre(b.rr)],
    );
  } else {
    lignes.push(['charge', 'aucune zone envoyée', 'faint']);
  }
  /* Les fenetres MT5 : ChartOpen n'existe que cote MQL5, V14 ne fait
     qu'ecrire la liste voulue. Savoir OU elle est ecrite compte — deux
     dossiers de terminal coexistent sur cette machine, et ecrire dans le
     mauvais ne produit aucune erreur, juste un ecran vide. */
  if (f && f.disponible) {
    const syms = f.symboles || [];
    lignes.push(['fenêtres demandées',
      syms.length ? `${syms.length}/${f.maxi} · ${syms.slice(0, 4).join(' ')}` : 'aucune',
      syms.length ? 'ok' : 'faint']);
  } else if (f) {
    lignes.push(['fenêtres', 'dossier MQL5 introuvable', 'warn']);
  }
  setRows(c, lignes);
}

function rendreDiscriminants(d) {
  const c = $('#discriminants');
  if (d.error) return erreur(c, d.error);
  $('#disc-count').textContent = d.n_trades
    ? `${d.n_gagnants || 0}G / ${d.n_perdants || 0}P` : 'aucune donnée';

  if (!d.suffisant || !(d.top || []).length) {
    return vide(c, d.message || 'échantillon insuffisant');
  }
  const t = el('table', 'grid');
  const htr = el('tr');
  ['Indicateur', 'Delta', 'p corrigé', 'Effet', 'Retenu']
    .forEach((h) => htr.append(el('th', null, h)));
  t.append(el('thead').appendChild(htr).parentNode);
  const tb = el('tbody');
  (d.top || []).forEach((x) => {
    const tr = el('tr');
    tr.append(el('td', null, x.nom));
    tr.append(el('td', x.delta >= 0 ? 'ok' : 'bad',
      `${x.delta >= 0 ? '+' : ''}${nombre(x.delta, 3)}`));
    tr.append(el('td', 'dim', nombre(x.p_corrige, 4)));
    tr.append(el('td', 'dim', x.taille));
    tr.append(el('td', x.retenu ? 'ok' : 'faint', x.retenu ? 'OUI' : 'non'));
    tb.append(tr);
  });
  t.append(tb);
  c.replaceChildren(t);
  const note = el('div', 'faint', d.message);
  note.style.cssText = 'padding:var(--s3) var(--s4);font-size:12px';
  c.append(note);
}

/* ─────────────────────────────── données ────────────────────────────────── */

async function chargerEtat() {
  $('#meta-time').textContent = 'chargement…';
  try {
    const r = await fetch('/api/state');
    const d = await r.json();
    rendreMur(d.wall || {});
    rendreCompte(d.account || {});
    rendreVendeurs(d.vendors || {});
    rendrePositions(d.positions || {});
    rendreEdge(d.edge || {});
    rendreBriques(d.bricks || [], d.tests || {});
    rendreRuns(d.runs || []);
    rendreBoucle(d.loop || {});
    rendrePont(d.bridge || {}, d.fenetres || {});
    rendreDiscriminants(d.discriminants || {});
    rendreRisque(d.risque || {});
    rendreAnalystes(d.analystes || {});
    rendreFantome(d.prod_fantome || {});
    const m = d.meta || {};
    $('#meta-llm').textContent = `${m.provider || '?'} · ${m.deep_model || '?'} / ${m.quick_model || '?'}`;
    $('#meta-time').textContent = new Date().toLocaleTimeString('fr-FR');
  } catch (e) {
    $('#meta-time').textContent = 'serveur injoignable';
    erreur($('#wall'), String(e));
  }
}

async function lancerBalayage() {
  const btn = $('#btn-scan');
  btn.disabled = true;
  $('#scan-bar').hidden = false;
  $('#scan-meta').textContent = 'balayage en cours…';
  try {
    const syms = encodeURIComponent($('#symbols').value.trim());
    const prod = $('#prod').checked ? '1' : '0';
    const r = await fetch(`/api/scan?symbols=${syms}&prod=${prod}`);
    rendreBalayage(await r.json());
  } catch (e) {
    erreur($('#scan'), String(e));
    $('#scan-meta').textContent = '';
  } finally {
    btn.disabled = false;
    $('#scan-bar').hidden = true;
  }
}

$('#btn-refresh').addEventListener('click', chargerEtat);
$('#btn-scan').addEventListener('click', lancerBalayage);
$('#symbols').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') lancerBalayage();
});

document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.ctrlKey || e.altKey || e.metaKey) return;
  const k = e.key.toLowerCase();
  if (k === 'r') { e.preventDefault(); chargerEtat(); }
  if (k === 's') { e.preventDefault(); lancerBalayage(); }
  if (k === 'p') { e.preventDefault(); $('#prod').checked = !$('#prod').checked; }
  if (e.key === 'Escape') { $('#detail-section').hidden = true; }
});

chargerEtat();
/* L'état se rafraîchit seul ; le balayage reste manuel — il lit MT5 et
   monopoliserait le verrou s'il tournait en boucle. */
setInterval(chargerEtat, 10000);
