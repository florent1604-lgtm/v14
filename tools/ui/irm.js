/* IRM V14 — client du flux vivant.
 *
 * Un seul EventSource sur /api/direct. EventSource se reconnecte tout seul si
 * le serveur redémarre : on ne réimplémente donc aucune boucle de reprise, on
 * se contente de refléter l'état de la connexion dans le bandeau vital.
 *
 * Aucune dépendance, aucun build. La page doit s'ouvrir sur un poste hors
 * ligne, exactement comme le tableau de bord.
 */

'use strict';

const $ = (id) => document.getElementById(id);

/* Clés vues au moins une fois, pour ne pas rejouer l'animation « neuf » sur
 * des évènements déjà affichés à chaque poussée du serveur. */
const dejaVu = new Set();
let filtreOrgane = null;
let organesConnus = [];

const ETATS = {
  en_cours: { nom: 'BOUCLE EN COURS', detail: (p) => `battement il y a ${fmtSec(p.age_s)} · intervalle ${fmtSec(p.intervalle_s)}` },
  figee:    { nom: 'BOUCLE FIGÉE',    detail: (p) => `dernier battement il y a ${fmtSec(p.age_s)} — plus de 3 tours manqués` },
  arretee:  { nom: 'BOUCLE ARRÊTÉE',  detail: () => 'aucun battement lisible' },
};

function fmtSec(s) {
  if (s === null || s === undefined) return '—';
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}

function fmtNombre(n) {
  if (n === null || n === undefined) return '—';
  return Number(n).toLocaleString('fr-FR');
}

function heure(iso) {
  if (!iso) return '--:--:--';
  const t = String(iso);
  const i = t.indexOf('T');
  return i < 0 ? t.slice(0, 8) : t.slice(i + 1, i + 9);
}

/* ── Bandeau vital ─────────────────────────────────────────────────────── */

function rendrePouls(p) {
  const etat = ETATS[p.etat] || ETATS.arretee;
  $('pastille').className = `pastille ${p.etat}`;
  $('etat-nom').textContent = etat.nom;
  $('etat-detail').textContent = etat.detail(p);

  $('v-capital').textContent = p.capital === null || p.capital === undefined
    ? '—' : `${Number(p.capital).toFixed(2)} €`;

  /* L'armement est écrit en toutes lettres, pas seulement coloré : c'est
   * l'information la plus lourde de la page. */
  $('v-arme').textContent = p.arme ? 'ARMÉE' : 'désarmée';
  $('v-arme').style.color = p.arme ? 'var(--alerte)' : 'var(--txt-faint)';

  $('v-tours').textContent = fmtNombre(p.tours);
  $('v-envoyes').textContent = fmtNombre(p.envoyes);
  $('v-positions').textContent = p.positions.length;

  rendrePositions(p.positions);
}

function rendrePositions(positions) {
  const cible = $('positions');
  if (!positions.length) {
    cible.innerHTML = '<li class="vide">aucune position ouverte</li>';
    return;
  }
  cible.innerHTML = positions.map((p) => {
    const sens = p.sens > 0 ? 'long' : (p.sens < 0 ? 'short' : '—');
    /* Le pic est la seule grandeur en R que l'état porte réellement. Le
     * résultat courant demanderait le prix vivant, donc le verrou MT5 —
     * l'IRM ne le prend pas, et n'affiche donc pas un chiffre qu'elle
     * n'a pas mesuré. */
    const arme = p.pic_r >= 0.8;
    return `<li>
      <div class="p-tete">
        <span class="p-sym">${esc(p.symbole)}</span>
        <span class="f-sens ${sens}">${sens.toUpperCase()}</span>
      </div>
      <div class="p-r ${arme ? 'gain' : ''}">pic +${p.pic_r.toFixed(2)} R</div>
      <div class="p-meta">${esc(p.contexte)} · phase ${esc(p.phase || '—')} · ticket ${esc(p.ticket)}</div>
      <div class="p-pic">1 R = ${p.unite_r} en prix${arme ? ' · breakeven armé' : ''}</div>
    </li>`;
  }).join('');
}

/* ── La chaîne d'organes ───────────────────────────────────────────────── */

function rendreOrganes(charge) {
  const chaine = charge.chaine || [];
  organesConnus = chaine.map((o) => ({ cle: o.cle, libelle: o.libelle }));

  $('chaine').innerHTML = chaine.map((o, i) => {
    let debit;
    if (o.entre === null || o.sort === null) {
      debit = '<div class="organe-debit"><span class="debit-vide">non compté par la boucle</span></div>';
    } else {
      debit = `<div class="organe-debit">
        <span class="debit-entre">${fmtNombre(o.entre)}</span>
        <span class="debit-fleche">→</span>
        <span class="debit-sort">${fmtNombre(o.sort)}</span>
      </div>`;
    }

    let barre = '';
    if (o.entre) {
      const perte = Math.min(100, Math.max(0, (o.perdus / o.entre) * 100));
      barre = `<div class="attrition" role="img"
        aria-label="${perte.toFixed(0)} % retenus par cet organe">
        <span style="width:${perte.toFixed(1)}%"></span></div>`;
    }

    const detail = (o.detail || []).slice(0, 4).map(
      (d) => `<li><span>${esc(d.code)}</span><b>${fmtNombre(d.n)}</b></li>`).join('');

    return `<li class="organe" data-organe="${o.cle}">
      <span class="organe-rang">${String(i + 1).padStart(2, '0')}</span>
      <div>
        <div class="organe-nom">${esc(o.libelle)}</div>
        <div class="organe-sous">${esc(o.soustitre)}</div>
      </div>
      ${debit}
      ${barre}
      ${detail ? `<ul class="organe-detail">${detail}</ul>` : ''}
    </li>`;
  }).join('');

  rendreSupports(charge.supports || {});
  rendreFiltres();
}

function rendreSupports(supports) {
  const entrees = Object.entries(supports)
    .sort((a, b) => a[0].localeCompare(b[0]));
  if (!entrees.length) {
    $('supports').innerHTML = '<li class="vide">aucun relevé</li>';
    return;
  }
  const max = Math.max(...entrees.map((e) => e[1]));
  $('supports').innerHTML = entrees.map(([nom, n]) => {
    const niveau = Number(String(nom).replace(/\D/g, '')) || 0;
    /* Le quorum EXPLORE est à 2 piliers : au-dessus, la barre est accentuée. */
    const quorum = niveau >= 2 ? ' quorum' : '';
    const part = max ? (n / max) * 100 : 0;
    return `<li>
      <span class="s-nom">${esc(nom)}</span>
      <span class="s-barre"><span class="${quorum.trim()}" style="width:${part.toFixed(1)}%"></span></span>
      <span class="s-n">${fmtNombre(n)}</span>
    </li>`;
  }).join('');
}

/* ── Flux ──────────────────────────────────────────────────────────────── */

function rendreFiltres() {
  const cible = $('filtres');
  if (cible.dataset.pret === '1') return;
  cible.dataset.pret = '1';

  const boutons = [{ cle: null, libelle: 'tout' }].concat(organesConnus);
  cible.innerHTML = boutons.map((b) => `
    <button type="button" data-cle="${b.cle === null ? '' : b.cle}"
      aria-pressed="${b.cle === filtreOrgane}">${esc(b.libelle)}</button>`).join('');

  cible.addEventListener('click', (ev) => {
    const bouton = ev.target.closest('button');
    if (!bouton) return;
    filtreOrgane = bouton.dataset.cle || null;
    cible.querySelectorAll('button').forEach((b) => {
      b.setAttribute('aria-pressed', String((b.dataset.cle || null) === filtreOrgane));
    });
    appliquerFiltre();
  });
}

function appliquerFiltre() {
  $('flux').querySelectorAll('li').forEach((li) => {
    li.hidden = filtreOrgane !== null && li.dataset.organe !== filtreOrgane;
  });
}

function rendreFlux(evenements) {
  const cible = $('flux');
  if (!evenements.length && !cible.children.length) {
    cible.innerHTML = '<li class="vide">aucun évènement — la boucle n\'écrit rien en ce moment</li>';
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const e of evenements) {
    const cle = `${e.a}|${e.organe}|${e.symbole}|${e.titre}`;
    if (dejaVu.has(cle)) continue;
    dejaVu.add(cle);

    const li = document.createElement('li');
    li.dataset.organe = e.organe;
    li.className = 'neuf';
    li.hidden = filtreOrgane !== null && e.organe !== filtreOrgane;

    const sens = e.sens > 0 ? 'long' : (e.sens < 0 ? 'short' : '');
    const libelle = (organesConnus.find((o) => o.cle === e.organe) || {}).libelle || e.organe;

    li.innerHTML = `
      <span class="f-heure">${heure(e.a)}</span>
      <span class="f-sym">${esc(e.symbole)}${sens ? ` <span class="f-sens ${sens}">${sens === 'long' ? '▲' : '▼'}</span>` : ''}</span>
      <span class="f-corps">
        <span class="f-titre"><span class="f-organe">${esc(libelle)}</span>${esc(e.titre)}</span>
        ${e.detail ? `<span class="f-detail">${esc(e.detail)}</span>` : ''}
      </span>`;
    fragment.appendChild(li);

    marquerOrgane(e.organe);
  }

  /* Le conteneur est en `column-reverse` : ajouter à la fin place en haut. */
  if (fragment.childElementCount) {
    const vide = cible.querySelector('.vide');
    if (vide) vide.remove();
    cible.appendChild(fragment);
  }

  /* Le flux est vivant : sans plafond, l'onglet grossit jusqu'à ramer. */
  while (cible.children.length > 400) cible.firstElementChild.remove();
  if (dejaVu.size > 4000) dejaVu.clear();
}

const minuteries = new Map();

function marquerOrgane(cle) {
  const carte = document.querySelector(`.organe[data-organe="${cle}"]`);
  if (!carte) return;
  carte.classList.add('actif');
  clearTimeout(minuteries.get(cle));
  minuteries.set(cle, setTimeout(() => carte.classList.remove('actif'), 2600));
}

function esc(v) {
  return String(v === null || v === undefined ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/* ── Connexion ─────────────────────────────────────────────────────────── */

function connecter() {
  const flux = new EventSource('/api/direct');

  flux.onmessage = (ev) => {
    let charge;
    try {
      charge = JSON.parse(ev.data);
    } catch (err) {
      return;   /* poussée tronquée : la suivante arrive dans 2 s */
    }
    rendrePouls(charge.pouls);
    rendreOrganes(charge.organes);
    rendreFlux(charge.evenements);
    if (charge.cortex) rendreCortex(charge.cortex);
    if (charge.memoire) rendreMemoire(charge.memoire);
    if (charge.injections) rendreInjections(charge.injections);
    if (charge.revues) rendreRevues(charge.revues);
  };

  flux.onerror = () => {
    /* EventSource retente seul. On le dit au lieu de laisser un état figé
     * passer pour un état frais — un tableau qui ment est pire qu'un vide. */
    $('pastille').className = 'pastille arretee';
    $('etat-nom').textContent = 'FLUX INTERROMPU';
    $('etat-detail').textContent = 'reconnexion automatique…';
  };
}

connecter();

/* ── Réflexion du cortex ───────────────────────────────────────────────── */

const SANTE_CORTEX = {
  hermes: (d) => `Hermès actif · ${d}`,
  repli:  (d) => `repli local · ${d}`,
  muet:   ()  => 'aucun verdict',
};

function rendreCortex(c) {
  const badge = $('cortex-sante');
  badge.className = `etiquette ${c.sante}`;
  badge.textContent = (SANTE_CORTEX[c.sante] || SANTE_CORTEX.muet)(c.detail)
    + (c.age_s !== null && c.age_s !== undefined ? ` · il y a ${fmtSec(c.age_s)}` : '');

  const cible = $('cortex-verdicts');
  if (!c.verdicts.length) {
    cible.innerHTML = '<li class="vide">le cortex n\'a encore rien jugé</li>';
    return;
  }
  cible.innerHTML = c.verdicts.map((v) => {
    const sens = v.sens > 0 ? 'long' : (v.sens < 0 ? 'short' : '');
    const conv = (v.conviction === null || v.conviction === undefined)
      ? '—' : Number(v.conviction).toFixed(2);
    /* Le modèle est affiché sur chaque ligne : un verdict d'Hermès et un
     * verdict du repli local ne valent pas la même chose, et les confondre
     * fausserait toute comparaison ultérieure. */
    const preuves = (v.preuves || []).map((p) => `<span class="puce">${esc(p)}</span>`).join('');
    /* Le PRODUCTEUR, pas le model_version : jusqu'au 05/09/2026 ce dernier
     * recopiait le modele local meme quand Hermes avait juge, parce que
     * l'identite est scellee avant de savoir quel cortex repondra. Le
     * producteur, lui, a toujours ete exact. */
    const cortex = (v.producteur || '').replace(/^hermes-cortex\//, '') || v.modele || '?';
    return `<li data-verdict="${esc(v.verdict)}">
      <span class="v-tete">
        <span class="v-sym">${esc(v.symbole)}${sens ? ` <span class="f-sens ${sens}">${sens === 'long' ? '▲' : '▼'}</span>` : ''}</span>
        <span class="v-heure">${heure(v.a)}</span>
      </span>
      <span class="v-note">
        <span class="v-verdict ${esc(v.verdict)}">${esc(v.verdict)}</span>
        <span class="v-conv">conviction ${conv}</span>
      </span>
      <span class="v-corps">
        <span class="v-raison">${esc(v.raisonnement) || '<i>sans raisonnement</i>'}</span>
        <span class="v-preuves">${preuves}<span class="puce modele">${esc(cortex)}</span></span>
      </span>
    </li>`;
  }).join('');
}

/* ── Mémoire historique ────────────────────────────────────────────────── */

function rendreMemoire(m) {
  $('memoire-compte').textContent =
    `${m.n_rentables} rentable${m.n_rentables > 1 ? 's' : ''} sur ${m.n_total} contexte${m.n_total > 1 ? 's' : ''}`;
  $('memoire-compte').className = 'etiquette' + (m.n_rentables ? ' hermes' : '');

  const cible = $('memoire');
  if (!m.contextes.length) {
    cible.innerHTML = '<li class="vide">aucun contexte évalué en ce moment</li>';
    return;
  }
  const entete = `<li class="memoire-entete" aria-hidden="true">
      <span>contexte</span><span>verdict</span><span>espérance</span><span>PF</span><span>clôtures</span>
    </li>`;
  cible.innerHTML = entete + m.contextes.map((x) => {
    const rentable = x.action === 'ALLOW';
    const e = Number(x.esperance_r);
    const pf = (x.profit_factor === null || x.profit_factor === undefined)
      ? '—' : Number(x.profit_factor).toFixed(2);
    return `<li class="${rentable ? 'rentable' : ''}" title="${esc(x.motif)}">
      <span class="m-ctx">${esc(x.contexte)}</span>
      <span class="m-act ${esc(x.action)}">${esc(x.action)}</span>
      <span class="m-chiffre ${e >= 0 ? 'gain' : 'perte'}">${e >= 0 ? '+' : ''}${e.toFixed(3)}<small> R</small></span>
      <span class="m-chiffre ${Number(x.profit_factor) >= 1 ? 'gain' : 'perte'}">${pf}</span>
      <span class="m-chiffre"><small>n=</small>${fmtNombre(x.echantillons)}</span>
    </li>`;
  }).join('');
}

/* ── Injections de données ─────────────────────────────────────────────── */

function rendreInjections(sources) {
  const cible = $('injections');
  if (!sources.length) {
    cible.innerHTML = '<li class="vide">aucune source relevée</li>';
    return;
  }
  const max = Math.max(1, ...sources.map((s) => s.n));
  cible.innerHTML = sources.map((s) => {
    const morte = s.n === 0;
    const part = (s.n / max) * 100;
    return `<li>
      <span class="i-nom ${morte ? 'morte' : ''}">${esc(s.nom)}</span>
      <span class="i-barre" role="img" aria-label="${s.n} citation${s.n > 1 ? 's' : ''}">
        <span class="${morte ? 'vide' : ''}" style="width:${part.toFixed(1)}%"></span></span>
      <span class="i-n">${morte ? 'muette' : fmtNombre(s.n)}</span>
    </li>`;
  }).join('');
}

/* ── Revue des positions ouvertes ──────────────────────────────────────── */

function rendreRevues(revues) {
  const cible = $('revues');
  if (!revues.length) {
    cible.innerHTML = '<li class="vide">aucune revue rendue</li>';
    return;
  }
  cible.innerHTML = revues.map((r) => {
    const conf = (r.confiance === null || r.confiance === undefined)
      ? '—' : Number(r.confiance).toFixed(2);
    return `<li data-etat="${esc(r.etat)}">
      <span class="r-tete">
        <span class="r-sym">${esc(r.symbole)}</span>
        <span class="r-etat ${esc(r.etat)}">${esc(r.etat)}</span>
        <span class="r-conf">confiance ${conf} · ${heure(r.a)}</span>
      </span>
      <span class="r-raison">${esc(r.raisonnement)}</span>
    </li>`;
  }).join('');
}
