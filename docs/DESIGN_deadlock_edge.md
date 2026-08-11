# Le verrou d'edge — diagnostic et protocole d'ouverture du mode PROD

> Document de conception. **Aucun code de production n'a été modifié pour l'écrire.**
> Rédigé le 07/08/2026 après lecture de `titanium/edge.py`,
> `titanium/gates/confluence_gate.py`, `titanium/features/builder.py`,
> `titanium/execution/position_manager.py`, `titanium/backtest.py`,
> `tools/backtest.py`, `tools/live_demo.py`, `titanium/web/state.py`,
> `titanium/features/structure.py`, `titanium/features/candlesticks.py`.

---

## 0. Verdict en dix lignes

1. La circularité est **voulue** et il faut la garder : c'est le correctif du
   fail-OPEN de V12. Ce n'est pas elle le problème.
2. Le problème est ailleurs, et il est triple : **le circuit est coupé**
   (`edge_ok` n'est injecté nulle part), **l'unité de mesure est infalsifiable**
   (`MIN_SAMPLES=20` + seuil 0.05 R = 41 % de faux positifs), et **la clé de
   contexte sur-compte les piliers** (elle lit `strengths`, pas les portes).
3. Mesurer en EXPLORE pour autoriser PROD est **valide** — PROD est un
   sous-ensemble strict d'EXPLORE — **à condition de conditionner sur le
   nombre de piliers réellement validés**, jamais d'agréger.
4. L'amorçage par backtest est à **rejeter** comme source d'autorisation, pour
   une raison mécanique avant d'être statistique : il écrirait
   `edge_ok=False`, ce qui bloque **aussi en EXPLORE** et éteindrait la boucle
   d'accumulation.
5. Recommandation : **option (d) — promotion par strate**, avec borne basse
   bootstrap, double clé humaine à sens unique, et échelle à quatre échelons
   réversible.
6. **Le mode PROD réel n'ouvrira pas avant plusieurs mois.** Au rythme actuel
   du démo (~3 trades/jour, compte à 57 USD), l'échantillon requis pour la
   première strate demande de l'ordre de **12 à 16 mois**. Le seul levier qui
   ramène ça à ~4 mois est de **recapitaliser le compte démo** et d'élargir
   l'univers. C'est gratuit et c'est la première action à mener.

---

## 1. Diagnostic — la circularité est-elle un défaut ?

### 1.1 La boucle, telle qu'elle est écrite

```
mode PROD (require_edge=True)
  └─ confluence_gate.evaluate : `if require_edge and edge_ok is not True → BLOCK_EDGE_UNPROVEN`
       └─ edge_ok vient de feats["cost"]["edge_ok"]
            └─ build_feats(edge_ok=…) — paramètre, défaut None
                 └─ alimenté par EdgeBook.edge_ok_for(Context)
                      └─ exige MIN_SAMPLES = 20 trades clos dans CE contexte
                           └─ produits par la boucle live, qui tourne en EXPLORE
```

### 1.2 Position : la circularité est le comportement voulu

Elle est la traduction en code d'une règle qui n'est pas négociable : **on ne
met pas d'argent réel sur un contexte qu'on n'a pas mesuré.** V12 avait
`edge_ok=True` en dur ; c'était un fail-OPEN et il a coûté cher. Une boucle
« il faut avoir mesuré pour être autorisé, il faut trader pour mesurer » n'est
pas un bug : c'est la forme normale d'une exigence de preuve. Tout système
d'homologation a cette forme (on ne certifie pas un avion sans vol d'essai,
et le vol d'essai n'est pas un vol commercial).

Ce qui est en revanche un défaut, c'est que **le circuit d'essai n'existe pas
vraiment**. Trois ruptures, toutes vérifiées dans le code d'aujourd'hui.

### 1.3 Rupture n°1 — `edge_ok` n'est injecté par aucun appelant

`EdgeBook.edge_ok_for()` (`titanium/edge.py:178`) porte le docstring
« Raccourci pour alimenter `build_feats(edge_ok=…)` ». **Elle n'a aucun site
d'appel dans tout le dépôt.** Recherche exhaustive de `edge_ok=` :

| Appelant | Injection |
|---|---|
| `tools/live_demo.py:241` — `build_feats(get_rates(...), get_rates(...))` | ❌ aucune |
| `titanium/web/state.py:400` (scan) | ❌ aucune — le verdict est lu ligne 413 **pour l'affichage seulement** |
| `titanium/backtest.py:231` — `build_feats(..., with_indicators=False)` | ❌ aucune |
| `scan_v14.py` | ❌ aucune |

Conséquence : **même un journal parfaitement rempli laisserait `cost.edge_ok`
à `None` pour toujours.** Le mode PROD ne se déverrouillerait jamais, quelle
que soit la quantité de trades accumulés. La boucle n'est pas circulaire, elle
est **sectionnée**. Tant que ce point n'est pas réparé, tout le reste du débat
est théorique.

*Note de conception au passage* : l'injection est un problème d'ordre. Le
contexte (`side`, `n_pillars`) n'existe qu'**après** un passage de porte, mais
`edge_ok` est consommé **pendant** ce passage. Il faut donc deux passages —
voir §5.4. Ne pas reconstruire les features entre les deux (≈ 90 ms + 50
séries d'indicateurs) : muter `feats["cost"]["edge_ok"]` suffit.

### 1.4 Rupture n°2 — la clé de contexte sur-compte les piliers

`context_from_feats` (`titanium/edge.py:189`) compte les piliers ainsi :

```python
piliers = sum(1 for cle in ("trend_sr", "fair_value", "liquidity",
                            "ote_ob", "candle_confirmed")
              if float(strengths.get(cle, 0.0) or 0.0) > 0)
```

Or `strengths` est construit par `build_feats` (`builder.py:274-280`) sur la
**non-nullité**, pas sur l'**alignement** :

| Pilier | `strengths` > 0 quand… | La porte passe quand… | Concordance |
|---|---|---|---|
| `trend_sr` | `on_level_strength > 0` | `on_sr_level` ∧ (reversal ∨ tendance alignée) | ✅ sur un ENTER |
| `fair_value` | `on_fair_price_zone` | `on_fair_price_zone` | ✅ exacte |
| `liquidity` | `liquidity != 0` | `liquidity == side` | ❌ **sur-compte** |
| `ote_ob` | `ote_dir != 0` | `ote == side` | ❌ **sur-compte** |
| `candle_confirmed` | `abs(score) > 0` ⟺ `direction != 0` | `candle == side` | ❌ **sur-compte** |

Un setup accepté en EXPLORE avec 2 piliers alignés et deux piliers
**contre-alignés** est journalisé `…|5p`. Il atterrit dans le même seau qu'un
setup à 4 piliers alignés — c'est-à-dire dans le seau que le mode PROD irait
consulter.

C'est exactement le canal de contamination qui rend l'option (a) fausse *telle
qu'implémentée*. À corriger avant toute décision de promotion, sinon la
stratification ne stratifie rien.

### 1.5 Rupture n°3 — le résultat journalisé ignore commission et swap

`manage_once` (`position_manager.py:480`) appelle `journaliser_cloture` **sans
`cost_r`**, qui vaut donc 0.0 pour tous les trades live. Et le `pnl_r` est
reconstruit par les prix :

```python
pnl_r = (prix_sortie - st.entry) / st.r * st.side
```

Les prix de remplissage portent bien le spread. Ils ne portent **ni la
commission, ni le swap, ni les frais**, qui vivent dans `deal.commission`,
`deal.swap`, `deal.fee` — champs disponibles dans le `history_deals_get` déjà
lu par `_cloture_depuis_historique` (ligne 275) et **jetés**.

Sur du swing H4 avec `MAX_BARRES` équivalent à ~2 jours de portage, le swap
n'est pas un détail. Le CLAUDE.md du projet dit lui-même « le coût est le
tueur dominant, baseline PF ≈ 0.20 en réel ». Journaliser un P&L amputé de ses
coûts, c'est **biaiser systématiquement l'espérance vers le haut** — et c'est
précisément ce biais-là qui décide de l'ouverture du réel.

Correctif : lire le net en devise et diviser par le risque en devise (§5.3).
C'est aussi plus exact que la reconstruction par les prix (pas de problème de
remplissages partiels, de conversion de devise, ni d'arrondi de lot).

### 1.6 Rupture n°4 (mineure) — le seau poubelle

`manage_once:434` crée un `TrackedState` sans `context_key` quand il découvre
un ticket qu'il n'a pas vu naître. `journaliser_cloture:325` retombe alors sur
`f"{st.symbol}|?|?|0p"`. Le fichier `results/positions.json` d'aujourd'hui
contient précisément un état de ce type (`86630768`, ETHUSD, aucun
`context_key`, aucun `entry`) : à sa clôture, il ira nourrir un seau
`ETHUSD|?|?|0p` qui ne veut rien dire.

Ce n'est pas grave à n=1. Ça le devient quand ce seau grossit et se met à
franchir `MIN_SAMPLES`. À traiter comme une **ligne invalide**, pas comme un
contexte (§5.2).

### 1.7 Le vrai défaut : le critère d'autorisation ne prouve rien

C'est le point le plus grave du document.

Le critère actuel est : `n ≥ 20` **et** `moyenne(pnl_r) ≥ 0.05 R`.

Prenons un contexte dont l'espérance vraie est **exactement zéro** — un
contexte totalement sans edge. Avec un écart-type par trade σ ≈ 1.0 R, l'erreur
type de la moyenne sur 20 trades vaut `1.0/√20 = 0.2236 R`. La probabilité que
sa moyenne observée dépasse 0.05 R est :

```
P(X̄ ≥ 0.05) = 1 − Φ(0.05 / 0.2236) = 1 − Φ(0.224) = 41,1 %
```

**Un contexte sans aucun edge ouvre le mode PROD deux fois sur cinq.**

Et il ne s'agit pas d'un test isolé. La clé de contexte est
`(symbole, sens, famille, n_piliers)`. Sur l'univers de `live_demo.UNIVERS`
(17 actifs) × 2 sens × 2 familles × 3 valeurs plausibles de `n_pillars`, cela
fait **≈ 204 seaux testés en parallèle, sans aucune correction de multiplicité**.
Si tous étaient sans edge, ~84 d'entre eux ouvriraient PROD par pur hasard.

Le seuil `EDGE_THRESHOLD_R = 0.05` vaut **moins d'un quart d'une erreur type**.
Fonctionnellement, `MIN_SAMPLES=20 + 0.05 R` **est** le `edge_ok=True` de V12,
avec vingt trades de cérémonie devant. Le fail-OPEN n'a pas été supprimé ; il a
été retardé.

C'est la conclusion la plus importante de ce document, et elle est indépendante
de l'option retenue pour la suite.

---

## 2. La question du quorum — mesurer en EXPLORE pour autoriser PROD

### 2.1 Établir ce qui diffère réellement entre les deux modes

Lecture de `confluence_gate.evaluate` : `require_edge` n'a **que deux effets**.

```python
mode   = "prod" if require_edge else "explore"          # étiquette
quorum = QUORUM_PROD if require_edge else QUORUM_EXPLORE  # 3 au lieu de 2
...
if require_edge and edge_ok is not True: BLOCK_EDGE_UNPROVEN
```

Tout le reste est **identique, mot pour mot** : G0..G5, la dérivation du sens
(`_setup_side` : support→long, résistance→short), la famille, les modérateurs
émotion/coût, le `rank`. En aval, `OrchestratorConfig.rr_ratio` (2.0), le
`RiskGate`, `budget_for`, `ManageParams` (breakeven 0.8 / trail 1.2 / 0.8) ne
lisent pas le mode.

**Ce n'est pas une supposition, c'est une vérification.** Il n'y a donc aucun
confondant caché entre les deux modes : la seule différence de population est
le seuil sur le nombre de piliers de micro-structure.

### 2.2 Le fait ensembliste

Soit `S` = nombre de piliers de micro-structure alignés (0..4), et `T` =
`trend_sr` validé. La condition d'`ENTER` est `T ∧ (S ≥ q)`.

```
ENTER_prod    = { setups : T ∧ S ≥ 3 }
ENTER_explore = { setups : T ∧ S ≥ 2 }

⇒ ENTER_prod ⊊ ENTER_explore
```

**Le mode PROD ne trade pas une autre population : il trade une
sous-population du mode EXPLORE.** La question posée — « les setups EXPLORE ne
sont pas les mêmes que les setups PROD » — est vraie de l'ensemble pris en bloc
et fausse strate par strate. Tout setup PROD est un setup EXPLORE, observé dans
les mêmes conditions d'exécution, sur le même compte, avec le même stop, la
même taille et la même gestion.

EXPLORE n'est donc pas un *proxy* de PROD. C'est un **dispositif
d'échantillonnage sur-inclusif** : on abaisse le seuil d'acceptation pour
observer toute la distribution de force au lieu de sa seule queue. Estimer
conditionnellement sur `S ≥ 3` et déployer sur `S ≥ 3` n'est pas une
extrapolation — c'est une observation directe.

Ce schéma (sur-échantillonner la région permissive, estimer conditionnellement,
déployer sur la région restrictive) est standard et il est légitime. **Sous
trois conditions.**

### 2.3 Condition A — la strate doit être enregistrée exactement

C'est la rupture n°2 (§1.4). Aujourd'hui `n_pillars` peut dépasser le nombre de
piliers réellement validés. Tant que c'est vrai, le seau `4p` contient des
trades de quorum 2, et conditionner dessus ne conditionne rien.

**Sans ce correctif, l'option (a) est statistiquement invalide.** Avec lui, elle
devient exacte. Coût du correctif : une fonction de 10 lignes.

### 2.4 Condition B — pas d'effet de sélection entre strates

C'est la difficulté réelle, et elle n'est pas dans le code de la porte mais
dans celui de la boucle.

`tools/live_demo.py` impose `MAX_POSITIONS = 3` et `MAX_PAR_SYMBOLE = 1`. En
EXPLORE, un setup `S=2` peut **occuper un créneau** et empêcher, dix minutes
plus tard, un setup `S=3` d'être pris sur un autre actif. En PROD, ce setup
`S=2` n'existerait pas et le créneau serait libre.

Autrement dit : les trades `S≥3` observés en EXPLORE ne sont pas un
sous-échantillon **aléatoire** des trades `S≥3` que PROD aurait pris. Ce sont
ceux qui sont arrivés alors qu'un créneau était disponible. Et la disponibilité
d'un créneau est corrélée à l'activité du marché : les setups se déclenchent en
grappes lors des régimes volatils. **La censure est corrélée au résultat.**

Direction du biais : l'échantillon `S≥3` sous-représente les périodes de
grappe, donc les régimes de forte volatilité. Sur une stratégie de continuation
sur niveau S/R, ces régimes sont probablement ceux où les stops sont balayés le
plus souvent — le biais joue donc vraisemblablement en faveur d'une espérance
mesurée trop optimiste. « Vraisemblablement » : ce n'est pas mesuré.

Deux réponses, à appliquer toutes les deux :

1. **Supprimer le biais par construction** — allouer les créneaux par priorité
   décroissante de `S`, puis de `rank`. Un `S=2` ne prend un créneau que s'il
   n'y a aucun `S≥3` candidat dans le même tour. La strate PROD n'est alors
   jamais censurée par la strate EXPLORE, et le biais tend vers zéro.
2. **Le mesurer quand même** — journaliser chaque `ENTER` refusé pour cause de
   créneau, avec son `S`. Si le taux de refus de la strate `S≥3` reste sous
   ~5 %, il est négligeable. Au-delà, l'estimation doit être escomptée ou le
   plafond de créneaux relevé.

### 2.5 Condition C — le multiple testing, qui est le tueur

Traité en §1.7 et repris dans les critères chiffrés (§6). Résumé : conditionner
correctement ne sert à rien si l'on teste 204 hypothèses au seuil « moyenne
> 0.05 R sur 20 points ».

### 2.6 Réponse à la question posée

> Les setups EXPLORE (quorum 2/4) ne sont pas les mêmes que les setups PROD
> (quorum 3/4). Mesurer sur les uns pour autoriser les autres est-il valide ?

**Non, si l'on mesure sur l'agrégat EXPLORE. Oui, si l'on mesure sur la strate
`S ≥ 3` de l'échantillon EXPLORE.**

L'agrégat est invalide pour une raison précise et nommable : c'est une
agrégation sujette au paradoxe de Simpson. La strate `S=2` est mécaniquement la
plus peuplée (seuil le plus bas), elle domine donc la moyenne groupée. Elle peut
aussi bien masquer une strate `S≥3` mauvaise que noyer une strate `S≥3` bonne.
Les deux erreurs se produisent, et la direction n'est pas prévisible a priori.

La strate, elle, est une observation directe, sans extrapolation, sur des
trades réellement exécutés dans les conditions de PROD. Il n'y a rien à
inférer : il n'y a qu'à compter — correctement.

**Corollaire opérationnel** : le seau `3p` (donc `S=2`, quorum-2 uniquement)
ne doit **jamais** autoriser quoi que ce soit en PROD. Il ne sert qu'à deux
choses : mesurer le coût de la permissivité, et alimenter l'analyse
discriminante.

---

## 3. Tableau comparatif des options

| | (a) Promotion par contexte sur données EXPLORE | (b) Amorçage par backtest | (c) Validation humaine supervisée | **(d) Promotion par strate — recommandée** |
|---|---|---|---|---|
| **Source des chiffres** | trades live démo, quorum 2 | 2 319 trades rejoués | jugement | trades live démo, **conditionnés sur `S≥3`** |
| **Délai avant première ouverture** | mois | immédiat | immédiat | mois |
| **Biais n°1** | **Contamination de strate** — `n_pillars` lu sur `strengths` mélange quorum-2 et quorum-3 dans le même seau (§1.4) | **Modèle d'entrée irréalisable** — entrée à la clôture de barre (`backtest.py:255`), impossible en live : on décide *à* la clôture, on est rempli *au tick suivant* | **Biais de confirmation** — Florent valide sa propre méthode, avec un intérêt financier à ouvrir | Éliminé : clé reconstruite depuis `Decision.gates` |
| **Biais n°2** | **Censure par créneau** — `MAX_POSITIONS=3` : un `S=2` peut voler le créneau d'un `S≥3`, et la censure est corrélée à la volatilité (§2.4) | **Coûts absents** — pas de slippage, pas de swap sur ~2 jours de portage, spread figé à `spec.spread` au moment du run et non celui de la barre historique | **Non reproductible** — aucune trace de ce qui a été jugé, ni de pourquoi ; impossible à auditer ou à rejouer | Éliminé par priorisation des créneaux + journal des refus |
| **Biais n°3** | **Multiplicité** — ~204 seaux testés sans correction ; 41 % de faux positifs par seau à n=20 (§1.7) | **Le R n'est pas le même** — le backtest fixe `sl = 1.5·ATR`, `RR = 2.0` ; le live prend `out.stop_distance` du RiskGate, arrondi de lot vers le bas, souvent au lot minimum sur 57 USD. Le dénominateur de `pnl_r` diffère : **ce ne sont pas les mêmes variables aléatoires** | **Multiplicité aggravée** — un humain regardant 204 seaux bruités validera ceux qui « ont l'air bons », soit exactement le mécanisme de faux positif, sans la correction | Ramené à 4 cellules + Benjamini-Hochberg q=0.10 |
| **Biais n°4** | Régime unique (quelques mois de démo = un régime) | **Pas de concurrence de créneaux** ni de plafond par symbole | Non chiffrable | Régime unique — **non résolu, assumé** (§7) |
| **Objection décisive** | Aucune, une fois les trois biais traités | **Mécanique, pas statistique** : l'espérance globale est −0.0347 R, donc `edge_ok` serait écrit à **`False`** pour la quasi-totalité des seaux. Or `evaluate` fait `if edge_ok is False: BLOCK_EDGE_NEGATIVE` **quel que soit `require_edge`**. Verser le backtest **éteindrait la boucle d'accumulation elle-même.** | Aucune autorité de mesure : un humain ne peut pas estimer une borne basse à 95 % à l'œil | — |
| **Le précédent V12** | Compatible : on mesure en exécution réelle, pas en simulation | **Fatal.** XAUUSD PF 2.31 en Python → **0.90** au testeur natif. Rapporté à l'excédent au-dessus du seuil de rentabilité : `(0,90−1)/(2,31−1) = −0,08`. **La rétention d'edge mesurée du Python vers l'exécution native est nulle — et même légèrement négative.** Aucun facteur d'escompte positif n'est défendable sur la seule mesure que l'on possède. | Neutre | Compatible |
| **Verdict** | Bonne direction, granularité fausse | **Rejeté comme source d'autorisation.** Conservé comme source de *priorisation* | **Rejeté comme source d'autorisation.** Conservé comme **droit de véto** | **Retenu** |

### 3.1 Ce qu'on garde de (b) et de (c)

Rejeter n'est pas jeter.

**Du backtest**, on garde le droit de décider **quoi échantillonner**, jamais
quoi autoriser. Choisir de concentrer la boucle démo sur XAUUSD — seul actif
stable en walk-forward (PF 1.704, segments +0.290/+0.213/+0.236) — est sûr :
au pire on échantillonne le mauvais actif et on perd du temps. Choisir
d'autoriser XAUUSD en réel sur cette base, c'est refaire exactement l'erreur de
V12 sur le même symbole. La dissymétrie est totale et il faut l'inscrire en dur :

> **Le backtest oriente l'échantillonnage. Il n'autorise rien.**

**De la validation humaine**, on garde une **clé à sens unique** : Florent peut
refuser une cellule que les statistiques acceptent ; il ne peut pas accepter
une cellule qu'elles refusent. C'est la même asymétrie que celle imposée au LLM
dans l'architecture (« le LLM n'a jamais l'autorité d'exécution ; il module,
il ne crée pas »). La cohérence n'est pas cosmétique : c'est la seule forme
d'autorité humaine qui ne détruise pas la valeur de la mesure.

---

## 4. Recommandation — unique et assumée

> **Adopter l'option (d) : promotion par strate, gouvernée par une borne basse
> bootstrap, avec double clé humaine à sens unique et une échelle à quatre
> échelons réversible.**
>
> **Et accepter que le mode PROD réel n'ouvrira pas avant plusieurs mois.**

Cinq décisions, dans l'ordre où elles doivent être implémentées.

### D1 — Réparer le circuit avant tout débat (bloquant)

Rien n'a de sens tant que les quatre ruptures du §1 tiennent :

1. injecter `edge_ok` dans le chemin live (§5.4) ;
2. reconstruire la clé de contexte depuis `Decision.gates`, pas `strengths` (§5.2) ;
3. journaliser le P&L **net de commission et de swap**, lu dans les deals (§5.3) ;
4. traiter `…|?|?|0p` comme une ligne invalide, pas comme un contexte.

### D2 — Changer l'unité d'autorisation

`(symbole, sens, famille, n_piliers)` reste l'unité de **rapport**. Elle cesse
d'être l'unité d'**autorisation**.

L'unité d'autorisation devient la **cellule** :

```
cellule = (classe d'actif, strate de piliers)
classe d'actif ∈ { fx, indices, metaux, crypto }
strate         ∈ { S>=3 }        ← seule strate promouvable
```

**Quatre cellules**, au lieu de 204 seaux. Justification :

- on ne peut pas estimer 204 moyennes avec quelques centaines de trades ; on
  peut en estimer quatre ;
- ce sur quoi PROD commute est le **quorum**, donc le quorum est la bonne unité ;
- la classe d'actif est conservée parce que le coût en R diffère
  structurellement entre une paire FX major et le crypto — les agréger
  mélangerait des régimes de coût incomparables ;
- le sens (`long`/`short`) et la famille (`continuation`/`reversal`) sont
  **rapportés séparément** et surveillés, mais ne créent pas de cellule tant
  qu'aucune cellule n'a atteint la taille requise. On raffine quand on a de
  quoi payer le raffinement, pas avant.
- la sélectivité **par symbole** ne disparaît pas : elle passe dans le **filtre
  d'univers** (quels actifs la boucle balaie), qui peut être gouverné par le
  backtest sans danger — voir §3.1.

### D3 — Changer le critère

`moyenne ≥ 0.05 R` disparaît comme critère d'autorisation. Remplacé par le
protocole V12 transposé, chiffré en §6 : borne basse de bootstrap par blocs,
taille d'échantillon dérivée d'un calcul de puissance, stabilité temporelle sur
3 segments, correction de Benjamini-Hochberg, contrôle de la part du coût.

`EDGE_THRESHOLD_R = 0.05` et `MIN_SAMPLES = 20` **restent** dans `edge.py` pour
l'affichage du tableau de bord et pour l'analyse exploratoire. Ils sont
simplement retirés du chemin d'autorisation. Renommer serait plus propre mais
casserait `tests/test_edge_and_loop.py` — hors périmètre.

### D4 — Changer ce que « ouvrir PROD » veut dire

Pas un booléen. Un **cliquet à quatre échelons**, chacun réversible :

| Échelon | Ce qui tourne | Ce que ça coûte | Ce que ça mesure |
|---|---|---|---|
| **0** | EXPLORE démo, état actuel | rien | la distribution de `S` |
| **1** | **PROD fantôme** — `evaluate(feats, require_edge=True)` en parallèle, journalisé, **jamais exécuté** | ~0 (une évaluation de fonction pure par tour) | le taux d'`ENTER` du quorum 3, donc le **délai réel** avant l'échelon 2 |
| **2** | PROD sur **démo**, une cellule, taille normale | rien | l'edge de la cellule en conditions PROD |
| **3** | PROD **réel**, une cellule, lot minimum | argent | la rétention démo→réel |

L'échelon 1 est à monter **immédiatement**, avant même D1 si nécessaire : il ne
coûte rien et il répond à la seule question qu'on ne sait pas répondre
aujourd'hui — *« à quelle fréquence le quorum 3 se déclenche-t-il ? »*. Sans
cette réponse, tout planning est une invention.

Et une **règle de rétrogradation automatique**, sans laquelle la promotion est
un aller simple : toutes les 10 clôtures, si l'espérance glissante sur les 30
derniers trades de la cellule promue passe sous 0, la cellule **revient
automatiquement à l'échelon inférieur**. V12 n'a jamais rétrogradé quoi que ce
soit ; c'est une des raisons pour lesquelles son `RISKGATE_ENABLED=0` a survécu
si longtemps.

### D5 — Recapitaliser le compte démo (le seul vrai levier de délai)

L'estimation de délai est la partie la plus désagréable de ce document, alors
autant la poser franchement.

Débit actuel, mesuré : `results/loop_heartbeat.json` donne 22 tours, 264
évaluations, 66 `ENTER`, **1 ordre envoyé**, sur un compte à 57,01 USD avec
12 actifs portables sur 17. Le débit n'est pas limité par la porte (25 % de
taux d'`ENTER` au quorum 2 — la porte n'est pas sélective à ce réglage) mais par
trois plafonds : `MAX_POSITIONS = 3`, `MAX_PAR_SYMBOLE = 1`, et surtout le
dimensionnement, qui refuse la plupart des actifs à 57 USD d'equity.

| Scénario | Trades/jour | Dont strate `S≥3` | Par cellule (4) | Délai pour 120 trades/cellule |
|---|---|---|---|---|
| Aujourd'hui (57 USD, 12 actifs, 3 créneaux) | ~3 | ~1 | ~0,25 | **~16 mois** |
| Démo à 10 000 USD, 60 actifs, 10 créneaux | ~15 | ~5 | ~1,2 | **~3–4 mois** |

Un compte démo se recapitalise en une demande au courtier. C'est **gratuit,
sans risque, et ça divise le délai par quatre.** C'est la première action à
mener, avant toute ligne de code.

Deuxième levier : rien n'interdit de faire tourner **plusieurs comptes démo en
parallèle** sur le même terminal logique, avec des `magic` distincts. Le
`account` doit alors entrer dans la ligne de journal pour qu'on puisse
partitionner (§5.1).

### D6 — Ce que je n'ai pas retenu, et pourquoi je le dis

Je n'ai pas retenu d'assouplir `MIN_SAMPLES` ni de fusionner toutes les
cellules en une seule. C'était tentant : une cellule unique
« tout univers, `S≥3` » atteindrait 120 trades en ~4 mois même au débit
actuel. Je ne le recommande pas, parce que mélanger le coût en R du crypto et
celui d'une paire FX major produit une moyenne qui n'est le coût de personne —
et le coût est, par le CLAUDE.md du projet, le facteur dominant. Si Florent
veut trancher pour la vitesse, c'est le compromis à discuter en premier ; mais
c'en est un, pas une amélioration.

---

## 5. Pseudocode — à implémenter sans réinterprétation

> Conventions : `NOUVEAU` = à créer, `MODIFIÉ` = à changer.
> Aucun changement de comportement par défaut n'est autorisé sans drapeau
> explicite : la boucle doit continuer à tourner pendant l'implémentation.

### 5.1 `titanium/edge.py` — MODIFIÉ

```python
# ── Constantes existantes : INCHANGÉES. Elles quittent le chemin d'autorisation,
#    elles restent pour le tableau de bord et l'exploration.
MIN_SAMPLES = 20
EDGE_THRESHOLD_R = 0.05

@dataclass
class ClosedTrade:
    context: str
    pnl_r: float
    closed_at: str = ""
    ticket: str = ""
    exit_reason: str = ""
    cost_r: float = 0.0
    # ── NOUVEAUX CHAMPS. Tous avec un défaut : les lignes déjà écrites
    #    doivent rester lisibles.
    source: str = "live"          # 'live' | 'backtest'
    account: str = ""             # login MT5 — sépare démo, réel, et multi-comptes
    mode: str = "explore"         # mode de la porte à l'ENTRÉE ('explore'|'prod')
    quorum: int = 0               # quorum en vigueur à l'entrée (2 ou 3)
    support_pillars: int = 0      # S ∈ 0..4, piliers de micro-structure ALIGNÉS
    asset_class: str = ""         # 'fx'|'indices'|'metaux'|'crypto'
    risk_money: float = 0.0       # risque en devise du compte, à l'ouverture
    exact_cost: bool = False      # True ⇒ pnl_r vient du net en devise (deals)

    def to_json(self) -> str: ...   # inchangé (asdict)


class TradeJournal:
    def read_all(self) -> list[ClosedTrade]:
        """MODIFIÉ : lit les nouveaux champs avec des défauts tolérants.

        Deux règles ajoutées, toutes deux fail-closed :
          · une ligne dont `context` finit par '|?|?|0p' est SAUTÉE — c'est le
            seau poubelle de `journaliser_cloture`, pas un contexte ;
          · un `source` absent vaut 'live' pour les lignes historiques, mais
            'backtest' est reconnu et conservé tel quel.
        """
        # ... parsing existant, plus :
        #   source=str(d.get("source", "live"))
        #   account=str(d.get("account", ""))
        #   mode=str(d.get("mode", "explore"))
        #   quorum=int(d.get("quorum", 0) or 0)
        #   support_pillars=int(d.get("support_pillars", 0) or 0)
        #   asset_class=str(d.get("asset_class", ""))
        #   risk_money=float(d.get("risk_money", 0.0) or 0.0)
        #   exact_cost=bool(d.get("exact_cost", False))
        # et :
        #   if context.endswith("|?|?|0p"): continue


@dataclass
class EdgeBook:
    journal: TradeJournal
    min_samples: int = MIN_SAMPLES
    threshold_r: float = EDGE_THRESHOLD_R
    sources: frozenset = frozenset({"live"})   # NOUVEAU
    accounts: frozenset | None = None          # NOUVEAU — None = tous
    _cache: dict = field(default_factory=dict, repr=False)

    def refresh(self):
        """MODIFIÉ : filtre par `sources` et `accounts` avant d'agréger.

        Par DÉFAUT le backtest est exclu. C'est le point unique qui garantit
        qu'un `--journaliser` accidentel ne peut pas autoriser du réel.
        """
        for t in self.journal.read_all():
            if t.source not in self.sources: continue
            if self.accounts is not None and t.account not in self.accounts: continue
            ...


# ─────────────────────────────────────────────────────────────────────────────
def context_from_decision(symbol: str, decision) -> Context:      # NOUVEAU
    """Contexte EXACT, construit depuis les portes et non depuis `strengths`.

    C'EST LA SEULE FONCTION AUTORISÉE À PRODUIRE UNE CLÉ JOURNALISÉE.

    `strengths` mesure une FORCE (non-nullité) ; les portes mesurent un
    ALIGNEMENT (== side). Pour `liquidity`, `ote_ob` et `candle_confirmed`, un
    pilier contre-aligné a une force > 0 et une porte en échec : compter la
    force sur-évalue le nombre de piliers et fait remonter des setups de
    quorum 2 dans les seaux de quorum 3. Voir DESIGN_deadlock_edge §1.4.
    """
    passes = {g.name for g in decision.gates if g.passed}
    support = len(passes & {"fair_value", "liquidity", "ote_ob", "candle_confirmed"})
    n_pillars = support + (1 if "trend_sr" in passes else 0)
    return Context(symbol=symbol, side=decision.side,
                   family=decision.setup_family, n_pillars=n_pillars)


def context_from_feats(symbol, feats, side=None) -> Context:      # MODIFIÉ
    """Conservé pour l'aperçu du tableau de bord. Délègue désormais à la porte
    pour ne pas entretenir deux comptages divergents (piège n°1 de V12)."""
    from titanium.gates import confluence_gate
    d = confluence_gate.evaluate(feats, side=side)
    return context_from_decision(symbol, d)


ASSET_CLASSES = {          # NOUVEAU — table explicite, jamais devinée
    "fx":      {"EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD",
                "NZDUSD","EURGBP","EURJPY","GBPJPY"},
    "indices": {"US500","GER40","UK100","USTECH","NAS100","HSI"},
    "metaux":  {"XAUUSD","XAGUSD"},
    "crypto":  {"BTCUSD","ETHUSD"},
}

def asset_class_of(symbol: str) -> str:      # NOUVEAU
    """Rend '' pour un symbole inconnu. Une cellule '' n'est JAMAIS promouvable
    — un actif non classé ne doit pas hériter du feu vert d'un autre."""
```

### 5.2 `titanium/gates/confluence_gate.py` — MODIFIÉ

```python
GATE_VERSION = "2.1.0"   # le contrat de sortie change → on incrémente

@dataclass(frozen=True)
class Decision:
    ...                       # champs existants inchangés
    support_passed: int = 0   # NOUVEAU — S ∈ 0..4, piliers de micro-structure alignés
    quorum: int = 0           # NOUVEAU — quorum en vigueur pour CETTE décision

# dans evaluate(), la fermeture _decide() renseigne les deux champs :
def _decide(verdict, side_, gates_, code_, reasons_, rank_=0.0) -> Decision:
    support = sum(1 for g in gates_
                  if g.name in _SUPPORT_PILLARS and g.passed)
    return Decision(..., support_passed=support, quorum=quorum)
```

*Aucun changement de logique décisionnelle.* On expose ce que la fonction
calcule déjà en local (ligne 212). `tests/test_confluence_gate.py` (35 tests)
doit rester vert sans modification ; ajouter des tests qui vérifient
`support_passed` et `quorum` sur chaque chemin de sortie.

### 5.3 `titanium/execution/position_manager.py` — MODIFIÉ

```python
@dataclass
class TrackedState:
    ...                          # champs existants inchangés
    risk_money: float = 0.0      # NOUVEAU — risque en devise du compte
    mode: str = "explore"        # NOUVEAU
    quorum: int = 0              # NOUVEAU
    support_pillars: int = 0     # NOUVEAU
    asset_class: str = ""        # NOUVEAU
    account: str = ""            # NOUVEAU — login MT5
    # to_dict / from_dict : mêmes règles de tolérance qu'aujourd'hui.
    # from_dict DOIT continuer à relire un état écrit avant ce changement.


@dataclass(frozen=True)
class ClotureInfo:               # NOUVEAU — remplace le tuple (prix, ts)
    prix: float | None = None
    ts: str = ""
    profit: float = 0.0          # devise du compte
    commission: float = 0.0
    swap: float = 0.0
    fee: float = 0.0
    complet: bool = False        # True si des deals ont bien été lus

    @property
    def net(self) -> float:
        return self.profit + self.commission + self.swap + self.fee


def _cloture_depuis_historique(mt5, ticket: str) -> ClotureInfo:   # MODIFIÉ
    """Lit AUSSI commission, swap et fee — champs déjà présents sur les deals
    de `history_deals_get` et jetés jusqu'ici.

    Sans eux, `pnl_r` ignore les frais de portage et la commission : sur du
    swing H4, c'est un biais SYSTÉMATIQUE À LA HAUSSE de l'espérance, et c'est
    cette espérance-là qui décide d'ouvrir l'argent réel.
    """
    # somme sur TOUS les deals de la position (entrée + sorties partielles),
    # prix/ts pris sur le deal de sortie le plus récent, `complet=True` si
    # au moins un deal a été lu. Ne lève jamais → ClotureInfo() par défaut.


def journaliser_cloture(st: TrackedState, ticket: str, *,
                        info: ClotureInfo,                 # ← SIGNATURE MODIFIÉE
                        journal_path: Path) -> bool:
    """MODIFIÉ. Trois changements :

    1. `pnl_r` EXACT quand c'est possible :
           if info.complet and st.risk_money > 0:
               pnl_r    = info.net / st.risk_money
               cost_r   = -(info.commission + info.swap + info.fee) / st.risk_money
               exact    = True
           else:
               pnl_r    = (info.prix - st.entry) / st.r * st.side   # approx. actuelle
               cost_r   = 0.0
               exact    = False        # ← EXCLUT la ligne des promotions
       Le net en devise est plus juste que la reconstruction par les prix :
       il absorbe remplissages partiels, conversion de devise et arrondi de lot.

    2. Les nouveaux champs de contexte sont recopiés dans `ClosedTrade`
       (source='live', account, mode, quorum, support_pillars, asset_class,
        risk_money, exact_cost).

    3. Le fallback `f"{st.symbol}|?|?|0p"` reste écrit (traçabilité) mais est
       désormais filtré à la LECTURE par `TradeJournal.read_all` (§5.1).

    Le reste — idempotence par `live:<ticket>`, écriture de `excursions.ndjson`,
    règle du giveback, drapeau `censored` — est INCHANGÉ.
    """

# ── Optimisation obligatoire au passage :
# `journaliser_cloture` relit AUJOURD'HUI tout le journal à chaque clôture
# (`any(t.ticket == marque for t in journal.read_all())`), soit O(n) par trade.
# À 5 000 lignes c'est encore acceptable ; ça ne l'est plus si le backtest est
# un jour versé dans un fichier voisin. Remplacer par un index de tickets chargé
# une fois par passage de `manage_once` et passé en argument.
```

Et dans `manage_once`, la purge (ligne 477) passe `info=` au lieu de
`prix_sortie=`/`ts_exit=`.

### 5.4 `tools/live_demo.py` — MODIFIÉ

```python
# ── A. INJECTION DE L'EDGE (répare la rupture n°1)
def injecter_edge(feats: dict, decision, *, symbole: str,
                  livre, promotions) -> bool | None:
    """Renseigne `feats['cost']['edge_ok']` À PARTIR de la première décision.

    POURQUOI DEUX PASSAGES DE PORTE. `edge_ok` est consommé PENDANT
    l'évaluation, mais le contexte (sens, nombre de piliers) n'existe qu'APRÈS.
    On évalue donc une première fois en EXPLORE (gratuit, fonction pure), on en
    tire le contexte, on écrit `edge_ok`, puis on réévalue dans le mode voulu.
    On NE reconstruit PAS les features : `build_feats` coûte ~90 ms et jusqu'à
    50 séries d'indicateurs. Muter `feats['cost']` suffit.

    Rend la valeur injectée (True / False / None) pour la trace.
    """
    ctx = context_from_decision(symbole, decision)
    cellule = Cell(asset_class_of(symbole), "S>=3")
    if decision.support_passed >= 3 and promotions.is_promoted(cellule.key()):
        valeur = True
    else:
        valeur = livre.verdict_for(ctx).edge_ok      # None ou False, jamais True
    feats.setdefault("cost", {})["edge_ok"] = valeur
    return valeur

# Point d'appel, dans tour(), entre build_feats et run_once :
#     d0    = confluence_gate.evaluate(feats, require_edge=False)
#     injecter_edge(feats, d0, symbole=sym, livre=livre, promotions=promos)
#     out   = run_once(sym, feats, ctx_risque, config=cfg)   # relit feats['cost']
#
# ⚠️ INVARIANT : `edge_ok=True` ne peut venir QUE du registre de promotions,
#    jamais d'un calcul à la volée. Un seul point d'écriture, ici.


# ── B. PRIORISATION DES CRÉNEAUX (supprime le biais de censure, §2.4)
# tour() est aujourd'hui « évaluer et envoyer dans la foulée ». Le scinder :
#
#   candidats = []
#   for sym in tradables:
#       ... build_feats, injecter_edge, run_once ...
#       if out.gate_verdict == "ENTER":
#           candidats.append(Candidat(sym, feats, out, d0.support_passed, d0.rank))
#
#   # Les setups PROD passent AVANT les setups quorum-2. Sans cela, un S=2 vole
#   # le créneau d'un S=3 et l'échantillon de la strate PROD devient censuré de
#   # façon corrélée à la volatilité — biais non corrigeable a posteriori.
#   candidats.sort(key=lambda c: (-c.support_passed, -c.rank))
#
#   for c in candidats:
#       if creneaux_restants <= 0:
#           journaliser_refus(c, raison="MAX_POSITIONS"); continue
#       if par_symbole.get(c.sym, 0) >= MAX_PAR_SYMBOLE:
#           journaliser_refus(c, raison="MAX_PAR_SYMBOLE"); continue
#       ... envoi ...


# ── C. JOURNAL DES REFUS (mesure le biais résiduel)
def journaliser_refus(c, *, raison: str) -> None:
    """Une ligne dans results/slots_refuses.ndjson :
       {at, symbol, support_passed, rank, raison}
    Sert à UNE chose : calculer le taux de censure de la strate S>=3.
    Au-delà de ~5 %, l'estimation d'espérance doit être escomptée."""


# ── D. PROD FANTÔME (échelon 1 — coût nul, à monter en premier)
def prod_fantome(sym: str, feats: dict) -> None:
    """Évalue la porte en mode PROD et journalise le verdict. N'EXÉCUTE RIEN.

    Répond à la seule question qu'on ne sait pas répondre aujourd'hui : à
    quelle fréquence le quorum 3 se déclenche-t-il ? Sans ce chiffre, aucun
    planning d'ouverture n'est autre chose qu'une invention.

    Sortie : results/prod_shadow.ndjson
       {at, symbol, verdict, code, side, support_passed, rank, bar_time}
    """
    d = confluence_gate.evaluate(feats, require_edge=True)
    # écrire la ligne ; ne jamais lever


# ── E. ATTACHEMENT DU CONTEXTE (complète _attacher_contexte, ligne 141)
#   TrackedState(..., risk_money=budget.risk_money,
#                     mode="explore",              # mode réel de la décision
#                     quorum=d0.quorum,
#                     support_pillars=d0.support_passed,
#                     asset_class=asset_class_of(sym),
#                     account=str(compte.login),
#                     context_key=context_from_decision(sym, d0).key())
```

### 5.5 `titanium/analysis/promotion.py` — NOUVEAU

```python
"""Décide si une cellule peut passer en PROD. Ne trade pas, n'exécute rien.

Réutilise `_benjamini_hochberg` de `titanium.analysis.discriminants` : le
correcteur de multiplicité existe déjà et a été validé sur vérité connue
(un bruit à p brut 0.072 ramené à 0.25). Ne pas en écrire un second.
"""

STRATE_MIN_SUPPORT   = 3        # seule la strate quorum-PROD est promouvable
MIN_TRADES_CELLULE   = 60       # plancher dur — voir §6 pour la dérivation
BORNE_BASSE_PLANCHER = 0.05     # la borne 95 % doit dépasser CE chiffre, pas 0
PF_MIN               = 1.30
BOOTSTRAP_N          = 10_000
BLOC                 = 5        # bootstrap par BLOCS : les trades sont corrélés
FDR_Q                = 0.10
SEGMENTS             = 3
PART_COUT_MAX        = 0.40     # coût moyen < 40 % de l'espérance
PART_EXACT_MIN       = 0.90     # ≥ 90 % de l'échantillon avec exact_cost=True
FENETRE_DEMOTION     = 30

@dataclass(frozen=True)
class Cell:
    asset_class: str
    support_bucket: str = "S>=3"
    def key(self) -> str: return f"{self.asset_class}|{self.support_bucket}"

@dataclass
class PromotionVerdict:
    cell: str
    n: int = 0
    expectancy_r: float = 0.0
    lower_95_r: float = 0.0
    profit_factor: float = 0.0
    sigma_r: float = 0.0
    segments_r: list[float] = field(default_factory=list)
    mean_cost_r: float = 0.0
    part_exact: float = 0.0
    p_value: float = 1.0
    q_value: float = 1.0
    n_requis: int = 0            # recalculé avec le sigma OBSERVÉ
    eligible: bool = False       # les statistiques disent oui
    bloquants: list[str] = field(default_factory=list)   # critères en échec, NOMMÉS

def block_bootstrap_lower(xs: list[float], *, alpha: float = 0.05,
                          n_boot: int = BOOTSTRAP_N, bloc: int = BLOC,
                          seed: int = 20260807) -> float:
    """Borne basse unilatérale à (1−alpha) de la moyenne, par blocs mobiles.

    PAR BLOCS, et pas i.i.d. : les trades arrivent en grappes de régime. Un
    bootstrap i.i.d. sur des données auto-corrélées RESSERRE artificiellement
    l'intervalle et fabrique de la significativité. Graine fixe : une décision
    d'ouvrir de l'argent réel doit être reproductible à l'identique.
    Aucune dépendance à scipy — `random.Random(seed)` suffit.
    """

def sample_size_required(sigma: float, observed_r: float, *,
                         plancher: float = BORNE_BASSE_PLANCHER,
                         z: float = 1.645) -> int:
    """n minimal pour que la borne basse dépasse `plancher`.

        n ≥ ( z · sigma / (observed_r − plancher) )²

    À N'UTILISER QUE POUR PLANIFIER, jamais pour justifier a posteriori un n
    déjà atteint. `MIN_TRADES_CELLULE` reste un plancher dur indépendant.
    """

def evaluate_cells(trades: list[ClosedTrade]) -> list[PromotionVerdict]:
    """Applique les 8 critères du §6 à chaque cellule, puis Benjamini-Hochberg
    sur l'ENSEMBLE des cellules testées (pas seulement celles qui passent —
    sinon la correction ne corrige rien).

    Filtres d'entrée, tous obligatoires :
        source == 'live'   ·   support_pillars >= STRATE_MIN_SUPPORT
        asset_class != ''  ·   account dans le périmètre demandé
    """

def check_demotion(trades: list[ClosedTrade],
                   promues: dict) -> list[str]:
    """Cellules à rétrograder : espérance des FENETRE_DEMOTION derniers trades
    de la cellule ≤ 0. Rend les clés. Appelé toutes les 10 clôtures."""
```

### 5.6 `titanium/analysis/promotion_registry.py` — NOUVEAU

```python
"""Registre des promotions — le SEUL endroit d'où `edge_ok=True` peut venir.

Fichier : results/promotions.json, append-only en esprit (on ne supprime pas,
on révoque). Une promotion révoquée reste visible : l'historique des décisions
d'ouverture est une pièce d'audit, pas un réglage.
"""

def load(path: Path) -> dict[str, dict]
def is_promoted(path: Path, cell_key: str) -> bool
    """True SEULEMENT si : entrée présente ∧ revoked_at is None ∧ rung >= 2
       ∧ approved_by non vide. Toute anomalie ⇒ False (fail-closed)."""

def promote(path: Path, cell_key: str, verdict: PromotionVerdict, *,
            approved_by: str, rung: int) -> None
    """Lève ValueError si `verdict.eligible` est False, ou si `approved_by`
    est vide.

    DOUBLE CLÉ À SENS UNIQUE. L'humain peut REFUSER une cellule que les
    statistiques acceptent ; il ne peut pas ACCEPTER une cellule qu'elles
    refusent. Même asymétrie que celle imposée au LLM dans l'architecture :
    il module, il ne crée pas. C'est la seule forme d'autorité humaine qui ne
    détruise pas la valeur de la mesure.
    """

def revoke(path: Path, cell_key: str, *, reason: str) -> None
```

Schéma d'une entrée :

```json
{
  "fx|S>=3": {
    "rung": 2,
    "promoted_at": "2026-11-03T09:12:00+00:00",
    "approved_by": "florent",
    "revoked_at": null,
    "revoke_reason": "",
    "verdict": { "n": 78, "expectancy_r": 0.283, "lower_95_r": 0.071,
                 "profit_factor": 1.41, "sigma_r": 1.12, "q_value": 0.04,
                 "segments_r": [0.31, 0.19, 0.34], "mean_cost_r": 0.062 }
  }
}
```

### 5.7 `tools/promotion.py` — NOUVEAU (CLI)

```
.venv\Scripts\python.exe tools\promotion.py --etat
    Tableau : cellule · n · espérance · borne basse 95 % · PF · segments ·
    q · n requis (sigma observé) · ÉLIGIBLE/BLOQUÉ + critères bloquants nommés.

.venv\Scripts\python.exe tools\promotion.py --promouvoir "fx|S>=3" --echelon 2 --par florent
    Refuse si le verdict n'est pas éligible. Refuse sans --par.

.venv\Scripts\python.exe tools\promotion.py --revoquer "fx|S>=3" --motif "..."
.venv\Scripts\python.exe tools\promotion.py --verifier-demotion
```

### 5.8 `titanium/web/state.py` — MODIFIÉ

`edge()` (ligne 169) rend en plus `promotions` : les 4 cellules, leur n, leur
borne basse, leur q, et le nombre de trades restants avant `n_requis`. C'est
la seule page qui répond à « dans combien de temps ? » avec un chiffre plutôt
qu'une impression.

### 5.9 Tests attendus

| Fichier | Contenu minimal |
|---|---|
| `tests/test_promotion.py` | borne basse d'un échantillon connu ; bootstrap par blocs ≠ i.i.d. sur données auto-corrélées ; BH appliqué à TOUTES les cellules ; `n_requis` croît en σ² ; chaque critère du §6 bloque seul ; rétrogradation déclenchée |
| `tests/test_promotion_registry.py` | `promote` lève sans `approved_by` ; `promote` lève sur verdict non éligible ; `is_promoted` False si révoqué ; registre corrompu ⇒ False (fail-closed) |
| `tests/test_contexte_exact.py` | un pilier **contre-aligné** ne compte pas dans `n_pillars` (le test qui manque aujourd'hui, §1.4) ; `context_from_feats` et `context_from_decision` concordent |
| `tests/test_journal_couts.py` | `pnl_r` = net/risk_money quand les deals sont lus ; `exact_cost=False` sur le fallback ; commission et swap descendent dans `cost_r` |
| `tests/test_injection_edge.py` | `edge_ok=True` impossible sans entrée au registre ; deux passages de porte, un seul `build_feats` |
| `tests/test_priorite_creneaux.py` | un `S=3` passe devant un `S=2` ; le refusé est journalisé avec son `S` |
| `tests/test_edge_sources.py` | `EdgeBook` par défaut ignore `source='backtest'` ; une ligne `…\|?\|?\|0p` est sautée |

---

## 6. Critères chiffrés d'ouverture du mode PROD

### 6.1 Pourquoi le critère actuel ne peut pas être conservé

Sous H₀ (espérance vraie nulle, σ = 1.0 R), probabilité d'ouvrir PROD :

| Critère | n | Faux positif par seau | Seaux testés | Ouvertures attendues sous H₀ |
|---|---|---|---|---|
| **Actuel** : moyenne ≥ 0.05 R | 20 | **41,1 %** | ~204 | **~84** |
| Proposé : borne basse 95 % > 0.05 R | 60 | 2,1 % | 4 | 0,08 |
| Proposé + Benjamini-Hochberg q=0.10 | 60 | < 2,1 % | 4 | < 0,08 |

Le passage de 84 à moins de 0,1 ouverture fortuite est l'objet entier de la
refonte du critère.

### 6.2 Les huit critères — tous obligatoires, aucun n'est compensable

Une cellule `(classe d'actif, S ≥ 3)` est **éligible** si et seulement si :

| # | Critère | Valeur | Justification |
|---|---|---|---|
| **C1** | trades clos dans la cellule | **n ≥ 60** | plancher dur, dérivé en §6.3 |
| **C2** | source | `live` uniquement, `support_pillars ≥ 3` | le backtest n'autorise rien (§3) ; la strate quorum-2 non plus (§2.6) |
| **C3** | part de l'échantillon à coût exact | **≥ 90 %** (`exact_cost=True`) | un P&L reconstruit par les prix ignore commission et swap (§1.5) |
| **C4** | borne basse unilatérale 95 %, bootstrap par blocs (10 000 tirages, blocs de 5) | **> +0,05 R** | pas « > 0 » : la marge remplace un facteur d'escompte qu'on ne sait pas calibrer (§6.4) |
| **C5** | profit factor de la cellule | **≥ 1,30** | V12 exigeait 1,20 ; relevé, voir §6.4 |
| **C6** | stabilité temporelle sur 3 segments consécutifs (`decouper_walk_forward`) | espérance > 0 sur **≥ 2 segments sur 3**, et **aucun segment < −0,10 R** | un edge présent sur un tiers seulement est un artefact — règle déjà écrite dans `titanium/backtest.py` |
| **C7** | q de Benjamini-Hochberg sur l'ensemble des cellules testées | **q ≤ 0,10** | la correction existe déjà (`discriminants._benjamini_hochberg`) et a été validée sur vérité connue |
| **C8** | part du coût dans l'espérance : `mean_cost_r / expectancy_r` | **< 0,40** | si l'edge est du même ordre que les frais, c'est un artefact de timing de frais, pas un edge |

**Et deux conditions non statistiques :**

| # | Condition | |
|---|---|---|
| **H1** | approbation humaine explicite (`approved_by` non vide) | à **sens unique** : peut refuser l'éligible, ne peut pas accepter l'inéligible |
| **H2** | taux de censure par créneau de la strate `S≥3` (`slots_refuses.ndjson`) | **< 5 %**, sinon le biais du §2.4 n'est pas négligeable et l'estimation doit être escomptée ou l'échantillon repris |

### 6.3 Dérivation de n — et pourquoi 20 ne pouvait pas marcher

Le critère C4 s'écrit `X̄ − 1,645·σ/√n > 0,05`. Donc la moyenne observée
minimale pour franchir la barre est `X̄min = 0,05 + 1,645·σ/√n`.

Avec σ = 1,0 R :

| n | X̄ min. pour passer | Puissance contre un edge vrai de +0,30 R | Faux positif sous H₀ |
|---|---|---|---|
| 20 | **+0,418 R** | 30 % | 2,4 % |
| 40 | +0,310 R | 47 % | 2,2 % |
| **60** | **+0,262 R** | **62 %** | **2,1 %** |
| 100 | +0,215 R | 80 % | 2,0 % |
| 150 | +0,184 R | 92 % | 1,9 % |

Lecture :

- à **n = 20**, il faudrait une espérance observée de **+0,42 R** — soit un PF
  de l'ordre de 2 — pour franchir une borne basse honnête. Aucun seau ne le
  fera. `MIN_SAMPLES = 20` n'est pas un seuil bas : c'est un seuil qui ne
  démontre **rien**, dans un sens comme dans l'autre.
- **n = 60** est le plancher retenu : il admet une espérance observée de
  +0,26 R, ce qui est du même ordre que le meilleur résultat de backtest
  connu (XAUUSD, +0,24 R). C'est exigeant mais atteignable.
- **n = 100** est le point où le test acquiert une vraie puissance (80 %
  contre un edge de +0,30 R).

**La procédure est délibérément sous-puissante.** À n = 60 elle refusera
environ 4 edges réels sur 10. C'est le sens correct de l'erreur : refuser un
edge réel coûte du temps, l'accepter à tort coûte le compte.

**Mise en garde sur σ.** Tous ces chiffres supposent σ = 1,0 R. Avec un R:R de
2,0, du breakeven et du trailing, σ est probablement plus proche de **1,3–1,4 R**
(mélange de −1, de +2 et de sorties trailing). **n varie en σ² : si le σ mesuré
vaut 1,4, doubler tous les n du tableau** (60 → 118, 100 → 196). D'où
`sample_size_required(sigma, observed_r)` en §5.5 : le σ ne se suppose pas, il
se calcule sur l'échantillon accumulé, et le n requis se recalcule avec lui à
chaque rapport.

### 6.4 Pourquoi une marge de +0,05 R plutôt qu'un facteur d'escompte

Il était tentant d'appliquer un « facteur V12 » : la stratégie a produit
XAUUSD PF 2,31 en Python contre 0,90 au testeur natif, donc on garderait une
fraction de l'edge simulé.

Le calcul montre qu'aucune fraction positive n'est défendable :

```
rétention = (PF_natif − 1) / (PF_python − 1) = (0,90 − 1) / (2,31 − 1) = −0,076
```

**L'excédent au-dessus du seuil de rentabilité n'a pas été escompté : il a été
entièrement détruit, et un peu plus.** Sur la seule mesure que nous possédions
du passage simulation → exécution, la rétention d'edge est **nulle**. Un
multiplicateur d'escompte positif serait donc plus optimiste que notre unique
donnée. C'est ce qui rend l'option (b) indéfendable, et non un scrupule
méthodologique général.

En revanche, ce chiffre mesure `simulation → exécution réelle`. Il ne mesure
**pas** `démo live → réel live`, qui est la transition de l'échelon 2 vers
l'échelon 3. Pour celle-là nous n'avons aucune mesure. On ne fabrique donc pas
un facteur : on exige une **marge** exprimée dans la même unité que la
grandeur mesurée (`borne basse > +0,05 R` plutôt que `> 0`), et on relève le PF
de 1,20 à 1,30. Une marge chiffrée qu'on assume vaut mieux qu'un facteur
d'escompte inventé.

**Et on mesure la transition manquante quand on y sera** : l'échelon 3 tourne
au lot minimum sur le compte réel 60261188 pendant que l'échelon 2 continue en
parallèle sur le démo, dans la même cellule. Comparer les deux séries donne le
facteur démo→réel réel, empiriquement, au lieu de l'emprunter à V12.

### 6.5 Franchissement des échelons

| Passage | Condition |
|---|---|
| 0 → 1 (PROD fantôme) | aucune — à faire immédiatement, coût nul |
| 1 → 2 (PROD sur démo) | C1..C8 + H1 + H2 sur la cellule |
| 2 → 3 (PROD réel, lot min) | 30 trades supplémentaires **à l'échelon 2**, espérance restée > 0, **et** ré-évaluation complète de C1..C8 sur l'échantillon augmenté |
| Rétrogradation (auto) | espérance des 30 derniers trades de la cellule ≤ 0 — vérifiée toutes les 10 clôtures, **sans intervention humaine** |
| Rétrogradation (humaine) | à tout moment, sans justification requise |

### 6.6 Délai réaliste, chiffré

| Configuration | Trades/j | Strate `S≥3` | Par cellule | n=60 | n=120 (si σ≈1,4) |
|---|---|---|---|---|---|
| Aujourd'hui — 57 USD, 12 actifs, 3 créneaux | ~3 | ~1/j | ~0,25/j | ~8 mois | **~16 mois** |
| Démo 10 000 USD, 60 actifs, 10 créneaux | ~15 | ~5/j | ~1,2/j | ~7 semaines | **~3,5 mois** |
| Idem + fusion des 4 cellules en 1 | ~15 | ~5/j | ~5/j | ~2 semaines | ~3,5 semaines |

**Dans le meilleur scénario raisonnable — démo recapitalisé, univers élargi,
quatre cellules — la première cellule devient éligible vers la fin de
l'automne 2026, et l'échelon 3 (argent réel) pas avant le début 2027.** Sans
recapitalisation du démo, il faut compter en années.

Je le formule sans ambiguïté puisque c'était demandé : **oui, la recommandation
implique de ne pas ouvrir PROD avant des mois.** Toute proposition qui ouvrirait
avant est, sur les chiffres ci-dessus, une proposition d'ouvrir sur du bruit.

---

## 7. Ce qui reste incertain

### 7.1 σ n'est pas mesuré

Toute la dérivation de §6.3 repose sur σ. Je l'estime entre 1,0 et 1,4 R sans
le mesurer. `n` varie en σ², donc l'incertitude sur le délai est d'un facteur
2. **Action** : calculer σ sur les 2 319 trades de backtest dès maintenant
(gratuit, disponible) pour avoir un ordre de grandeur, puis le recalculer sur
l'échantillon live à chaque rapport de promotion. Le σ du backtest sera
lui-même biaisé (pas de swap, R différent) mais il donne le bon ordre.

### 7.2 La fréquence du quorum 3 est inconnue

L'hypothèse « la strate `S≥3` représente ~30 % des `ENTER` » n'est fondée sur
rien. Le seul chiffre disponible est 66 `ENTER` sur 264 évaluations **au quorum
2**. Si le quorum 3 ne se déclenche que 5 % du temps, tous les délais du §6.6
sont à multiplier par six. **C'est ce que l'échelon 1 (PROD fantôme) mesure, et
c'est pour ça qu'il faut le monter en premier.**

### 7.3 Démo ≠ réel, et on ne sait pas de combien

Les serveurs démo remplissent typiquement mieux que les serveurs réels
(slippage optimiste, pas de rejet, pas de requote, exécution instantanée). Un
edge mesuré sur le démo 50061786 n'est pas garanti sur le réel 60261188. §6.4
propose de mesurer ce facteur au lieu de le supposer, mais cette mesure n'existe
qu'à l'échelon 3 — c'est-à-dire après avoir déjà engagé de l'argent. Il y a là
un résidu de circularité qu'aucun protocole ne supprime ; le lot minimum est la
seule réponse, et c'en est une faible.

### 7.4 Un seul régime

Trois à quatre mois de démo, c'est un régime de marché. Aucune borne basse,
aucun bootstrap par blocs, aucune correction de multiplicité ne compense
l'absence de diversité de régime. Une cellule promue en novembre 2026 aura été
validée sur l'automne 2026 et rien d'autre. La rétrogradation automatique
(§6.5) est le seul filet, et c'est un filet réactif : il constate le changement
de régime, il ne l'anticipe pas.

### 7.5 La classification d'actifs est arbitraire

`ASSET_CLASSES` regroupe par intuition (fx / indices / métaux / crypto). Rien
ne dit que le coût en R d'EURUSD et de GBPJPY appartiennent au même régime.
Le regroupement correct devrait être empirique — clusteriser sur `cost_r`
observé — mais cela demande… des données. Circularité mineure, assumée : on
part de la classification évidente, on la révise quand `mean_cost_r` par
symbole devient lisible.

### 7.6 Les données déjà accumulées sont ambiguës

Avec les ruptures n°2 (sur-comptage) et n°3 (coûts absents) encore en place,
tout trade journalisé avant leur correction a un `n_pillars` possiblement faux
et un `pnl_r` optimiste. À date, cela concerne **un seul ordre** (heartbeat :
`envoyes: 1`), donc l'enjeu est nul. Il le devient dans deux semaines.
**Corriger avant d'accumuler** ; et marquer les lignes antérieures avec
`exact_cost=False`, ce qui les exclut automatiquement des promotions par C3.

### 7.7 Ce que ce document ne tranche pas

- **Faut-il continuer à trader en EXPLORE sur le compte réel ?** V12 tourne en
  argent réel depuis le 04/08/2026 sur un capital de 20 €. V14 n'y touche pas
  et ce document ne parle que de V14. Mais la logique développée ici — « la
  strate quorum-2 ne prouve rien » — s'applique mot pour mot à V12, et mérite
  d'être portée à l'attention de Florent.
- **Le quorum 3 est-il le bon seuil PROD ?** Rien ne dit que 3/4 est meilleur
  que 4/4. L'échelon 1 (PROD fantôme) peut trivialement journaliser les deux et
  laisser les données répondre — mais chaque strate supplémentaire est une
  cellule de plus à remplir, donc du délai en plus. À arbitrer.
- **Le seuil de rétrogradation (30 trades, espérance ≤ 0)** est posé sans
  dérivation. À n=30, il se déclenchera parfois à tort. C'est acceptable :
  rétrograder à tort coûte un aller-retour d'échelon, pas de l'argent.
