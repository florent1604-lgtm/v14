# Amendement de l'architecture du banc A/B — v2

Amende `docs/ARCHITECTURE_BANC_AB_20260825.md` (commit `a0bd282`, 434 lignes).
Statut : **soumis aux deux ACCEPT**. Aucune ligne de code, aucun moteur, aucun
rejeu, aucun seuil, aucun ordre, aucun redémarrage.

Sources de l'amendement :

| Source | Offset / commit | Verdict |
|---|---|---|
| Codex, 5 réserves | hub 679 | AMEND avant les deux ACCEPT |
| Claude, sémantique P1a | hub 690-692, commit `75914a9` | **ACCEPT** |
| Hermès, puissance | hub 693 + addendum 694 | **NO-GO pour conclure**, AMEND scientifique |
| Prime, mesures | hub 685-688 | embargo falsifié, B sous-déterminé |

Corpus : `results/p1a/cohorte_373.json`, SHA-256
`10d6277664775ea8599b9304b8e7e86055b35f9a659fce572dfc2f4b22e2e908`, borné à
`97465104:closed`, époque `051f50ad`, commit `c3cda39`.

---

## 0. Le verdict qui gouverne tout le reste

**Le banc ne pourra rien conclure sur ces données, et il doit être construit
pour le dire lui-même.**

Hermès l'établit et je l'accepte sans réserve : la MDE à 80 % vaut 0,487 R alors
que l'effet observé vaut 0,330 R ; le bootstrap deux voies symbole×jour rend
zéro plausible, IC95 [−0,6433 ; +0,0410] ; après BH 5 % sur 7 hypothèses le
contraste piliers donne q = 0,0978 et n'est pas retenu ; la fenêtre 70/30 ne
contient que 6 observations 4p. Il faudrait ~125 observations 4p et ~814 au
total pour l'effet observé, ~338 et ~2 209 pour 0,20 R.

Conséquence sur la nature du livrable. Le banc n'est **pas** un instrument de
décision. C'est (a) une spécification préenregistrée, (b) une porte qui refuse
de conclure, (c) un générateur d'hypothèses pour une fenêtre future intacte.
Toute sortie qui ressemblerait à une recommandation est un défaut, pas un
résultat.

**Précision que j'ajoute à l'audit d'Hermès — l'unité de bloc.** Hermès bloque
sur 12 jours. Ce sont les jours de *clôture*. Or la dépendance de choc commun
s'exerce à la **décision**, pas à la sortie. Les 373 se placent sur **10 jours
de décision distincts**, et la strate 4p sur **9 seulement** (12/08 : 2, 13 : 9,
14 : 9, 17 : 11, 18 : 7, 19 : 9, 20 : 4, 21 : 1, 24 : 5). Un LODO sur 9 blocs
effectifs dont deux portent 1 et 2 observations est plus fragile encore que ce
que publie l'audit. **L'unité de bloc préenregistrée est le jour de décision**,
et le décompte de blocs effectifs par cellule doit être publié.

---

## 1. Variante B — RETIRÉE telle que spécifiée, remplacée par B(r)

La variante B disait « 4p ramené au poids 3p ». Elle n'est pas reconstructible.

Table figée de `titanium.confiance.evaluer` (`total=4`, `quorum=2`,
`MAX_RISK_PCT=2.0`), par conviction :

| support_pillars | contexte | n | conv. 0,0 | 0,5 | 1,0 |
|---|---|---|---|---|---|
| 2 | 3p | 316 | 0,500 | 0,500 | 0,625 |
| 3 | 4p | 57 | 0,844 | 1,125 | 1,406 |
| 4 | 5p | 0 | 1,313 | 1,750 | 1,750 |

Le ratio de poids 4p/3p vaut 2,25× à conviction neutre mais parcourt
**1,35× à 2,81×** sur l'enveloppe. **`conviction` n'est pas un champ de la
cohorte scellée** : l'entrée qui fixe le poids n'a pas été journalisée. Le clamp
est de surcroît asymétrique — le plancher écrase la moitié basse à 2 piliers, le
plafond la moitié haute à 4 piliers — donc `conviction = 0,5` n'est pas un choix
neutre : il biaise les deux strates en sens opposés.

**Décision.** B devient **B(r)**, famille de sensibilité à un paramètre, où
`r ∈ [1,35 ; 2,81]` est le ratio de poids 4p/3p supposé, et où la contribution
de chaque trade 4p est mise à l'échelle de `1/r`. Règles :

1. Aucun point-estimate B unique n'est publié. La sortie est une **bande** sur
   l'enveloppe complète, plus des scénarios **nommés** (`r = 2,25` s'appelle
   « conviction neutre supposée », jamais « le » résultat).
2. Le statut `NOT_IDENTIFIABLE` est porté dans la sortie machine dès qu'une
   revendication monétaire est demandée. Le seuil de 125 observations 4p
   d'Hermès concerne **uniquement** la confirmation de l'association de qualité
   en R ; il ne rend pas le sizing reconstructible. Plus d'observations ne
   résoudront jamais une entrée non journalisée.
3. B(r) est un **proxy de repondération sans réallocation**. Ni reproduction du
   live, ni conséquence portefeuille : `POIDS_CONVICTION`, le portefeuille, le
   lot minimum et les arrondis restent absents, et le rejeu calcule à
   `quantity = 1`.
4. **Dette de journalisation ouverte** — et c'est la vraie leçon : enregistrer
   `conviction` et le `pct` de risque effectif à la décision. Sans cela, aucun
   banc de sizing ne sera identifiable, jamais. Tâche distincte, hors de ce lot,
   et qui touche l'écriture d'un journal — donc soumise à ton arbitrage.

## 2. Embargo — RETIRÉ, remplacé par une purge par chevauchement exact

Mon embargo de 50 h reposait sur « 200 barres M15 ». Les deux prémisses sont
fausses. La cohorte n'est pas M15 (M15 292, H1 74, H4 7) ; la durée calendaire
`placed_at`→`closed_at` culmine à 61,99 h en M15 (**248 barres**), 65,24 h en H1
(65 barres), 59,14 h en H4 ; et **13 trades sur 373, soit 3,5 %, dépassent
50 h**. Le facteur qui fait exploser l'embargo est le temps **calendaire** sur
M15 — week-ends et gaps — pas le pas de temps. Hermès corrige d'ailleurs sa
propre formulation en 694 : le défaut n'est pas H1, c'est l'embargo en barres.

**Décision.** Un embargo scalaire est abandonné. À chaque frontière de fold, on
applique une **purge par intervalle exact** : tout trade d'apprentissage dont
l'intervalle `[decision_at, exit_at]` chevauche la fenêtre de validation est
retiré.

- `exit_at = ts_exit` (vérifié identique à `closed_at` sur les 373).
- `decision_at = min(ts_open, placed_at)`, plancher conservateur ramené à
  l'ouverture de la barre du TF. Justification : `placed_at − ts_open` est
  positif sur toute la cohorte, de 3 ms à 601 s ; `placed_at` est donc un
  horodatage de journalisation postérieur à la décision et ne peut pas servir de
  borne gauche.
- Le maximum observé (65,24 h) est une **borne inférieure** : la cohorte ne
  couvre que 12,9 jours et est censurée à droite au cutoff. Aucun plafond ne
  peut donc être calibré sur l'observé. Si un plafond reste nécessaire, il vient
  d'une **règle de durée maximale préenregistrée**, avec exclusions et
  décomptes publiés.
- Test obligatoire : un fold construit sur un TF mixte M15/H1/H4 dont un trade
  chevauche la frontière doit voir ce trade **exclu**, et le décompte d'exclus
  publié.

## 3. Résolution des cinq réserves de Codex (offset 679)

**R1 — contamination OOS.** Accepté sans réserve. **B(r), C et D sont suffixées
`EXPLORATOIRE`** sur l'époque `051f50ad`, dans le nom de la variante et dans la
sortie machine. Le segment de vérification a déjà été lu pour l'axe piliers
(4p +0,100/8672, 3p +0,088/72250 publiés avant préenregistrement) et pour l'axe
classe d'actif (suspension FX décidée le 24/08 sur ce segment). Aucune
revendication hors échantillon n'est possible sur ce corpus. Seule une fenêtre
future préenregistrée peut confirmer.

**R2 — baseline figée.** Accepté. `confiance.evaluer` n'est **pas** appelée au
runtime du banc. La table du §1 est figée dans `config/`, accompagnée de
`source_commit`, du `code_sha256` de `titanium/confiance.py` et des paramètres
(`total=4`, `quorum=2`, `MAX_RISK_PCT=2.0`). Un test compare la table figée à la
fonction vivante et **échoue** si elles divergent : une édition future de
`confiance.py` casse le banc au lieu de le déplacer silencieusement. La
variante A est nommée **« proxy de sizing partiel »**, jamais « reproduction du
live ». Le poids 5p est **défini explicitement** (1,750 à conviction neutre,
effectif 0 sur cette cohorte) plutôt que ramené implicitement au minimum.

**R3 — embargo.** Accepté et durci : voir §2. La réserve était juste dans sa
direction et fausse dans son mécanisme ; la correction est mesurée.

**R4 — identité et domaine.** Accepté. Clé appariée composite
`(corpus_epoch, symbol, position_ticket)`, unicité **assertée** et non supposée.
Une variante-filtre est une **spécification déclarative** : champs ex ante
allowlistés, opérateurs allowlistés, aucun `eval`, aucun callable, aucune
lambda. Sont **refusées** toute option de réallocation, d'exposition, de créneau
et de plafond. Le zéro d'une décision filtrée mesure une **abstention sans
réallocation** — le capital libéré ne finance rien — et jamais un déploiement
live.

**R5 — séparer sélection et score.** Accepté. `net_r`, `gross_r`, `pnl_r`,
`mfe_r`, `mae_r` et tout champ d'issue sont **interdits** dans la construction
et la sélection d'une variante, et autorisés **uniquement** dans le calcul du
résultat **après gel du masque**. Le masque est gelé, sérialisé et haché avant
la première lecture d'une issue. **Test de non-interférence obligatoire** :
permuter ou remplacer les issues sans toucher aux champs ex ante ne doit
**jamais** modifier le masque ; le test échoue si le hash du masque bouge.

## 4. Résolution des amendements d'Hermès (693 / 694)

| # | Exigence | Résolution |
|---|---|---|
| 1 | B/C/D `EXPLORATOIRE` | §3 R1 — accepté |
| 2 | ne jamais mélanger cohorte 373 et rejeu long | Accepté. Deux magasins séparés, aucun agrégat commun. La cohorte **génère** l'hypothèse ; le rejeu long n'estime qu'une **stabilité historique exploratoire**. Un test interdit toute jointure entre les deux. |
| 3 | `symbole×mois` invalide sur 373 | Accepté. Un seul mois (2026-08) ; 5 folds donneraient ≈11 4p par fold. Sur la cohorte : **jour de décision**, LOSO, LODO uniquement. `symbole×mois` reste recevable sur le rejeu multiannuel. |
| 4 | embargo par durée réelle | §2 — remplacé par la purge par chevauchement exact, plus strict que demandé |
| 5 | gate machine `NOT_POWERED` | §5 — accepté |
| 6 | hypothèse primaire unique + BH 5 % | §6 — accepté |
| 7 | mapping 4p→support 3, 5p explicite | §3 R2 et §7 — accepté |
| 694-1 | B non identifié → bande ou `NOT_IDENTIFIABLE` | §1 — accepté, B retirée et remplacée |
| 694-2 | purge par chevauchement exact | §2 — accepté |

## 5. Porte machine `NOT_POWERED`

Avant toute mesure, et **avant** toute lecture d'issue, le banc publie par
cellule : effectif, effectif par strate, nombre de **blocs de décision
effectifs**, nombre de symboles, empan calendaire.

Minima préenregistrés pour qu'une cellule soit **conclusive** : ≥ 125
observations 4p, ≥ 814 observations totales, ≥ 20 jours de décision
indépendants, ≥ 30 symboles.

Une cellule sous l'un de ces minima rend l'état **`NOT_POWERED`**. Cet état
n'est ni un zéro, ni une absence d'effet, ni un « pas de différence
significative » : c'est un **refus de mesurer**. Sur la cohorte actuelle
(57 / 373 / 9 blocs 4p / 62 symboles) **toutes les cellules de l'axe piliers
sortent `NOT_POWERED`**, et c'est le comportement attendu du banc au premier
jour.

## 6. Plan d'inférence préenregistré

- **Hypothèse primaire unique** : B(r), l'association de qualité 4p vs 3p en R.
- **Secondaires** : classe d'actif, TF, sens, spread — sous **BH 5 %**, avec la
  famille d'hypothèses **déclarée avant** la mesure et son cardinal publié.
- Incertitude par **bootstrap deux voies symbole × jour de décision**, qui est
  le seul schéma respectant les deux dépendances. Les IC iid et une-voie peuvent
  être publiés comme diagnostics, jamais comme preuve.
- **Aucune décision métier** ne peut découler de ce banc sur ce corpus.

## 7. Intégration de l'audit de Claude (ACCEPT, commit `75914a9`)

Vocabulaire imposé au banc, sans exception : `N p = support_pillars + 1`, car
`trend_sr` est obligatoire et compte. **3p ↔ `support_pillars = 2`, 316
trades ; 4p ↔ `support_pillars = 3`, 57 trades ; 5p ↔ 4, effectif 0.** Aucun
« 2p vs 3p » ne doit apparaître. Le nom de contexte fait foi, c'est lui qui est
journalisé.

Sont également repris : `191` = clôtures perdantes = 187 `init` perdants + 4
`breakeven` perdants ; `195` = `exit_reason=init` = 187 perdants + **8
gagnants**. `exit_reason=init` décrit un **état de gestion**, pas une issue —
le banc ne doit jamais traiter `init` comme synonyme de perte.

La jointure `placed`→`closed` est **obligatoire et non facultative** :
`spread_r`, `regime` et `planned_price` ne sont portés que par `placed`. La
cohorte scellée les portant déjà, le banc lit la cohorte et ne refait aucune
jointure.

**Je relaie la réserve de forme de Claude à Codex** : ajouter une ligne dans
`semantics` du manifeste disant que `is_negative` tient lieu de `censored` — les
deux ensembles de 191 sont strictement identiques, mais un consommateur venant
d'`excursions.ndjson` cherchera `censored` et ne le trouvera pas. Non bloquant.

## 8. Ce que ce document ne fait pas

Je ne calcule **aucun** résultat de B(r), C ou D ici. Le masque n'est pas gelé,
la spécification n'est pas préenregistrée, et lire une issue maintenant
détruirait la seule garantie que ce banc peut encore offrir. Les chiffres
présents dans ce document sont exclusivement des quantités de **conception** —
effectifs, durées, poids, blocs — jamais des issues.

## 9. Séquence

1. Deux ACCEPT sur cet amendement. ← **porte actuelle**
2. Spécification préenregistrée : masque, familles d'hypothèses, minima, gel et
   hachage.
3. Tests d'abord — non-interférence, purge par chevauchement, table figée vs
   `confiance.evaluer`, unicité de la clé, refus des options de réallocation.
4. Implémentation.
5. Mesure exploratoire, dont l'issue attendue au premier jour est
   `NOT_POWERED` sur l'axe primaire.

Aucune étape ne touche `FICHIERS_MOTEUR`, le trading, un seuil, un ordre, une
promotion ou un redémarrage.
