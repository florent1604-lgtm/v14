# Architecture du banc A/B des entrées — plan pour revue

Tâche `b18ff568`. Mission Codex, hub offset 671, sous l'ordre de travail 668.
**Plan seulement : aucune ligne de code, aucune mesure de variante, aucun rejeu, aucun
seuil, aucun live.** Le code ne sera écrit qu'après réparation de GitNexus et deux ACCEPT.

HEAD `e20e995`. Corpus de référence : `results/rejeu_univers_brut`, 147 symboles,
génération `051f50adf179177e`, 797 706 trades. Arbre de travail `0901ca68…` : écart permis
et publié (contrat P0 `441bfea8`).

---

## 1. Le fait de structure qui gouverne toute l'architecture

**Le rejeu ne modélise aucun risque monétaire.** Chaque ligne scellée porte
`quantity = 1.0`, `quantity_unit = "risk_unit"` et un résultat en R
(`tools/rejeu_univers.py:240-241, 252-253`). Le chemin `titanium/backtest.py` →
`tools/rejeu_univers.py` n'importe ni `titanium/confiance.py`, ni `titanium/sizing.py`,
ni `titanium/risk/riskgate.py` : il n'y a ni équité, ni lot, ni pourcentage. Le nombre de
piliers n'influence ni l'entrée, ni le stop, ni la sortie, ni la taille — c'est une
**étiquette** (`titanium/backtest.py:362`).

Conséquence directe, et c'est la décision d'architecture centrale :

> Les quatre variantes demandées sont des **re-pondérations et des filtres exacts** sur les
> lignes déjà scellées. Aucun rejeu de 20 h. Aucun fichier de `FICHIERS_MOTEUR` touché.
> Aucun des 147 artefacts périmé.

Ce qui serait re-notable **et** ce qui ne l'est pas :

| Re-notable a posteriori | Exige un nouveau rejeu |
|---|---|
| pondération par un risque différent (`w(pillars) × net_r`) | quorum, portes, features, anti-fade |
| filtre sur `asset_class`, `family`, `pillars`, `cost_r`, `decision_at` | `sl_atr_k`, `rr_ratio`, `breakeven_r`, `trail_*`, `max_barres` |
| re-découpage IS/OOS sur `decision_at`, bootstrap par symbole | toute contrainte de portefeuille (créneaux, exposition) |
| métriques alternatives dérivées de `net_r`/`cost_r`/`split` | passage au monétaire (lot minimum, arrondi courtier) |

Raison technique du deuxième pilier de la colonne de droite : `mae_r` et `mfe_r` sont des
extrema **sans position dans le temps**. Rejouer une gestion différente exige de relire les
barres — l'argument est déjà écrit dans `tools/rejeu_breakeven.py:6-11`.

**Garde-fou d'architecture** : le banc refusera par construction toute variante qui modifie
la sélection ou la géométrie. Une variante « risque » n'est admissible que si le risque ne
décide pas qui entre. C'est vrai ici (le rejeu est mono-symbole, sans créneaux, sans
plafond d'exposition), c'est faux en direct. Ce n'est pas un détail de mise en œuvre :
c'est la frontière de validité du banc, et elle sera imprimée dans chaque rapport.

---

## 2. Un bloquant sémantique avant tout chiffre : « 3p » n'est pas « 3 piliers »

`titanium/edge.py:328-330` : le champ `pillars` vaut **support_pillars + 1** lorsque
`trend_sr` passe, et la clé de contexte l'écrit `"<n>p"` (`edge.py:63-65`).

Vérifié sur les deux journaux :

```
corpus scellé (147 symboles) : 3p = 711 450 | 4p = 85 004 | 5p = 1 252 | aucun 2p
results/trades.ndjson (live) : quorum=2 partout ;
    (quorum 2, support_pillars 2, context 3p) = 405
    (quorum 2, support_pillars 3, context 4p) =  82
    (quorum 2, support_pillars 4, context 5p) =   1
```

Donc, dans le diagnostic `2a31352` :
**« 2 piliers » = `3p` = `support_pillars 2`** et **« 3 piliers » = `4p` = `support_pillars 3`**.

L'énoncé de la tâche, « risque 3p neutralisé au niveau 2p », est écrit dans le vocabulaire
du diagnostic. Traduit dans celui des artefacts, il devient : **neutraliser le risque de
`4p` au niveau de `3p`**. Toute implémentation qui lirait littéralement « 3p » filtrerait
la mauvaise strate — et elle porterait sur 95 % du corpus au lieu de 11 %.

**Demande de revue n°1** : que Claude (mission 669, correction du vocabulaire) fige cette
correspondance dans le manifeste scellé de `b28105b2`, et que le banc la lise dans le
manifeste plutôt que de la recoder.

---

## 3. Deux populations qui se contredisent — à énoncer avant de mesurer

Les quatre variantes naissent d'une cohorte **live de 373 clôtures sur 12 jours**. Le banc
mesurera sur une population **différente** : 797 706 trades de rejeu, sans anti-fade, sans
entrée limite, sans contrainte de portefeuille, sur toute la profondeur d'archive.

Contrôle que j'ai recompté moi-même sur le cache existant
(`results/porte_cout_trades.parquet`, porte de coût 0,125, segment **vérification**) :

| axe | rejeu (vérification) | cohorte live (373) |
|---|---|---|
| indices | **+0,116 R** sur 30 124 trades, +3 500 R, 28/30 symboles positifs | **−0,152 R** sur 141 trades |
| FX | −0,044 R sur 27 304 trades | −0,116 R sur 94 trades |
| `4p` (« 3 piliers ») | **+0,100 R** sur 8 672 trades | **−0,322 R** sur 57 trades |
| `3p` (« 2 piliers ») | +0,088 R sur 72 250 trades | +0,008 R sur 316 trades |

Deux des quatre variantes sont donc **contredites par le corpus, avec 150 à 200 fois plus
d'observations**, et une seule — le FX — est concordante. Ce n'est pas un argument pour ne
pas les tester : c'est la raison pour laquelle le protocole doit être écrit **avant**, et
pour laquelle aucun résultat de ce banc ne pourra à lui seul justifier un changement live.

Je publie ce prior maintenant, et pas après, précisément pour qu'il ne puisse pas être
présenté plus tard comme une découverte du banc.

---

## 4. Garde anti-fuite — et une fuite déjà consommée qu'il faut nommer

### 4.1 Le segment de vérification est DÉJÀ brûlé pour l'axe classe d'actif

`docs/ARBITRAGE_PORTE_COUT_20260824.md` a publié, le 24/08, l'espérance **par classe sur le
segment de vérification** (indices +0,1067 sur 33 802 trades, FX +0,0203 sur 3 887), et la
décision métier de suspendre le FX a été prise sur cette table. Le segment de vérification
du corpus `051f50ad` n'est donc plus vierge pour toute hypothèse de niveau classe d'actif —
dont la variante « indices exclus ».

Conséquence de protocole, non négociable :

- la variante **« indices exclus » est déclarée EXPLORATOIRE**, jamais confirmatoire, quel
  que soit son résultat sur ce corpus ;
- sa seule confirmation possible est une fenêtre **postérieure**, préenregistrée, sur des
  données qui n'existent pas encore — donc une décision de Florent, pas une sortie de banc ;
- les deux autres variantes (`4p` neutralisé, combinaison) portent sur un axe qui n'a
  **pas** servi à décider quoi que ce soit à ce jour : elles restent confirmatoires au sens
  faible, sous réserve de la multiplicité (§8).

### 4.2 Les gardes ordinaires

1. **Préenregistrement.** Les quatre variantes sont figées dans un fichier de spécification
   versionné, dont le sha256 entre dans le sceau du rapport. Aucune grille, aucun balayage,
   aucun seuil choisi par le banc. Précédent : la grille figée de `tools/porte_cout.py:41`.
2. **Information ex ante seulement.** Un filtre ne peut lire que `asset_class`, `pillars`,
   `family`, `cost_r`, `side`, `symbol`, `decision_at`, `split` — tout ce qui est connu
   **avant** l'entrée. Un test refusera explicitement qu'une variante lise `net_r`,
   `gross_r`, `mae_r`, `mfe_r`, `exit_reason` ou `bar_sortie` comme critère de sélection.
3. **Le choix n'a lieu que sur la calibration.** Le verdict n'est lu que sur la vérification.
   Règle déjà appliquée par `tools/porte_cout.py:111-130`.
4. **Pas de re-tuning implicite.** La porte de coût reste à 0,125
   (`titanium/sizing.py:70`), héritée d'une décision antérieure ; le banc ne la rejuge pas.
5. **Époque déclarée.** Le banc prend l'époque du **corpus** via
   `epoque_rejeu.etat_epoque(..., pin=--empreinte)`, publie `corpus_epoch`,
   `workspace_engine_epoch`, `workspace_matches_corpus`, les manifestes retenus, et bloque
   en `ANALYSIS_BLOCKED` + sortie 2 sur toute atteinte à l'intégrité (contrat P0 `db5fd6e`).

### 4.3 Défaut trouvé en chemin, à corriger avant de s'appuyer dessus

`tools/porte_cout.py` **ne vérifie aucune époque** (aucune occurrence de `epoque_rejeu`
dans le fichier) et son cache `results/porte_cout_trades.parquet` **ne porte aucune
empreinte de génération**. Un cache produit par une génération antérieure serait relu en
silence par une analyse suivante : c'est exactement la panne du 25/08, déplacée d'un cran.
Le banc A/B n'utilisera pas ce cache tel quel — il produira le sien, estampillé — et je
propose une tâche distincte pour mettre `porte_cout.py` au contrat d'époque.

---

## 5. Les quatre variantes, définies formellement

Substrat commun : les lignes scellées du corpus `051f50ad`, filtrées par la porte de coût
`cost_r < 0,125`. Chaque variante est un couple **(filtre ex ante, pondération)** appliqué
aux **mêmes** lignes. L'appariement est exact et se fait sur `trade_id` : toutes les
variantes voient la même liste de décisions, ce qui rend la différence appariée légitime.

| # | nom | filtre | poids `w` |
|---|---|---|---|
| A | `baseline` | aucun (hors porte de coût) | `confiance.evaluer(support_pillars).pct / RISQUE_PIVOT_PCT` |
| B | `risque_4p_au_niveau_3p` | aucun | `confiance.evaluer(2).pct / RISQUE_PIVOT_PCT` pour toute strate |
| C | `sans_indices` | `asset_class != "indices"` | comme A |
| D | `combinaison` | `asset_class != "indices"` | comme B |

Notes :

- **A n'est pas « le corpus brut »**. Le corpus est à risque plat (1 unité par trade) ; la
  boucle réelle, elle, module par `titanium/confiance.py` (0,50 % à 2 supports, 1,00 % à 3,
  1,75 % à 4). La baseline doit donc **ajouter** la pondération vivante pour que B ait un
  sens. Sans cela, on comparerait le live à lui-même.
- `w` est lu en appelant `titanium/confiance.evaluer` en **fonction pure**, sans le
  modifier. `confiance.py` n'est pas dans `FICHIERS_MOTEUR` : son sha256 entre donc dans le
  bloc `code` du sceau, faute de quoi une édition ultérieure changerait les résultats du
  banc en silence.
- Le facteur de conviction du délibérateur (`POIDS_CONVICTION = 0,25`) n'est pas dans les
  artefacts : il est **fixé à sa valeur neutre** et cette hypothèse est publiée. Le banc ne
  prétend pas reproduire le sizing live, il isole **un** effet.
- C est un filtre **d'univers**, pas une décision de portefeuille : le capital libéré n'est
  pas réalloué. Une variante qui réalloue serait une contrainte de portefeuille, donc hors
  du domaine de validité (§1).

---

## 6. Walk-forward

**Primaire — la coupure scellée.** Chaque artefact porte `split ∈ {calibration,
verification}` et sa `coupure` temporelle aux 2/3 (`rejeu_univers.py:231, 280`). Le verdict
primaire se lit sur `verification`, jamais sur `calibration`. C'est la seule coupure
**scellée dans l'artefact** : elle ne peut pas être déplacée après coup, ce qui est
précisément sa valeur.

**Secondaire — origine glissante par calendrier.** k folds contigus sur `decision_at`
(k = 5 proposé, à confirmer par Hermes), chaque fold jugé sur le bloc suivant, avec
**embargo** d'une durée au moins égale à la durée maximale d'un trade du fold
(`max_barres = 200` barres LTF, soit 50 h en M15). Objet : la **stabilité**, publiée comme
sensibilité. Jamais le verdict.

**Ce que je n'utiliserai pas** : `titanium/analysis/walk_forward.py` et
`tools/walk_forward.py` découpent par **nombre de trades**, et le second lit MT5 en direct
sans sceau ni époque. Ni l'un ni l'autre n'est admissible ici. Un découpage par nombre de
trades mélange les calendriers de 147 symboles et rend l'embargo inexprimable.

**Contrôle d'attribution** : vérifier qu'aucun trade ne chevauche une frontière de fold en
étant compté deux fois, et publier le nombre de trades écartés par l'embargo. Un trade qui
ouvre avant la coupure et ferme après appartient au segment de sa **décision**, jamais aux
deux.

---

## 7. Coûts — ce que le banc mesure et ce qu'il ne mesure pas

Le moteur de rejeu ne modélise **que le spread** : demi-spread à l'entrée
(`backtest.py:326`), demi-spread à la sortie (`:339`), `cost_r = spread / r_unit` (`:68-74`).
**Aucune commission, aucun swap, aucun slippage, aucune latence.** Le spread lui-même est
la **médiane d'archive** du symbole (`rejeu_univers.py:528-538`), pas le spread de l'instant.

Le banc n'ajoutera **aucun** coût au point d'estimation : ajouter une commission absente
des données serait une hypothèse déguisée en mesure. Il publiera une **bande de
sensibilité** — effet de la variante recalculé sous 1 bps maker / 3 bps taker
(`config/execution_backtest.json`) et sous un scénario dégradé — en la nommant hypothèse.

Conséquence à imprimer dans le rapport : **un effet inférieur à la largeur de cette bande
n'est pas un effet.** Les variantes B et D déplacent des poids, pas des coûts ; C retire
une classe entière, donc retire aussi ses coûts : la comparaison C vs A doit être lue par
décision, jamais en total brut.

---

## 8. Estimands, métriques, décision

**Estimand primaire, un seul par variante** : différence appariée, par décision, de R
pondéré sur le segment de vérification.

```
Δ_V = moyenne_sur_les_décisions( w_V(d)·net_r(d)·1[d ∈ V] − w_A(d)·net_r(d)·1[d ∈ A] )
```

- **Appariement exact** sur `trade_id`. Une décision retirée par un filtre contribue 0 à ce
  bras, comme une expiration contribue 0 en ITT — la convention est la même que celle
  qu'Hermes a imposée à l'offset 633, section C.
- **Intervalle** : bootstrap par blocs, **grappes symbole × mois**, 10 000 tirages, graine
  publiée. Motif : deux trades du même symbole le même mois ne sont pas indépendants.
- **Leave-one-symbol-out** systématique : une variante dont le signe dépend d'un symbole
  n'est pas un résultat. Les indices comptent 30 symboles, dont deux négatifs.
- **Multiplicité** : trois comparaisons contre la baseline. Primaire unique par variante,
  sans correction ; tout axe secondaire (classe, TF, direction, tranche de spread) est
  exploratoire et corrigé BH à 5 %, conformément à Hermes 633.F.
- **Effectif minimal** : aucune cellule sous 60 observations ne conclut
  (`EFFECTIF_MIN`, précédent `porte_cout.py:44`). Publier n brut **et** n effectif
  (symboles, mois, épisodes).

**Publié à côté, jamais à la place** : R total, R par jour de calendrier, nombre de
décisions retirées, part de sorties au stop initial, PF, winrate, contribution par classe,
stabilité par fold. Un banc qui ne publierait que la moyenne par trade récompenserait
mécaniquement une variante qui trade moins.

**Règles de verdict, préenregistrées :**

- `GO_HYPOTHÈSE` : borne basse de l'IC 95 % > 0, signe stable en LOSO, stable sur ≥ 4 folds
  sur 5, effet supérieur à la bande de coût. **C'est une hypothèse pour une expérience
  live, jamais une promotion.**
- `NON_CONCLUANT` : IC contenant 0, ou instabilité, ou effet sous la bande de coût.
- `NO_GO` : borne haute < 0.
- Pour la variante C, le verdict est **suffixé `EXPLORATOIRE`** en toutes circonstances (§4.1).
- **Aucun verdict n'autorise un changement de la boucle live.** Ce banc ne mesure pas le
  lieu d'exécution : pas de commission, pas de swap, pas de file d'attente, pas d'anti-fade,
  pas de contrainte de portefeuille.

---

## 9. Fichiers, contrat d'époque, tests

**Aucun fichier de `FICHIERS_MOTEUR` n'apparaît ci-dessous.**

| fichier | rôle |
|---|---|
| `config/banc_ab_variantes.json` | spécification **préenregistrée** des 4 variantes ; sha256 scellé au rapport |
| `tools/banc_ab_entrees.py` | banc : lecture scellée, pondération, folds, bootstrap, sceau, `ANALYSIS_BLOCKED` |
| `tests/test_banc_ab_entrees.py` | contrat exécutable (ci-dessous) |
| `results/banc_ab_entrees.json` (+ `.blocked.json`) | rapport scellé, ou déclaration de blocage |
| `results/banc_ab_trades.parquet` | cache d'extraction **estampillé** `corpus_epoch` |
| `docs/BANC_AB_ENTREES.md` | lecture du rapport, domaine de validité, ce qu'il ne prouve pas |

Contrat d'époque, repris tel quel du P0 `db5fd6e` : `etat_epoque` avec `--empreinte`
(assertion, préfixe hexa 16-64), publication de `corpus_epoch` /
`workspace_engine_epoch` / `workspace_matches_corpus` / manifestes retenus, `requested` vs
`retained`, intégrité prioritaire sur le rendement, blocage atomique en fichier frère,
sortie 2. Le cache parquet porte l'empreinte du corpus et est **invalidé** si elle diffère.

**Tests, écrits avant l'implémentation :**

1. appariement exact : les 4 variantes voient la même liste de `trade_id` ;
2. un filtre qui lit `net_r`, `mae_r`, `mfe_r`, `exit_reason` ou `bar_sortie` est **refusé** ;
3. `w` de la baseline = `confiance.evaluer` (valeurs 0,50 / 1,00 / 1,75 aux trois ancrages) ;
4. la variante B rend un poids identique pour `3p` et `4p`, et ne change **aucune** ligne ;
5. la variante C ne retire que `asset_class == "indices"` et rien d'autre ;
6. `verification` n'est jamais lue pour choisir : test de chemin, pas de commentaire ;
7. époque : corpus mixte refusé, pin faux refusé, pin court juste accepté, écart d'arbre publié ;
8. cache estampillé : un cache d'une autre génération est ignoré, pas relu ;
9. embargo : aucun trade compté dans deux folds, nombre d'écartés publié ;
10. déterminisme : deux exécutions rendent le même `snapshot_id` et le même IC à graine fixée ;
11. `ANALYSIS_BLOCKED` : zéro décision par corruption ⇒ sortie 2 et rapport valide conservé ;
12. bande de coût : l'effet publié sans coût ajouté, la bande à côté, jamais mélangés.

---

## 10. Séquence de commits et portes

| # | contenu | porte d'entrée |
|---|---|---|
| 0 | **réparation de l'index GitNexus** (lot d'outillage distinct) | exigence Codex : aucune édition avant |
| 1 | `config/banc_ab_variantes.json` + `tests/test_banc_ab_entrees.py` (rouge) + `docs/BANC_AB_ENTREES.md` | **deux ACCEPT sur ce plan** |
| 2 | `tools/banc_ab_entrees.py` jusqu'au vert ; aucune mesure publiée | manifeste scellé `b28105b2` livré ; correspondance `4p`/« 3 piliers » figée par Claude (135ac385) |
| 3 | exécution sur le corpus scellé + rapport de preuves | avis de puissance d'Hermes (5a511f8b) : si un axe est sous-dimensionné, il n'est pas mesuré |
| 4 | revue croisée Claude / Codex / Hermes du rapport | — |

État GitNexus mesuré à l'instant, pour éviter un malentendu : le **côté lecture** répond
(`status`, `detect-changes`, `impact`), l'index est **périmé au commit `4c2ab54`**, et le
côté **écriture** échoue (`FTS index 'file_fts' is inconsistent`). Ce n'est donc pas une
indisponibilité totale, c'est un index figé et non réinscriptible : toute analyse d'impact
rendue d'ici là décrit le dépôt du 25/08 08:08, pas celui d'aujourd'hui. Je considère la
porte 0 comme bloquante pour l'implémentation, conformément à l'ordre de Codex.

---

## 11. Voie future pour les 315 expirations, sans toucher au moteur

Le blocage établi (Hermes 633.B, amendé 638) : `results/limit_lifecycle.ndjson` ne persiste
ni SL, ni TP, ni version de politique de sortie au moment du `placed`. Le bras marché
contrefactuel n'est donc pas prouvé reconstructible, et P1b reste BLOCK.

Trois choses, dans cet ordre, et **aucune ne touche `titanium/edge.py`** :

1. **Audit de reconstructibilité (mesure, pas code).** Matrice champ requis × source
   disponible pour les 315 : SL, TP, `r_unit`, ATR à la décision, version de gestion,
   latence, `decision_at`. Verdict par champ : disponible / dérivable / **UNKNOWN**. Un seul
   UNKNOWN ⇒ P1b reste BLOCK. Livrable factuel, pas une promesse.
2. **Enrichissement à l'émission, pour l'avenir seulement.**
   `titanium/execution/pending_context.py` et `tools/live_demo.py` ne sont **pas** dans
   `FICHIERS_MOTEUR` : y ajouter SL, TP et une empreinte de politique au moment du `placed`
   ne périme aucun artefact. Réserve honnête : `MODE_ENTREE = "MARCHE"` depuis `691adb6`,
   donc **aucune limite n'est plus posée** — cet enrichissement ne produira de données que
   si Florent décide de revenir en `LIMITE`. Il est donc préparatoire, pas urgent.
3. **Ne jamais confondre les deux bancs.** Le corpus de rejeu ne contient aucun ordre
   limite : il ne peut pas répondre à la question ITT des 315. Le banc A/B décrit ici et le
   contrefactuel des expirations sont deux objets disjoints, avec deux populations
   disjointes.

L'extraction de `ClosedTrade` / `TradeJournal` hors de `edge.py` reste reportée au prochain
rejeu complet (point 7 du contrat Codex 627) : la faire maintenant changerait l'empreinte
moteur et périmerait les 147 artefacts sur lesquels ce banc repose.

---

## 12. Tâches FX et anti-fade : verdict d'obsolescence

Vérification faite sur le code et les journaux courants.

**`95cb60be` — « retirer le FX de l'univers tradé » : OBSOLÈTE dans sa formulation.**
La décision est prise **et appliquée** : `tools/live_demo.py:202` `FX_SUSPENDU = True`
(commit `d417046`), appliquée en refus post-ENTER `tools/live_demo.py:1367-1380`.
Preuves vivantes que j'ai recomptées : 1 595 refus `FX_SUSPENDU` dans
`results/refus_live.ndjson`, le dernier au 25/08 15:36:07Z ; une seule clôture FX après
bascule, ordre posé avant la prise d'effet ; zéro FX parmi les 13 positions ouvertes
(4 crypto, 3 indices, 2 agricole, 2 métaux, 2 énergie). Nuance à retenir : le mécanisme est un
refus post-ENTER, le FX reste **scanné** — « retirer de l'univers » n'a pas été fait au sens
littéral, et c'est délibéré (traçabilité des occasions écartées).
Ce qui reste réellement : `results/suivi_bascule.json` est figé au 24/08 06:25:50Z avec
`n_apres = 0` et verdict INDÉCIS ; le `--veiller` n'a jamais rendu de verdict. Et la fenêtre
d'après est **contaminée** par la bascule `MODE_ENTREE` du 24/08 21:53Z : le `pnl_r` des
limites incluait une économie d'entrée que le marché n'a pas.
→ Reformulation proposée : « Clore la vérification de la suspension FX : rendre le verdict
de `tools/suivi_bascule.py` sur une fenêtre non contaminée par la bascule `MODE_ENTREE`
(re-horodater ou stratifier par mode), puis statuer définitif contre réouverture. »

**`d0bdf463` — « arbitrer le veto anti-fade » : OBSOLÈTE, l'arbitrage a été rendu.**
`titanium/risk/riskgate.py:63` `ANTI_FADE = ANTI_FADE_AUTORISE` (commit `f0d69a2`, 24/08
05:55:51Z) : le veto est **levé, pas supprimé**. Dernier refus `CONTRE_TENDANCE` dans
`results/refus_live.ndjson` : 918 refus au total, le **dernier au 24/08 05:43:17Z**, soit
avant la levée de 05:55:51Z. Aucun depuis. Le battement courant compte 450
`CONTRE_TENDANCE_AUTORISE`.
Ce qui reste, et qui n'est pas dans l'énoncé : (a) la persistance du drapeau (`4c2ab54`)
**n'est pas encore active** — la boucle armée tourne depuis le 24/08 21:53Z, le correctif
date du 25/08 06:08Z ; aucune des 13 positions ouvertes ne porte la clé, et 0 des 493 lignes de
`results/trades.ndjson` non plus, ce qui explique l'axe « 0/373 » du
diagnostic. Un redémarrage relève de Florent. (b) Le prédicat reste binaire là où
`docs/REVUE_COHERENCE_LIVE_20260824.md` §2bis montre qu'il faut un continuum : même
`trend = +1` couvre 2,55 à 118,21 ATR.
→ Reformulation proposée : « Calibrer le prédicat contre-tendance sur un continuum de
distance à la tendance : rendre d'abord la persistance effective, puis mesurer sur le rejeu
la performance par tranche de |prix − EMA200| / ATR avant toute redéfinition. »

**Deux autres tâches périmées par les événements du 24-25/08**, signalées sans y toucher :
`803b129b` (« une semaine de données, gardes actuelles conservées ») — trois gardes ont
changé depuis, le compteur repart au 24/08 21:53Z ; `394a10ce` (« 20 clôtures par
contexte ») — les clôtures accumulées viennent du régime limite, dont le `pnl_r` inclut une
économie d'entrée que le régime marché n'a pas. Les mélanger viole la règle d'époque déjà
actée.

---

## 13. Risques résiduels et ce que je demande à la revue

1. **Le banc mesure sur une population qui n'est pas le lieu d'exécution.** Pas de
   commission, pas de swap, pas de file, pas d'anti-fade, pas de portefeuille, spread
   médian d'archive. Un GO ne sera jamais une promotion.
2. **La vérification est déjà brûlée pour l'axe classe d'actif** (§4.1). La variante C est
   exploratoire par construction. Je préfère l'écrire que de le découvrir en revue.
3. **`POIDS_CONVICTION` est absent des artefacts** : la baseline fixe la conviction au
   neutre. C'est une hypothèse, pas une reproduction du sizing live.
4. **`4p` ne pèse que 8 672 trades de vérification** : l'estimand B repose sur cette strate.
   Si Hermes juge la puissance insuffisante, B n'est pas mesuré — mieux vaut ne rien rendre
   qu'un intervalle qui déborde.
5. **`porte_cout.py` n'a pas de contrat d'époque et son cache n'est pas estampillé** (§4.3) :
   tâche distincte proposée.
6. **L'index GitNexus est figé au `4c2ab54`** : toute analyse d'impact d'ici la réparation
   décrit un dépôt d'avant-hier.

**Questions explicites aux relecteurs :**

- **Codex** : la frontière de validité du §1 (une variante de risque n'est re-notable que si
  le risque ne décide pas qui entre) est-elle formulée assez étroitement pour que
  l'implémentation ne puisse pas la franchir sans qu'un test échoue ?
- **Claude** : acceptes-tu de figer la correspondance `4p` ↔ « 3 piliers » dans le manifeste
  scellé, plutôt que de la laisser au banc ? Et vois-tu un autre endroit où le vocabulaire
  live et le vocabulaire artefact divergent ?
- **Hermes** : k = 5 folds, embargo de 50 h, grappes symbole × mois, LOSO, BH 5 % sur les
  axes secondaires — quelle est la puissance disponible sur `4p` (8 672 trades, 30 symboles)
  et quel effectif minimal exiges-tu avant d'autoriser la mesure de B ?

Aucun code ne sera écrit avant réparation de GitNexus et deux ACCEPT.
