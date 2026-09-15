# Corrections Codex / Hermès / Claude — 8 septembre 2026

## État opérationnel

- Le compte `10055401` sur `Axi-US50-Demo` est confirmé DEMO (`trade_mode=0`).
- Le mur d'exécution est ouvert, `real_allowed=false` et la boucle `live_demo --armer`
  tourne sans console bloquante avec une sortie redirigée vers
  `results/live_demo.current.log`.
- Une seule instance de `live_demo`, `dashboard`, `analystes` et
  `collecteur_microstructure` tourne. Le tableau de bord, le CollabHub et le terminal de
  collaboration répondent.
- Le registre d'exécution contient trois intentions anciennes classées
  `RESOLVED_NO_ORDER`, aucune intention `UNKNOWN` et aucun deal attribué. La boucle a repris
  ses tours après la revue exhaustive du dernier intent DJ30.fs.
- Aucune position et aucun ordre en attente ne sont ouverts au moment du dernier contrôle.
- Hermès Cortex est `ready` et produit des décisions signées
  `hermes-cortex/claude-opus-5`. Les refus `CORTEX_POLICY_STALE` observés pendant un tour
  correspondent au délai asynchrone avant publication ; l'analyste renouvelle ensuite la
  politique.

## Correctifs principaux

- Les observations de gestion de position conservent leur horodatage source. Une observation
  absente, future, sans fuseau ou périmée ne peut plus autoriser une sortie.
- Les appels Hermès sont sérialisés autour de la limitation de fréquence et du circuit de
  disponibilité. Sous Windows, le Cortex lance l'interpréteur du venv Hermès et son point
  d'entrée Python, sans passer par le shim bloqué par Smart App Control.
- La microstructure utilise une collecte à 5 s, une fraîcheur de place de 8 s et une fraîcheur
  globale de 15 s.
- Le cache de corrélation suit les symboles réellement demandés, inclut BTCUSD et regroupe les
  changements continus du catalogue dans une fenêtre de 15 minutes.
- Le registre d'exécution conserve les diagnostics numériques sûrs du terminal et sait revoir
  un accusé perdu dans les ordres actifs, l'historique et les deals, avec gestion des tags
  tronqués.
- Le tag d'intention MT5 est ramené de 31 à 29 caractères. Le binding Python Axi refusait les
  commentaires de 30 caractères ou plus avec `last_error=-2`, avant toute transmission. Le
  suffixe de 16 chiffres hexadécimaux conserve un discriminateur de 64 bits.
- `RES_E_INVALID_PARAMS (-2)` est maintenant classé `REJECTED` plutôt que `UNKNOWN`, car il
  décrit un rejet local de paramètres. Les erreurs IPC, de réception et de délai restent
  incertaines et continuent de bloquer les nouvelles entrées jusqu'à réconciliation.
- `build_sealed_decisions` valide maintenant la jointure décision/résolution : ticket normalisé,
  symbole, ordre temporel, métriques finies, raison de sortie et unicité du ticket dans
  l'époque de politique.
- Le registre de décisions précharge son index d'identifiants avant le premier envoi et
  déduplique ensuite en O(1), sans rescanner le fichier complet après chaque événement.
- Le substitut de test xxHash a été remplacé par une implémentation Python pure de XXH3-128
  compatible bit à bit, validée par Hermès sur 12 483 vecteurs officiels xxHash v0.8.2.

## Décisions de configuration autorisées

- **Retrait du FX** : décision humaine confirmée le 8 septembre. `FX_SUSPENDU=True` est actif et
  les instruments FX sont exclus avant la décision d'entrée. Le journal de tâches est clos.
- **Levée de l'anti-fade** : décision humaine confirmée le 8 septembre.
  `ANTI_FADE=ANTI_FADE_AUTORISE` est actif. Une entrée de retournement reste soumise aux gardes
  de coût, risque, microstructure, mémoire et Cortex. Le journal de tâches est clos.
- **Environnement** : la valeur factice `DEEPSEEK_API_KEY=sk-deepseek-test` et ses variantes
  autorisées ont été retirées de `.env` sans afficher le reste du fichier.

Le retrait du FX réduit l'exposition aux spreads, au bruit et aux régimes qui dégradaient
l'échantillon historique ; il réduit aussi le nombre d'occasions et la diversification. La
levée de l'anti-fade permet de mesurer et d'exécuter la famille `reversal`, positive hors
échantillon à `+0,1250 R` avec le FX exclu et la porte de coût active. Elle expose davantage aux
faux retournements et à la variance contre tendance. La famille `continuation` restait plus
forte à `+0,1819 R`, donc les autres gardes restent nécessaires.

## Cas IT40

Le terminal Axi annonce pour IT40 `trade_exemode=2` (Market Execution) et `filling_mode=0`.
Trois appels `order_check`, sans envoi, ont retourné `10030 Unsupported filling mode` pour FOK,
IOC et RETURN. La documentation MetaQuotes interdit RETURN en Market Execution ; le courtier ne
fournit donc aucun mode valide pour un ordre marché IT40 dans cette configuration. Le moteur ne
devine pas un mode. Grâce au tag corrigé, un éventuel essai reçoit un refus courtier explicite
et ne crée plus l'incertitude globale provoquée par le commentaire invalide.

Avec le nouveau tag, `order_check` retourne `retcode=0 Done` sur DJ30.fs et XRP-JPY. Ces deux
validations ne transmettent aucun ordre.

## Validation

- Suite sûre complète : **2 842 tests réussis**, 3 ignorés, 71 sous-tests réussis, 19
  avertissements, en 72,07 s après le dernier correctif. Seuls les modules qui manipulent
  volontairement `.env` ou une
  configuration locale externe sont exclus de cette passe.
- Tests ciblés registre, exécution, scellement et décision : **103 réussis**.
- Ruff global et porte `E9,F63,F7,F82` : réussis.
- `git diff --check` : réussi ; les messages restants concernent uniquement la conversion
  LF/CRLF de fichiers déjà modifiés.
- Le test de régression du tag a été observé rouge avec 31 caractères, puis vert avec la limite
  de 29 caractères.
- GitNexus après les correctifs principaux : empreinte
  `5D5116B4669F9590992B026FE85A2F81E4B57B654C6BDEA2A4596A0F7049888F`, 240 symboles
  modifiés, 97 éléments affectés et 114 fichiers. Le risque agrégé `CRITICAL` reflète le vaste
  arbre partagé ; l'impact ciblé du correctif `execute_recorded` est `LOW` (un appelant direct,
  deux flux).

Les modifications concurrentes et préexistantes du vaste arbre de travail ont été conservées.
Aucun commit global n'a été créé par Codex. Les échanges de preuve et les demandes de revue ont
été publiés sur CollabHub pour Hermès et Claude.

## Recalibrage local du 9 septembre 2026

Pendant la remise à zéro du compte Claude, le Cortex utilise
`ollama-local/qwen3.5:2b`. Le choix est mesuré sur la machine cible (15,35 Gio de RAM,
GPU AMD intégré, calcul CPU) :

- `org/qwen2.5-1m:7b` alloue bien 65 536 tokens mais dépasse 120 s même sur le smoke
  Hermes ; son chargement réserve environ 9,29 Go ;
- `granite4.2:3b` alloue 65 536 tokens mais dépasse également 120 s et pousse la RAM
  jusqu'à 99,3 % ;
- `qwen3.5:2b` alloue 65 536 tokens, réserve environ 3,53 Go, rend le smoke Hermes
  à chaud en 12,95 s et le prompt V14 réel en 37,69 s par appel unitaire.

Le CLI interactif Hermes chargeait son contexte agent généraliste et pouvait dépasser
120 s sur un lot que l'API Ollama locale traitait en 48 s. Le transport local du Cortex
impose donc directement le rôle système Hermes V14, `num_ctx=65536`, `think=false`,
température nulle, JSON strict et aucun outil. Il conserve les contrats scellés, le
disjoncteur, la publication `hermes-cortex/qwen3.5:2b` et le repli `WAIT`.

Qwen 2B omettait parfois une référence dans un lot de deux. Le contrôle d'intégrité a
bien refusé ces réponses. La taille est maintenant bornée à un candidat par appel, sans
espacement de quota distant. Trois lots réels consécutifs ont ensuite rendu six
politiques exactes en 83 s, 91 s et 90 s, sans timeout. Le noyau a consommé une politique
`CORTEX_POLICY_EXACT ALLOW` pour BTC-JPY ; l'ordre a ensuite été refusé par le garde de
corrélation `GRAPPE_SYMBOLE_ABSENT`, ce qui confirme que l'autorité Hermes ne contourne
aucune protection de risque.

Les preuves reproductibles sont dans `results/qwen*_64k_benchmark_20260909.json`,
`results/granite4_2_3b_64k_benchmark_20260909.json`,
`results/direct_ollama_production_prompt_20260909.json` et
`results/hermes_cortex_exact_prompt_20260909.json`. Le stress de récupération à 55 000
mots a expiré pour les trois candidats : la fenêtre est réellement allouée, mais une
charge proche de 64k n'est pas exploitable dans le délai V14 sur ce matériel.

## Blocage des pertes live du 9 septembre 2026

L'état armé décrit au début de ce rapport est remplacé par cette décision plus récente.
La chaîne d'exécution DEMO sait transmettre, rapprocher et journaliser un ordre, mais son
edge live est négatif. Les nouvelles entrées sont donc désarmées. La gestion des stops et
des sorties adaptatives des positions déjà ouvertes reste active.

Le compte `10055401` totalise, au dernier contrôle, cinq clôtures le 9 septembre pour
`-4,3060 R`, et 95 clôtures sur sept jours pour `-18,3823 R`. Sur les 787 clôtures alors
analysées, le total était `-100,4815 R`, l'espérance `-0,1277 R` par trade et le profit
factor `0,746`. Les familles `continuation` et `reversal` étaient toutes deux négatives.
Ces résultats live invalident toute affirmation actuelle de rentabilité, même si certains
rejeux historiques étaient positifs.

Le module `titanium/execution/live_loss_guard.py` impose maintenant une preuve comptable
live, nette, UTC et dédupliquée par ticket. Il produit :

- `BLOCK` à partir de `-2 R` sur la journée UTC ;
- `BLOCK` à partir de `-6 R` sur sept jours glissants ;
- `WAIT` si le journal, le compte ou une preuve récente est absent ou invalide.

La boucle appelle ce coupe-circuit après la gestion protectrice et la réconciliation, mais
avant tout nouveau balayage d'entrée. Le heartbeat vérifié affiche `armed=false`,
`execution_trace.status=OK`, `live_loss_guard.action=BLOCK` et `envoyes=0`. Une position
s'est fermée sous protection après le désarmement ; trois positions restent gérées au
dernier heartbeat. Aucun réarmement ne doit avoir lieu avant une validation hors échantillon
positive après spread, commission, slippage et swap.

Claude a confirmé via CollabHub que l'anti-fade n'explique pas cette série : les cinq
clôtures du jour portent `contre_tendance=false`. Le verdict et les seuils ont été transmis
à Hermes sous la tâche `V14-LOSS-GUARD`, message
`7db2c98a-0637-4239-93f4-fbff778bc01c`, avec instruction de maintenir `WAIT/BLOCK` jusqu'à
une preuve hors échantillon positive.

Validation finale de ce correctif : 77 tests ciblés réussis ; suite sûre complète de
2 856 tests réussis, 3 ignorés et 71 sous-tests réussis ; Ruff global réussi. Le test qui
écrit volontairement dans `.env` a été exclu conformément aux règles du dépôt, et la valeur
factice qu'il avait recréée a été retirée sans afficher le fichier. GitNexus signale un
risque agrégé `CRITICAL` sur les 118 fichiers déjà modifiés dans l'arbre partagé ; les
impacts ciblés de `tour` et `_traiter_lot` restent `LOW`. Aucun commit global n'a été créé.
