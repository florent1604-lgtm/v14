# Veille recherche et sourcing d'alpha — Titanium V14

**Date :** 13 septembre 2026
**Objet :** où trouver la prochaine idée, comment la filtrer, et comment la **prouver ici** au lieu
de l'importer sur la foi d'un papier.
**Documents liés :** `DOSSIER_PRODUCTION_V14_20260913.md` §3 (ce que les acteurs institutionnels
exécutent), `ARCHITECTURE_EXECUTION_MULTI_OS_V14_20260913.md` (où faire tourner la recherche),
`CORTEX_INGENIEUR_V14_20260913.md` (qui lit les papiers).

> Méthode : les chiffres d'inventaire de ce document ont été mesurés sur le disque et sur le
> terminal MT5 le 13/09/2026. Les listes de sources et de thèmes proviennent de la note que vous
> avez transmise (SSRN, arXiv, RePEc/IDEES, QuantConnect ; GEX, 0DTE, options, volatilité,
> microstructure, VWAP, flux de commandes, apprentissage automatique, changement de régime) ; elles
> sont confrontées ici à la donnée qui existe réellement dans le dépôt, pas reprises telles quelles.

---

## 1. Le problème que ce document résout

Vous avez demandé « ce que les véritables acteurs des marchés exécutent pour être rentables » et un
dossier à passer en production. Le dossier de production répond pour l'**exécution**
(`DOSSIER_PRODUCTION_V14_20260913.md` §3 : TWAP, VWAP, POV, Implementation Shortfall, SOR, iceberg,
anti-gaming — avec ce qui se transpose et ce qui ne se transpose pas).

Il manquait le volet **amont** : où naît une idée, et par quelle porte elle entre dans ce dépôt.
C'est le sujet de ce document — et il est dangereux, parce que la littérature quantitative est un
immense réservoir d'hypothèses vraies mais **non transposables**, et qu'un backtest sur données déjà
vues ne prouve rien. La règle du dépôt s'applique ici plus qu'ailleurs : **une technique est une
hypothèse mesurée, jamais une rentabilité.**

---

## 2. L'inventaire : ce que V14 possède déjà comme matière

Avant d'aller chercher des données, il faut compter celles qu'on a. Mesuré :

| Matière | Volume / état | Exploitée ? |
|---|---|---|
| **Carnet L2 Binance** (BTCUSDT, ETHUSDT) | **20 Go**, 52 fichiers `.depth.ndjson` + `.trades.ndjson`, depuis le 16/08/2026 | **Quasiment pas** — l'enregistreur sait se **valider** (`--verifier`, `tools/enregistreur_carnet_binance.py:371`), il n'alimente **aucune** feature |
| Barres MT5 | 149 symboles, multi-unités (M1→D1) via `titanium/data/mt5_dataflows.py`, `archive_barres.py` | oui, chemin nominal |
| Microstructure MT5 | spread, `point`, `tick_value/tick_size`, ATR | oui, porte de coût |
| **`VIX.fs`** | le **seul** instrument de volatilité du catalogue (sur 149) | **non** — absent de toute feature |
| Calendrier macro | flux externe livré, états `CLEAR/ELEVATED/BLACKOUT` | oui, mais **veto uniquement** (voir dossier §1.1) |
| Arène d'exécution | `tools/backtest_execution_matrix.py`, **seed `14082026`**, 17 techniques × 864 scénarios | oui |
| Corpus scellé | 147 artefacts de rejeu + `results/analyse_pertes_scellee_20260813.json` | oui, mais **6 sources moteur divergent déjà** (dossier §5, P0.3) |
| Tunnel de refus | 2 254 refus `COUT_SPREAD` sur 136 tours | non — mesuré une fois, pas publié |

**La conclusion la plus utile de ce document est dans ce tableau :** le gisement d'alpha le plus
proche n'est pas dans un papier, il est dans **20 Go de carnet L2 déjà payés, déjà sur le disque, et
jamais lus par une seule feature**. C'est de la microstructure réelle — la seule famille de recherche
de votre liste qui soit à la fois **institutionnelle** et **entièrement instrumentable ici**.

---

## 3. Les quatre gisements documentaires

| Source | Ce qu'elle donne | Comment l'exploiter | Le piège |
|---|---|---|---|
| **SSRN** (preprints finance) | exécution, microstructure, market impact, optimal execution — les papiers **les plus proches de votre problème** | surveiller les séries « Market Microstructure » et « Trading & Market Microstructure » ; un papier d'exécution optimale est directement comparable à vos 17 techniques | mélange de preprints non relus ; un modèle optimal suppose souvent des coûts et une profondeur que vous n'avez pas |
| **arXiv** (q-fin.TR, q-fin.MF, ML) | méthodes : apprentissage, séries temporelles, RL, filtrage | le plus utile pour la **méthode de mesure**, pas pour le signal | prépublication, reproductibilité faible, et surtout : les jeux de données des papiers ne sont pas les vôtres |
| **RePEc / IDEES** (économie, finance) | cadrage économique, régime, coûts de transaction, littérature ancienne sur l'impact | utile pour la **thèse** d'une famille (pourquoi un effet existe) | horizon long, données agrégées, verdict rarement actionnable tel quel |
| **QuantConnect** (implémentations, backtests) | **code** d'algorithmes, d'indicateurs, de règles de risque | source d'implémentations de référence à comparer, et de harnais de test | code conçu pour **leur** moteur et **leurs** données ; recopier un moteur est un piège de licence et de données |

**Ce qui compte le plus :** ces quatre sources servent à produire des **hypothèses**, jamais des
verdicts. Un papier entre dans V14 par la porte du §5, pas par un copier-coller.

---

## 4. Les neuf thèmes, confrontés à la donnée que V14 possède

| Thème | Donnée requise | V14 a ? | Verdict |
|---|---|---|---|
| **Microstructure** | carnet, ticks, tailles | **oui — 20 Go L2 Binance** | ✅ **le chantier n°1** |
| **Flux de commandes** (order flow) | trades signés, déséquilibre du carnet | **oui**, même archive (`.trades.ndjson` à côté des `.depth.ndjson`) | ✅ **chantier n°1, même lot** |
| **Changement de régime** | séries longues + états | **oui** (barres, `VIX.fs`, calendrier macro) | ✅ testable dans l'arène, seed fixe |
| **Volatilité** | mesures de vol, idéalement multi-venues | **partielle** : ATR, spread, `VIX.fs`. L'**agrégateur multi-venues demandé au départ n'existe pas** | ⚠️ à construire (voir plus bas) |
| **VWAP** | volume, prix, horodatage | oui (barres) | ⚠️ utilisable comme **référence d'attribution**, pas comme signal d'entrée discrétionnaire |
| **Apprentissage automatique** | features + cibles + protocole honnête | oui, mais **sans protocole train/test cloisonné** | ⚠️ dangereux : 864 scénarios **déjà vus** ne sont pas un jeu de test |
| **Options** | chaîne d'options, surfaces, volumes | **non** — 149 symboles, aucun contrat d'option | ❌ hors de portée sur ce compte |
| **GEX** (gamma exposure) | chaîne d'options + open interest par strike | **non** | ❌ **infaisable** : exige des données OPRA, non disponibles chez ce courtier |
| **0DTE** | options à expiration du jour | **non** | ❌ même raison ; en plus, le sous-jacent (SPX 0DTE) n'est pas échangeable ici |

**Ce qu'il faut dire clairement :** trois des neuf thèmes (options, GEX, 0DTE) sont les plus
populaires de la liste et sont **arithmétiquement hors de portée** sur un compte DEMO Axi dont le
catalogue n'expose aucun contrat d'option. Les poursuivre produirait un document, pas un signal. Les
faire entrer exigerait de changer de fournisseur de données **et** de courtier — une décision
d'infrastructure, pas une amélioration du bot.

Trois autres (microstructure, flux de commandes, changement de régime) ne coûtent **rien** : la
donnée est déjà payée et déjà sur le disque.

---

## 5. Le pipeline : d'un papier à une technique livrée

Cinq portes, dans cet ordre. Aucune n'est optionnelle.

1. **Source** — un papier, une implémentation de référence, ou une observation sur vos propres
   données (le tunnel de refus et les 20 Go sont des sources légitimes).
2. **Hypothèse écrite avant la mesure** *(pre-registration)* — une phrase falsifiable, avec la
   prédiction chiffrée : « sur tel régime, tel coût moyen baisse de X ». **Écrire avant de mesurer**
   est ce qui empêche de transformer un bruit en découverte ; c'est aussi ce qui rend la porte 5
   défendable devant un auditeur.
3. **Implémentation mesurée dans l'arène** — `tools/backtest_execution_matrix.py`, seed **14082026**,
   les 864 scénarios, la matrice des 15 politiques comme référence **inchangée**. Une technique
   nouvelle s'ajoute au catalogue, elle ne remplace rien.
4. **Cloisonnement** — les scénarios ayant déjà servi à choisir la famille actuelle, tout candidat
   calibré dessus est **in-sample par construction**. Il faut soit un jeu de symboles/périodes
   tenus à part **dès maintenant**, soit des quotes archivées (carnet L2) comme second jeu. Sans
   cette porte, la mesure 3 est un miroir.
5. **Décision humaine explicite** — aucune promotion de seuil, aucune mise en live par un agent.
   Règle inchangée du projet.

**Compter les essais.** Chaque technique testée est un tirage : après `n` essais sur 864 scénarios,
la meilleure ligne est en partie sélection. Le plan adaptatif publie déjà honnêtement
**10 techniques sur 17 battent `market`** ; le même honnêteté doit s'appliquer au compteur d'essais
(« 17 testées, 10 gagnantes, seuil de bruit attendu ≈ 8,5 »), sinon la prochaine itération
« découvrira » du hasard.

---

## 6. Trois chantiers à ouvrir, priorisés

### R1 — Exploiter le carnet L2 déjà enregistré *(coût marginal ≈ 0)*

**Ce qui existe :** 20 Go, BTCUSDT + ETHUSDT, depuis le 16/08/2026, en paires
`depths` / `trades`, avec un vérificateur de rejeu déjà écrit.

**Ce qui manque :** un producteur de features. Concrètement, trois mesures qui se calculent sur ces
fichiers et qui nourrissent soit les techniques adaptatives existantes (`adaptive_features.py`), soit
la porte de coût :

- **déséquilibre du carnet** (bid/ask depth imbalance sur les 5–10 premiers niveaux) ;
- **taux de trade agressif signé** (volume acheteur − vendeur à la vente) ;
- **élasticité du carnet** (combien de bps faut-il payer pour X notionnel) — c'est la mesure qui
  **valide ou invalide** le modèle de coût actuel, qui suppose un spread fixe et aucun impact.

Cette troisième mesure est la plus précieuse du document : la porte de coût compare aujourd'hui
`spread / stop`, sans impact de taille. Sur 20 Go de carnet réel, on peut vérifier si cette
simplification tient, ou chiffrer l'erreur.

**Porte de sortie :** trois features calculées depuis l'archive, rejouables, avec un rapport
« coût modélisé vs coût réalisable » sur au moins un symbole.

### R2 — La mesure d'attribution d'exécution *(la brique absente du dossier §3)*

Sans elle, aucune des 17 techniques ne peut être jugée sur son **exécution** : on sait qu'une
technique bat `market` dans le simulateur, pas si l'entrée a été **bien placée** dans le temps. La
référence VWAP/TWAP de la période donne exactement ce jugement (écart réalisé ≤ 0 = bonne
exécution). Les données nécessaires existent (barres + horodatage + volume).

**Porte de sortie :** pour chaque trade clos, un écart à la VWAP de sa fenêtre, agrégé par technique.

### R3 — L'agrégateur de volatilité multi-venues *(demandé au départ, jamais construit)*

Constat à assumer : **aucune trace** de cette brique dans le dépôt, hors la mention honnête du
dossier §5 P2.10. Le périmètre minimal qui a du sens ici : `VIX.fs` (déjà au catalogue), la volatilité
réalisée des barres, et — pour le crypto — la volatilité implicite ou réalisée dérivée de l'archive
Binance. **Ne pas** vendre cela comme un feed institutionnel : c'est un indicateur de régime local.

**Porte de sortie :** un état de régime volatilité, publié dans la télémétrie, **lecteur unique
identifié** avant d'écrire la moindre ligne — la règle « aucun état sans lecteur » s'applique.

---

## 7. Le rôle du cortex dans cette boucle

C'est ici que le cortex ingénieur (`CORTEX_INGENIEUR_V14_20260913.md`) paie réellement : **un agent
de veille qui lit, résume et classe — jamais un agent qui décide.** Le partage correct :

| Tâche | Qui |
|---|---|
| Lire un papier, en extraire l'hypothèse testable et les données qu'elle exige | **Claude** (jugement long, sur abonnement) |
| Rendre un verdict borné sur une technique mesurée (conforme au schéma) | **Codex** (`--output-schema`) |
| Mesurer | **personne d'autre que le harnais** — un LLM ne mesure pas |
| Promouvoir un seuil | **l'utilisateur** |

Et la contrepartie, déjà écrite dans le doc 3 : un agent de veille qui interroge en boucle épuisera
le forfait. La cadence du §6 du doc 3 (quotidienne pour la veille) est ce qui rend le chantier
tenable.

---

## 8. Ce que ce document ne recommande pas

- **Importer GEX ou 0DTE.** Aucun contrat d'option n'est disponible chez ce courtier : ce serait
  produire du texte, pas du signal. À rouvrir seulement si le fournisseur de données change.
- **Recopier un algorithme de QuantConnect tel quel.** Données, moteur et licence diffèrent ; ce qui
  se reprend, c'est l'**idée**, mesurée dans votre arène.
- **Calibrer sur les 864 scénarios puis annoncer un résultat.** Ils sont déjà vus. Sans jeu tenu à
  part, toute amélioration annoncée est un ajustement.
- **Laisser le carnet L2 grossir sans lecteur.** 20 Go sur le disque, zéro feature : c'est le plus
  gros actif inexploité du dépôt, et il coûte du disque chaque jour.
- **Faire lire les papiers par le cortex sur le chemin de l'ordre.** La règle du dossier §6 tient :
  le cortex publie, il ne signe pas.

---

## 9. En une phrase

Le meilleur déploiement n'est pas un nouvel outil : c'est d'**ouvrir les 20 Go de carnet déjà
enregistrés pour valider le modèle de coût**, de **mesurer l'exécution** (VWAP réalisée par
technique), et de **réserver les papiers** — SSRN et arXiv en premier, options/GEX **écartés** faute
de données — au travail d'hypothèse, jamais de décision.
