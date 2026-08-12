# Piliers G4/G5 — cause racine et correctif mesuré

**Mission Prime `ba74e58a`** · Claude · 12/08/2026
Compte DEMO 10055401 · aucun ordre, aucun redémarrage, aucun seuil ni quorum touché.

---

## Résumé

| Question posée | Réponse |
|---|---|
| `ict_structure.py` / `ict_market_profiles.py` alimentent-ils G4/G5 ? | **Non.** 1 595 lignes importées uniquement par leur propre test. Zéro import en production. |
| D'où viennent les 1,6–1,7 pt de « gain » attribués à l'enrichissement ICT ? | **Du marché, pas du code.** La chaîne G4/G5 est inchangée depuis le commit initial. |
| G4 à 7,5 % : détecteur cassé ? | **Non.** 96 % des échecs sont « prix hors golden zone » — la condition même du pilier. |
| G5 à 6,7 % : détecteur cassé ? | **Alimentation manquante.** 80 % des échecs viennent d'un moteur muet. Corrigé. |
| Effet du correctif | **G5 11,9 % → 23,7 %**, quorum **19,3 % → 24,4 %**, A/B contrôlé sur 135 setups. |

---

## 1. Cause racine : les modules ICT sont du code mort

```
$ grep -rn "ict_structure\|ict_market_profiles" --include=*.py .
tests/test_ict_enrichment.py:14  ...
tests/test_ict_enrichment.py:21  ...
        (30 occurrences, TOUTES dans ce seul fichier de test)
```

Aucun import dynamique non plus : le seul `__import__` du dépôt est dans
`tools/dashboard.py` et ne vise pas ces modules.

Et la chaîne qui calcule réellement G4/G5 n'a pas été touchée par `5c5884e` :

```
$ git show --stat 5c5884e -- titanium/features/builder.py       → 0 ligne
$ git show --stat 5c5884e -- titanium/gates/confluence_gate.py  → 0 ligne
$ git show --stat 5c5884e -- titanium/features/smc.py           → 0 ligne
$ git show --stat 5c5884e -- titanium/features/candlesticks.py  → 0 ligne

$ git log --oneline -1 -- titanium/features/builder.py
3b53839 V14 initial
```

**Conclusion.** Le code qui produit G4 et G5 est identique avant et après
`5c5884e`. Un écart de 1,6 pt entre deux mesures d'un code identique mesure la
variation du marché entre les deux relevés, rien d'autre. La comparaison
avant/après du plan Prime ne pouvait donc rien révéler.

---

## 2. Pourquoi G4 échoue — et pourquoi ce n'est pas un défaut

Mesure sur **134 setups directionnels réels**, catalogue complet, marché ouvert
(`tools/mesure_piliers_ict.py`) :

```
prix hors golden zone       119 / 124   96.0 %
fib contre le setup           4 / 124    3.2 %
fib indisponible              1 / 124    0.8 %
```

G4 exige que le prix soit **simultanément** sur un niveau S/R en H4 (d'où vient
le sens du setup) **et** dans la zone 0,618–0,79 d'un repli en M15. Ces deux
conditions sont calculées sur des unités de temps différentes et sont donc
quasi indépendantes. Un taux de 7,5 % est le produit de deux événements rares,
pas la signature d'un détecteur en panne.

**Rien à corriger sur G4.** Le desserrer reviendrait à supprimer la golden
zone, c'est-à-dire le pilier lui-même. Voir la section 5 pour l'option
mesurée et rejetée.

---

## 3. Pourquoi G5 échouait — et ce qui manquait vraiment

```
motif en attente de confirmation   64 / 125   51.2 %
aucun motif détecté                36 / 125   28.8 %
motif contre le setup              25 / 125   20.0 %
```

**80 % des échecs viennent d'un moteur muet**, pas d'un désaccord. L'automate
de confirmation de `candlesticks.py` a été vérifié ligne à ligne : il est
correct — un motif formé en N−1 devient actionnable si la barre N le confirme.
Il n'y a pas de blocage structurel.

Le vrai manque est ailleurs. `candlesticks.py` reconnaît des **formes**
(englobante, marteau, harami, trois soldats…). Il ne regarde jamais
l'**amplitude**. Or en ICT, la bougie de confirmation la plus forte n'a pas de
forme particulière : c'est le *displacement* — 2 ATR ou plus en 1 à 3 bougies,
corps pleins. C'est la signature d'un passage institutionnel, et le pilier
« bougie de confirmation » ne la voyait pas.

`ict_structure.detect_displacement` la détecte déjà. Elle n'était branchée
nulle part.

---

## 4. Le correctif

`titanium/features/builder.py` — `_displacement_dir()`, câblé en **secours**
de G5 :

```python
candle_dir = int(cndl.get("direction") or 0)
candle_source = "formes" if candle_dir else ""
if candle_dir == 0:                       # le moteur de FORMES est muet
    candle_dir, candle_force = _displacement_dir(ltf)
    if candle_dir:
        candle_source = "displacement"
```

**Secours et non remplacement.** Sur les 18 setups où le displacement était
aligné avec le sens, **8 avaient un moteur de formes disant le contraire**.
Écraser un avis existant serait un passage en force ; on ne comble que le
silence. Cette règle est verrouillée par
`test_moteur_qui_parle_garde_la_main`.

Constantes exposées, toutes réglables sans toucher au corps du code :

| constante | valeur | rôle |
|---|---|---|
| `DISPLACEMENT_FALLBACK` | `True` | mettre à `False` restaure exactement l'ancien comportement |
| `DISPLACEMENT_MIN_ATR` | `2.0` | seuil ICT canonique ; à 1 ATR une bougie ordinaire suffirait |
| `DISPLACEMENT_LOOKBACK` | `20` | fenêtre de recherche, en barres |
| `DISPLACEMENT_FRAICHEUR` | `4` | un displacement vieux de 15 barres ne confirme plus rien |

`BUILDER_VERSION` passe de `1.0.0` à `1.1.0` — la trace de chaque décision
porte la version, donc les journaux d'avant et d'après restent distinguables.

La trace gagne `candle_source` (`"formes"` / `"displacement"` / `""`). Sans ce
champ on ne pourrait pas, plus tard, comparer l'espérance des deux sources sur
les trades journalisés. C'est la mesure qui dira si ce secours mérite de
rester.

---

## 5. Ce que j'ai mesuré puis **rejeté**

Les quatre signaux ICT candidats, taux de passage brut sur les 134 setups :

| signal | taux | verdict |
|---|---|---|
| `breaker block` | **62,7 %** | rejeté — G4 passerait de 7,5 % à 64,9 % |
| `discount/premium` | **55,2 %** | rejeté — découpe le range en deux, pile ou face par construction |
| `BOS structurel` | **51,5 %** | rejeté — pile ou face |
| `displacement` | **14,2 %** | **retenu** — le seul qui discrimine |

Un signal qui passe une fois sur deux n'est pas un filtre de confluence, c'est
un tampon. Câbler les trois premiers ferait passer le quorum de 21,6 % à
79,9 % — le gate deviendrait décoratif. C'est exactement l'erreur E1/E2 du
registre `docs/LECONS.md`, en plus gros.

Le cas du `breaker block` mérite une note technique : `detect_breaker_blocks`
fabrique une zone pour presque **chaque bougie** dont le corps est dépassé plus
tard, puis renvoie `breakers[-10:]`. Dix zones réparties sur la plage récente
couvrent l'essentiel de celle-ci ; « le prix est près d'un breaker » est donc
presque toujours vrai. Ce n'est pas le concept ICT, qui exige une cassure de
**structure** (un OB qui tenait un swing, puis violé). Le détecteur serait à
réécrire avant d'être utilisable — je ne l'ai pas fait, ce n'est pas le
périmètre de cette mission.

### Erreur commise et corrigée en cours de mesure

La première passe donnait `breaker` à **68,7 %** avec une tolérance « au
contact » de 0,2 % du prix. Cette tolérance vaut **6 ATR sur EURUSD** et 0,3
ATR sur BTCUSD — elle ne veut rien dire d'un actif à l'autre. Reprise en
multiples d'ATR (`TOL_CONTACT_ATR = 0.25`), le chiffre tombe à 57,5 %. Le
verdict ne change pas, mais le premier nombre était un artefact de mon propre
code. Consigné ici parce qu'un chiffre faux qui confirme la conclusion reste
un chiffre faux.

---

## 6. Effet mesuré — A/B contrôlé

Deux passes successives sur données réelles ne se comparent pas : le marché
bouge entre elles, et l'écart mélange l'effet du code et celui du marché (entre
mes deux relevés, G3 a perdu 7 points sans qu'une ligne de code le concerne).

`tools/ab_displacement.py` charge les barres **une seule fois** par actif, puis
appelle `build_feats` deux fois dessus, drapeau éteint puis allumé. Tout écart
est alors imputable au seul code.

```
SETUPS DIRECTIONNELS (identiques dans les deux passes) : 135

                               éteint     allumé    écart
--------------------------------------------------------
G5 candle passé              16  11.9%   32  23.7%      +16
quorum 2 atteint             26  19.3%   33  24.4%       +7

SOURCE DE G5 (passe allumée)
  aucune             69    51.1 %
  formes             38    28.1 %
  displacement       28    20.7 %
```

G5 **double**. Le quorum gagne 5,1 points — 7 setups de plus sur 135 passent la
confluence. Modeste, et c'est voulu : un correctif qui aurait quadruplé le
débit aurait été un desserrage déguisé.

---

## 7. Livrables

| fichier | rôle |
|---|---|
| `titanium/features/builder.py` | le correctif — `_displacement_dir` + câblage secours |
| `tools/live_demo.py` | `_stratification` porte `candle_source` jusqu'au journal |
| `tests/test_displacement_fallback.py` | 15 tests · secours, non-écrasement, fraîcheur, amplitude, drapeau, trace, journal |
| `tools/mesure_piliers_ict.py` | décomposition des échecs G4/G5 + contrefactuel ICT, réexécutable |
| `tools/ab_displacement.py` | A/B contrôlé sur barres identiques |
| `collab/CLAUDE_PILLAR_FIX.md` | ce document |

Suite complète après correctif : **1 599 passés, 2 skips, 0 échec** (207 s).

---

## 8. Ce qui reste ouvert

1. **Le correctif est actif en production depuis 13:40:54** — et ce n'est pas
   moi qui ai redémarré.

   J'avais d'abord écrit l'inverse, en me fiant au PID rapporté par Prime à
   10:29 sans le revérifier. Les faits :

   | | |
   |---|---|
   | PID `live_demo` actuel | **27588** (et non 28088) |
   | démarrage du processus | 12/08/2026 **13:40:54** |
   | `builder.py` modifié | 13:14:27 |
   | `live_demo.py` modifié | 13:22:03 |

   Les deux fichiers précèdent le démarrage : la boucle a importé le
   correctif. Quelqu'un l'a redémarrée entre 13:22 et 13:41 — probablement la
   mission `fd5be523` (instrumentation du chemin d'exécution), qui en a
   besoin. Je maintiens la consigne : je ne redémarre pas.

   **Effet observé en production**, `results/shadow_prod.ndjson`, de part et
   d'autre de 11:40:54 UTC :

   ```
   G5 candle_confirmed nommé comme MANQUANT dans le motif
     avant   9013 / 12865   70.1 %
     après     16 / 48      33.3 %
   ```

   Direction et ordre de grandeur concordants avec l'A/B contrôlé. Deux
   réserves, parce que ce chiffre est facile à sur-lire : l'échantillon après
   bascule ne compte que 48 enregistrements et le marché a bougé en même
   temps ; et `shadow_prod.ndjson` ne journalise **que** les setups à 2
   piliers ou plus (mesuré : 2 piliers 87,5 %, 3 piliers 12,5 %, jamais
   moins). C'est donc un taux **conditionnel** aux candidats déjà retenus,
   utile en avant/après à filtre constant, trompeur lu comme un taux global.
   L'A/B sur barres identiques reste la preuve principale.

   Retour arrière si Prime le décide : `DISPLACEMENT_FALLBACK = False`
   restaure exactement l'ancien comportement, avec un test qui le verrouille.

2. **G4 reste à 7,5–9,6 %** et je ne propose pas d'y toucher. Si Prime veut le
   remonter, la seule voie honnête est de mesurer d'abord l'espérance des
   setups à 2 piliers contre ceux à 3, sur les trades journalisés. Si les
   2-piliers perdent, le quorum devrait **monter**, pas descendre. Aucune
   donnée d'aujourd'hui ne permet de trancher — l'époque courante compte
   1 trade.

3. ~~`candle_source` n'aura de valeur que journalisé.~~ **Vérifié et corrigé.**
   Le champ n'atteignait pas le journal : `_stratification` fige les champs à
   l'ouverture et ne le portait pas. Ajouté, avec deux tests — dont un qui
   vérifie que les enregistrements antérieurs restent lisibles (chaîne vide).
   La comparaison « formes » contre « displacement » sur des **résultats** est
   donc possible dès les prochaines clôtures.

4. **`ict_market_profiles.py` (927 lignes) reste entièrement mort.** Il ne
   contient rien qui alimente un pilier — sessions, kill zones, profils
   d'actifs. Soit on lui trouve un usage nommé, soit on le retire ; le laisser
   en l'état entretient l'illusion qu'une fonctionnalité existe.
