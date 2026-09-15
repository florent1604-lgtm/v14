# Étude de conception — application Windows locale Titanium V14

**Date :** 28 août 2026  
**Statut :** recherche et conception ; aucune modification du runtime  
**Cible :** application locale Windows, fluide, interactive, complète et visuellement originale  
**Invariants :** PAPER/DEMO ONLY, aucun secret exposé, aucun ordre ou changement de risque déclenché par l’interface sans autorisation humaine explicite

## 1. Verdict exécutif

La cible recommandée est une **application desktop Tauri 2 + React/TypeScript**, avec le moteur Python V14 conservé comme **sidecar local** et une API de télémétrie typée. Cette voie permet de transformer l’actuel dashboard web en véritable application Windows sans réécrire les organes de trading.

La surface primaire est un **MONITOR** : elle sert à voir l’état du corps, du marché et des décisions. Les surfaces secondaires sont **COMMAND / INSPECT** pour explorer un actif, une décision ou une anomalie, et **OPERATE** uniquement pour les actions locales non critiques explicitement autorisées (par exemple gérer une tâche de collaboration). Ce n’est ni une page marketing, ni une grille de cartes décoratives.

L’identité visuelle proposée est **Titanium OS / ORBE** : graphite presque noir, métal froid, lumière cyan bioélectrique, couleurs sémantiques strictes, typographie Windows soignée, tracés organiques et profondeur par luminance — sans gradient bleu-violet générique, sans glassmorphism systématique et sans accumulation d’icônes artificielles.

### Recommandation en une ligne

```text
Tauri 2 (coquille Windows) + React/TypeScript (UI) + sidecar Python V14
+ REST pour les snapshots + WebSocket/SSE pour les événements
+ Lightweight Charts pour le marché + ECharts/Cytoscape pour organes et statistiques.
```

## 2. Ce qui existe déjà et doit être préservé

V14 possède déjà un socle d’interface utile :

- serveur local lié à `127.0.0.1`, CSP stricte et routes séparées dans `tools/dashboard.py:1-22`, `tools/dashboard.py:97-117` ;
- état agrégé sans effet d’ordre dans `titanium/web/state.py:1-10` ;
- mur DEMO/réel, compte, vendeurs, tests, runs, edge, positions, pont MQL5, boucle, analystes, risque et promotion dans `titanium/web/state.py:565-584` ;
- graphiques synchronisant bougies, zones, plan et verdict dans `titanium/web/state.py:685-783` ;
- anatomie mesurée des organes et artères dans `titanium/web/medecin.py:39-76`, `titanium/web/medecin.py:117-295` ;
- cartographie syntaxique du code, couverture et faiblesses dans `titanium/web/carte.py:1-27`, `titanium/web/carte.py:198-237` ;
- gouvernance multi-agent, tâches, services et preuves dans `titanium/web/command_center.py:122-137`, `titanium/web/command_center.py:232-257` ;
- interface actuelle déjà pensée comme poste de contrôle, avec vitaux, anomalies, anatomie, actif, positions, promotion et registres dans `tools/ui/poste.html:7-174`.

Le nouveau produit ne doit donc pas repartir de zéro. Il doit :

1. conserver Python comme autorité métier ;
2. remplacer la page longue par un shell desktop multi-espaces ;
3. rendre les données temps réel et navigables ;
4. exposer l’intégralité des organes avec provenance et fraîcheur ;
5. renforcer la sécurité entre présentation, contrôle et exécution.

## 3. Audit de l’interface actuelle

### Forces

- Beaucoup de données réelles, peu de métriques inventées.
- Les anomalies remontent avant les détails.
- Les couleurs ont déjà une signification opérationnelle.
- Le graphique d’actif regroupe prix, zones et plan dans le même instant causal.
- La page distingue présence d’un module, santé du flux et couverture de tests.
- Les règles de décision restent côté Python ; le JavaScript ne duplique pas les seuils métier (`tools/ui/poste.js:1-7`).

### Limites

- La page est très longue : l’utilisateur parcourt une succession de blocs plutôt qu’un espace de travail.
- Les éléments importants et secondaires partagent trop souvent le même poids visuel.
- La grille de panneaux donne une impression d’outil d’administration générique.
- La navigation entre état global, actif, décision, organe et preuve n’est pas suffisamment relationnelle.
- Le canvas financier est artisanal : peu de zoom, curseur croisé, synchronisation multi-timeframe ou annotations interactives.
- Le polling périodique (`tools/ui/poste.js:1022-1037`) peut être remplacé par un flux événementiel et des invalidations ciblées.
- Le graphe organique est informatif, mais il doit devenir un véritable point d’entrée vers chaque organe, artère, alerte et preuve.
- Les interactions clavier, panneaux redimensionnables, vues sauvegardées, recherche transversale et historique/replay manquent.

### Diagnostic anti-interface-IA

L’actuel dashboard reste sérieux, mais certains signes peuvent évoquer une interface générée : nombreuses cartes de même poids, labels très techniques partout, teintes néon sur fond sombre et hiérarchie surtout obtenue par des boîtes. La nouvelle conception doit réduire ce signal par la **composition**, pas par un simple changement de couleurs.

Score de « slop » estimé de l’existant : **3/10**.

- grille de tuiles de poids trop uniforme ;
- typographie technique utilisée presque partout ;
- composition de dashboard conventionnelle.

La cible doit viser **0–1/10** : une seule composition de poste de contrôle, des plans de lecture clairs et des panneaux uniquement lorsque la fonction l’exige.

## 4. Recherche et comparaison des stacks desktop

| Option | Fluidité/UI riche | Intégration Python V14 | Packaging Windows | Empreinte | Sécurité | Maintenabilité | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| **Tauri 2 + React/TS** | excellente | excellente via sidecar/API | très bon, MSI/NSIS | faible à moyenne | excellente si capabilities minimales | excellente | **recommandé** |
| Electron + React/TS | excellente | bonne via processus enfant/API | mature | élevée, Chromium embarqué | bonne mais exige durcissement constant | très bonne | plan B |
| PySide6 / Qt Quick | excellente | native | bon via `pyside6-deploy` | moyenne à élevée | bonne | moyenne pour une équipe surtout web/Python | valable si UI 100 % Qt |
| PWA locale | bonne | bonne via localhost | faible | faible | navigateur, intégration OS limitée | bonne | insuffisant comme cible finale |

### 4.1 Pourquoi Tauri 2 convient le mieux

- Tauri utilise HTML dans une WebView et un backend Rust avec communication par messages.
- Sous Windows, Tauri utilise WebView2, basé sur Edge/Chromium ; l’installateur peut assurer sa présence sur les systèmes qui ne l’embarquent pas.
- Tauri documente explicitement les **sidecars**, y compris les applications ou serveurs Python empaquetés avec PyInstaller.
- La WebView système évite d’embarquer un Chromium complet pour chaque installation.
- Les capabilities Tauri permettent une surface d’autorisation étroite : pas de shell générique, pas d’accès fichier arbitraire, pas de commande d’exécution métier directement exposée au frontend.

### 4.2 Pourquoi Electron reste un plan B

Electron est extrêmement mature et son modèle multi-processus isole processus principal et renderers. Il simplifie certains cas complexes, mais embarque Chromium et Node, augmente l’empreinte et crée une surface de sécurité plus large. Pour une application locale unique sur Windows, ces coûts ne sont pas justifiés tant que Tauri répond aux besoins.

### 4.3 Quand PySide6 serait préférable

PySide6 est pertinent si la priorité absolue devient une interface Qt native/Qt Quick, une intégration directe aux objets Python ou un rendu spécifique non web. Son outil officiel `pyside6-deploy` s’appuie sur Nuitka et produit un `.exe` Windows. En revanche, une UI aussi dense, animée, personnalisable et riche en visualisations demandera davantage d’effort qu’un frontend React spécialisé.

### 4.4 Pourquoi ne pas rester uniquement en PWA

Une PWA reste utile comme mode secours ou aperçu distant, mais elle ne donne pas la même maîtrise du cycle de vie, du tray, des fenêtres, du packaging, des raccourcis globaux, des mises à jour signées et du processus Python associé. La cible demandée est une application Windows, pas seulement une page installable.

## 5. Architecture technique cible

```text
┌──────────────── Application Windows Titanium ────────────────┐
│                                                              │
│  Tauri 2                                                     │
│  ├─ fenêtre, tray, cycle de vie, mise à jour signée          │
│  ├─ capabilities minimales                                   │
│  └─ supervise le sidecar Python                              │
│                                                              │
│  React + TypeScript                                          │
│  ├─ shell ORBE                                               │
│  ├─ workspaces et panneaux                                   │
│  ├─ graphiques / tables virtualisées                         │
│  └─ aucune règle de trading                                  │
│                │ snapshots REST                              │
│                │ événements WebSocket/SSE                    │
│                ▼                                             │
│  Sidecar Python V14                                          │
│  ├─ adaptateur autour de titanium.web.state                  │
│  ├─ contrats Pydantic/OpenAPI versionnés                     │
│  ├─ lecture MT5 sérialisée                                   │
│  ├─ projections organes / décisions / mémoire                │
│  └─ actions locales allowlistées, séparées                   │
│                │                                             │
│                ▼                                             │
│  Organes V14 existants, résultats, CollabHub, Prometheus     │
└──────────────────────────────────────────────────────────────┘
```

### 5.1 Séparer trois plans

1. **Plan de données** : snapshots, événements, graphiques, preuves, fraîcheur.
2. **Plan de contrôle local** : tâches, préférences, disposition des panneaux, export.
3. **Plan d’exécution** : mur DEMO, RiskGate, exécuteur MT5 — physiquement séparé et inaccessible par défaut depuis l’UI.

Aucun bouton visuel ne doit appeler directement `mt5.order_send`, modifier un stop, armer une boucle, promouvoir une cellule ou changer le risque. Une future action sensible exigerait un contrat dédié, une confirmation humaine hors ambiguïté, une preuve d’autorité et un audit append-only.

### 5.2 Transition sans rupture

- **Phase initiale** : l’application Tauri consomme les routes existantes de `tools/dashboard.py` sur loopback.
- **Phase intermédiaire** : extraire un service API typé sans changer `titanium.web.state`.
- **Phase cible** : snapshots REST versionnés + canal événementiel, tout en gardant un mode web de secours read-only.

### 5.3 Contrats de données

Chaque bloc doit porter :

```text
schema_version
source
observed_at
received_at
age_ms
freshness = FRESH | STALE | UNKNOWN
quality_flags[]
correlation_id / decision_id / organ_id
```

Une donnée stale ne doit jamais être rendue comme fraîche. Le frontend affiche l’âge mesuré, mais la décision de fraîcheur vient du backend.

## 6. Composition de l’application

### 6.1 Shell permanent

```text
┌─ titre natif + recherche universelle + état PAPER/DEMO ──────────────┐
│ Rail       │ Bande vitale : compte · boucle · risque · cerveau       │
│ espaces    ├──────────────────────────────────────────────┬───────────┤
│            │                                              │ Inspecteur│
│ ORBE       │        espace de travail principal           │ contextuel│
│ Marché     │        (1 sujet dominant)                    │ preuves   │
│ Décision   │                                              │ causalité │
│ Risque     ├──────────────────────────────────────────────┴───────────┤
│ Mémoire    │ chronologie événements / alertes / latence              │
│ Cerveau    └───────────────────────────────────────────────────────────┘
│ Système
└───────────────────────────────────────────────────────────────────────┘
```

Principes :

- un sujet dominant au centre, pas douze cartes concurrentes ;
- inspecteur contextuel à droite, escamotable ;
- timeline globale en bas pour relier les événements ;
- rail gauche compact, avec états critiques visibles ;
- panneaux redimensionnables et dispositions enregistrées ;
- navigation complète au clavier ;
- commande universelle `Ctrl+K` pour chercher actif, décision, organe, ticket, tâche ou preuve.

## 7. Espaces fonctionnels complets

### A. ORBE — corps vivant

Vue anatomique principale du bot : organes groupés par perception, décision, risque, action et mémoire ; artères animées seulement lorsqu’un débit réel est mesuré.

Fonctions :

- zoom, déplacement et sélection ;
- clic sur un organe → identité, version, santé, lag, dernière pulsation, dépendances et preuves ;
- clic sur une artère → débit, messages, pertes, erreurs, TTL et causalité ;
- mode « flux », « santé », « fraîcheur », « autorité » et « code » ;
- mode historique pour rejouer un incident ;
- filtres `sain / ralenti / bouché / inconnu / stale / quarantiné`.

### B. Radar Marché

- univers complet virtualisé ;
- heatmap par classe d’actif, tendance, qualité de données, émotion, énergie, setup et edge ;
- scan explicite, jamais automatique s’il coûte une lecture MT5 importante ;
- tri multi-colonnes, favoris et groupes ;
- âge de chaque donnée ;
- tunnel des refus : données → structure → confluence → edge → risque.

### C. Cockpit Actif

- chandeliers multi-timeframe ;
- curseur croisé et synchronisation M5/M15/H1/H4 ;
- zones SR/FVG/OTE/VPOC, liquidité, stops, entrée/SL/TP ;
- panneau de piliers avec raisons, pas seulement des voyants ;
- émotion valence/arousal séparée des indicateurs mécaniques ;
- macro/endocrinien avec provenance et demi-vie ;
- décision déterministe, avis cortex, contradictions et abstentions ;
- bouton « ouvrir dans MT5 » uniquement comme navigation, pas comme ordre.

### D. Chaîne de décision

Visualisation causale d’une décision :

```text
Observation → Qualité → Features → Setup → Confluence →
Émotion/Macro → Edge → RiskGate → Mur DEMO → Résultat/Abstention
```

Chaque nœud expose entrées, sortie, durée, version, preuve et motif. Le mode comparaison montre deux décisions ou deux versions de politique côte à côte.

### E. Émotion / système limbique

- plan circumplex interactif valence × arousal ;
- trajectoire temporelle de la foule ;
- états nommés : panique, capitulation, euphorie, complaisance, espoir, anxiété ;
- provenance : pression acheteur/vendeur, positionnement, volatilité/volume, macro/news, chasse aux stops ;
- confiance et fraîcheur par source ;
- aucune présentation du RSI comme émotion.

### F. Macro / endocrinien

- FRED/EIA et futurs organes macro ;
- valeurs, unités, période économique, date de connaissance et vintage ;
- hormones lentes, concentration, direction, demi-vie et organes récepteurs ;
- révisions et données inconnues/stale mises en évidence ;
- aucun texte externe interprété comme instruction.

### G. Risque et exposition

- budget global, occupation, risque par position et corrélation ;
- échelle de sizing issue du backend ;
- scénarios « si SL touchés » en lecture analytique ;
- limites par actif/classe ;
- raisons RiskGate détaillées ;
- mur DEMO/réel toujours visible ;
- armement représenté comme danger, jamais comme succès vert.

### H. Positions et OMS

- positions V14 et ordres limites ;
- P&L, R courant, excursion favorable, phase, SL/TP, expiration ;
- timeline intention → grant → ordre → ack → fill → gestion → clôture ;
- rapprochement avec ledger et journal ;
- détection d’état inconnu ou de ticket non suivi ;
- lecture seule dans le premier lot.

### I. Edge, mémoire et promotion

- contexte × classe d’actif × strate ;
- nombre d’échantillons, expectancy R, borne 95 %, profit factor, coûts exacts ;
- journal rejeté ou incomplet ;
- progression vers les seuils sans présenter une promotion automatique ;
- replay d’épisodes et comparaison intention/résultat ;
- mémoire centrale : faits, propositions, alertes et intégrité.

### J. Cerveau et analystes

- état de conscience, modèle, digest, latence, file d’attente et circuit breaker ;
- propositions GLM/Llama/Hermès séparées des décisions déterministes ;
- preuves citées, contradictions, incertitudes, invalidateurs et expiration ;
- comparaison avis cortex / décision mécanique / résultat ;
- aucune route LLM → MT5.

### K. Command Center multi-agent

- Florent, Prime, Claude, Codex, Hermès ;
- présence, rôle, activité et preuves ;
- tâches en colonnes ou liste dense, dépendances et accusés de réception ;
- rapports et handoffs ;
- aucun lancement automatique de LLM depuis une tâche, conforme à `titanium/web/command_center.py:240-247`.

### L. Système, code et observabilité

- services, PID, port, heartbeat, CPU/mémoire si exposés proprement ;
- métriques Prometheus/Grafana intégrées ou liées ;
- modules, imports, dépendants, cycles, couverture et symboles inertes ;
- tests récents, durée, échecs et historique ;
- logs structurés redacted, sans ligne de commande ni secret ;
- état du pont MQL5, indicateur, payloads et fenêtres.

### M. Données et provenance

- chaîne de vendeurs par méthode ;
- santé des sources, latence, cache, erreurs et fallback ;
- provenance jusqu’au fait brut ;
- qualité, âge, révision et licence ;
- export scellé d’un paquet de preuves, jamais de secret.

### N. Réglages locaux

- thème, densité, animations, disposition, raccourcis, format numérique ;
- sources purement visuelles/locales ;
- les paramètres de trading, risque, armement et promotion n’y figurent pas dans le premier lot.

## 8. Design system original « Titanium OS »

### 8.1 Palette

| Rôle | Couleur proposée | Usage |
|---|---|---|
| vide | `#05080C` | fond principal |
| graphite | `#0A1016` | rails et plans profonds |
| titane | `#111A22` | surfaces |
| relief | `#18242E` | hover/sélection |
| encre | `#E7EDF2` | texte principal |
| encre faible | `#8A9AA8` | métadonnées |
| bioélectrique | `#64D8E8` | focus, sélection, flux actif |
| sain | `#38B889` | uniquement santé positive |
| vigilance | `#E5A84B` | ralentissement/stale |
| danger | `#F05A67` | compte réel, mur armé, erreur critique |
| mémoire | `#A88AD8` | cortex/mémoire uniquement |

Le cyan n’est pas une décoration : il signale le focus et la circulation. Le vert ne signifie pas « action autorisée », seulement « état sain ». Le rouge est réservé au danger concret.

### 8.2 Typographie

- **UI principale :** Segoe UI Variable, native Windows, précise et non ostentatoire.
- **Nombres et preuves :** IBM Plex Mono ou JetBrains Mono, avec chiffres tabulaires.
- Taille de base 13–14 px pour la densité desktop, jamais sous 11 px.
- Titres de section en casse normale ; uppercase réservé aux micro-labels techniques.

### 8.3 Formes et profondeur

- rayons 4–8 px, pas de capsules partout ;
- bordures 1 px très faibles ;
- niveaux de profondeur par luminance, pas par blur ;
- ombres rares, réservées aux menus, inspecteurs flottants et palette de commandes ;
- grille 4 px, rythme principal 8 px ;
- aucun grand halo décoratif continu.

### 8.4 Motion

- 120–180 ms pour hover/sélection ;
- 180–260 ms pour ouverture de panneau ;
- flux organique à 20–30 FPS maximum et uniquement si la donnée change ;
- pas de boucle décorative ;
- `prefers-reduced-motion` respecté ;
- toute animation de santé dépend d’un heartbeat réel.

## 9. Bibliothèques UI recommandées

| Besoin | Choix | Motif |
|---|---|---|
| shell | React + TypeScript + Vite | écosystème mature, typage, rapidité |
| primitives accessibles | Radix UI ou Ariakit, restylés | clavier, focus, menus, dialogs |
| état serveur | TanStack Query | cache, invalidation, erreurs, retries bornés |
| état local | Zustand léger | layouts, filtres, préférences |
| tables | TanStack Table + Virtual | grandes listes sans jank |
| marché | TradingView Lightweight Charts | bibliothèque dédiée aux graphiques financiers interactifs |
| statistiques | Apache ECharts | heatmaps, distributions, séries multiples |
| organes/code | Cytoscape.js ou React Flow | graphes inspectables, zoom et sélection |
| panneaux | `react-resizable-panels` | shell dockable sans lourdeur IDE complète |
| validation | schémas générés depuis OpenAPI + Zod | contrat frontend/backend vérifiable |

Il faut éviter une grosse bibliothèque de composants visuels prête à l’emploi : elle rendrait l’interface reconnaissable comme un template. Les primitives peuvent être utilisées, mais le style doit être propre à Titanium.

## 10. Performance et fluidité

Budgets cibles sur la machine de Florent :

- fenêtre interactive en moins de 2 s après affichage du shell ;
- navigation entre espaces sous 100 ms hors chargement de données ;
- interaction courante à 60 FPS ;
- graphe organique limité à 20–30 FPS ;
- aucune requête MT5 lourde déclenchée par un simple hover ;
- tables virtualisées au-delà de 200 lignes ;
- snapshots différentiels plutôt que rechargement global ;
- cache explicite avec âge visible ;
- workers web pour agrégations purement visuelles lourdes ;
- backpressure sur événements et coalescence des ticks ;
- aucun calcul statistique de 20 s dans le thread HTTP principal, dans la continuité de `titanium/web/state.py:409-457`.

## 11. Sécurité Windows et locale

- liaison backend sur `127.0.0.1` uniquement ;
- jeton de session éphémère transmis par la coquille, jamais persisté ;
- origine et CSP strictes ;
- Tauri capabilities en deny-by-default ;
- aucune API shell générique ;
- aucun accès frontend arbitraire au système de fichiers ;
- routes read-only séparées des routes de tâche ;
- sérialisation stricte et limites de taille ;
- redaction des chemins sensibles, secrets et lignes de commande ;
- signature du bundle et des mises à jour ;
- provenance et journal append-only pour toute future action sensible ;
- compte réel affiché comme incident critique ;
- `LIVE` absent des contrats d’exécution exposés à l’interface.

## 12. Roadmap de réalisation

### Lot 0 — preuve de design

- produire trois compositions : conservatrice, recommandée, divergente ;
- utiliser des données JSON réelles anonymisées/scellées, pas des métriques inventées ;
- prototype interactif du shell, ORBE, cockpit actif et inspecteur ;
- test visuel 1600×900, 1920×1080 et 2560×1440 ;
- revue Florent avant code de production.

### Lot 1 — coquille Windows read-only

- Tauri 2 + React/TS ;
- démarrage/supervision du dashboard Python existant ;
- ORBE, vitaux, anomalies, positions et cockpit actif ;
- aucune route d’action sensible ;
- packaging installable Windows.

### Lot 2 — API typée et temps réel

- contrats versionnés ;
- snapshots par domaine ;
- WebSocket/SSE ;
- fraîcheur/provenance uniforme ;
- cache, backpressure et reconnexion.

### Lot 3 — organes complets

- émotion, macro, edge, mémoire, promotion, cerveau, données ;
- decision trace et replay ;
- graphes relations et timeline causale.

### Lot 4 — système et collaboration

- Command Center ;
- observabilité, tests et carte du code ;
- exports de preuves ;
- logs redacted.

### Lot 5 — durcissement

- tests E2E Playwright ;
- tests Tauri/sidecar ;
- audit capabilities/CSP ;
- crash recovery ;
- installation propre sur un Windows de test ;
- mesure CPU/RAM/latence ;
- signature et procédure de mise à jour.

## 13. Critères d’acceptation

### Complétude

- chaque bloc retourné par `titanium.web.state.state()` possède un espace ou un inspecteur ;
- chaque organe de `medecin.py` est navigable ;
- chaque décision est reliée à ses preuves, sa fraîcheur et son résultat ;
- Command Center, code, tests, services et données sont intégrés.

### Vérité

- aucune valeur fraîche sans timestamp et règle de fraîcheur ;
- aucune animation sans mesure ;
- aucune métrique factice ;
- aucune donnée LLM présentée comme décision déterministe ;
- aucune promotion présentée comme automatique.

### UX

- les trois urgences majeures sont visibles en moins de 2 secondes ;
- un actif, ticket, organe ou décision est accessible en trois actions maximum ou via `Ctrl+K` ;
- navigation 100 % clavier des fonctions principales ;
- contrastes WCAG AA pour texte et contrôles ;
- mode réduction de mouvement fonctionnel ;
- aucun écran principal composé d’une simple grille uniforme de cartes.

### Performance

- absence de jank sur listes et graphes ;
- pas de blocage UI pendant un scan ;
- reconnexion backend sans écran blanc ;
- pression événementielle bornée ;
- mémoire stable sur une session de huit heures.

### Sûreté

- application inutilisable comme chemin direct vers un ordre réel ;
- aucune capacité shell libre ;
- aucun secret dans l’UI, les logs ou les exports ;
- action sensible absente du premier lot ;
- PAPER/DEMO visible en permanence et dérivé de l’autorité backend.

## 14. Sources officielles consultées

### Desktop

- Tauri, architecture : https://v2.tauri.app/concept/architecture/
- Tauri, sidecars / binaires externes : https://v2.tauri.app/develop/sidecar/
- Tauri, versions de WebView : https://v2.tauri.app/reference/webview-versions/
- Electron, modèle de processus : https://www.electronjs.org/docs/latest/tutorial/process-model
- Electron, sécurité : https://www.electronjs.org/docs/latest/tutorial/security
- Qt for Python, `pyside6-deploy` : https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-deploy.html
- Microsoft Edge, Progressive Web Apps : https://learn.microsoft.com/en-us/microsoft-edge/progressive-web-apps-chromium/

### Visualisation

- TradingView Lightweight Charts : https://tradingview.github.io/lightweight-charts/
- Apache ECharts : https://echarts.apache.org/en/index.html
- Cytoscape.js : https://js.cytoscape.org/
- TanStack Table : https://tanstack.com/table/latest
- TanStack Virtual : https://tanstack.com/virtual/latest

### Projet V14

- `tools/dashboard.py`
- `tools/ui/poste.html`
- `tools/ui/poste.js`
- `titanium/web/state.py`
- `titanium/web/medecin.py`
- `titanium/web/carte.py`
- `titanium/web/command_center.py`
- `docs/ETUDE_ARCHITECTURE_ORGANIQUE_V14_20260827.md`
- `docs/PROPOSITION_ORGANISME_FEDERE_V14_20260827.md`

## 15. Décision proposée

**GO recommandé pour le Lot 0 : prototype interactif haute fidélité, read-only, basé sur les vraies structures V14.**

La bonne première réalisation n’est pas encore le packaging final. Il faut d’abord valider la composition du poste de contrôle avec trois directions visuelles, puis consolider la plus forte. La direction recommandée est un **ORBE sombre et dense, asymétrique, orienté flux et inspection**, avec cockpit actif au centre, santé du corps en permanence et preuves contextuelles à droite.

**NO-GO** pour :

- réécrire les organes en TypeScript ;
- ajouter des boutons d’armement, de risque ou d’ordre au prototype ;
- maquiller des données stale ;
- copier l’identité visuelle d’un terminal connu ;
- transformer l’application en grille de cartes néon générique.
