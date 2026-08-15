# Reprise Prime — 15/08/2026

Note écrite par Claude à la demande de Florent, après une session Prime qui
n'a rien pu faire. Elle sert deux choses : dire **pourquoi** cette session a
échoué, et rendre à Prime le contexte qu'il n'a pas retrouvé.

---

## 1. Ce qui a réellement cassé ce matin

Trois causes distinctes, empilées. Aucune n'est le modèle.

### a) La session a été lancée hors de V14 — cause principale

Session `01a00412`, 15/08 06:18 UTC, première ligne du journal :

```json
{"type":"session","id":"01a00412-…","cwd":"C:\\Users\\flore"}
```

`cwd` vaut le dossier personnel, **pas** `C:\Users\flore\Desktop\V14`. Or c'est
`--cwd` qui décide de tout ce que Prime découvre : `AGENTS.md`, `CLAUDE.md`,
`.prime/agent/skills`, `collab/`. Lancé depuis la racine personnelle, il n'avait
littéralement aucun moyen de savoir que V14 existe.

C'est le piège déjà documenté dans les deux lanceurs — *« le kernel et les outils
héritent du répertoire du DÉMON, pas de celui du client »*. `prime-agent` a
été appelé nu, sans passer par `PRIME_V14.bat`.

### b) Le kernel IPython était mort

Chaque appel d'outil de la session a renvoyé `Kernel has been shut down`, y
compris `1+1`. Prime n'a donc même pas pu explorer le disque pour retrouver V14
tout seul. État vérifié aujourd'hui :

| interpréteur candidat | `import rlm` |
|---|---|
| `~/.prime/agent/kernel-win/Scripts/python.exe` | ✅ OK |
| `~/.prime/agent/kernel-venv/Scripts/python.exe` | ❌ `ModuleNotFoundError` |

Le bon kernel existe et fonctionne. Le lanceur le désigne ; un `prime-agent` nu
non. C'est la même cause que (a).

### c) Le modèle n'était pas Gemini — mais l'affichage pouvait le faire croire

Les six sessions Prime existantes tournent **toutes** sur `claude-opus-5`,
fournisseur `anthropic`. La sonde d'abonnement passe (`prime_auth_probe.mjs`
sort 0, entrée `oauth` présente dans `auth.json`).

En revanche `PRIME_V14.bat` codait `claude-sonnet-5` comme modèle Claude par
défaut — alors que `.prime/agent/settings.json` dit `claude-opus-5`. Le drapeau
explicite passé au binaire gagne sur le fichier : la bannière n'a **jamais** pu
annoncer Opus 5, et affichait `gemini-2.5-flash` dès que la sonde échouait. D'où
l'impression légitime que Prime tournait sur Gemini.

**Corrigé aujourd'hui**, dans `PRIME_V14.bat` et `tools/prime_agent_v14.sh` :

- modèle Claude par défaut → `claude-opus-5`, aligné sur `settings.json` ;
- un repli Gemini n'est plus silencieux : bandeau `ATTENTION` explicite ;
- `node` absent du PATH est distingué de « pas d'abonnement » — les deux
  produisaient le même repli muet ;
- l'attachement à une session existante prévient que le modèle affiché ne
  s'applique qu'à une session **neuve**.

---

## 2. Relancer correctement

Session neuve, Opus 5, racine V14, kernel valide :

```
PRIME_V14.bat --provider anthropic --model claude-opus-5
```

Sans argument, le lanceur se **rattache** à la session vivante de cette racine
(aucun doublon de worker) et hérite du modèle de sa création.

Ne jamais appeler `prime-agent` nu : c'est exactement ce qui a produit la panne
de ce matin.

Après un changement de variable d'environnement, `prime-agent shutdown --force`
avant de relancer — sinon le client se rattache à l'ancien démon.

---

## 3. Où en est V14

| | |
|---|---|
| HEAD | `917661b` — *performance réelle des stratégies d'exécution sur 100 clôtures* |
| arbre | propre, hors métadonnées GitNexus (`AGENTS.md`, `CLAUDE.md`) |
| boucle `live_demo` | **active**, armée, pid 26016 |
| dashboard | actif, pid 15744 |
| analystes | ⚠️ **DOUBLON** — 2 instances (10572, 29632), à trancher par Prime |
| compte | DEMO 10055401, equity **3 668,97 EUR** |
| univers portable | **6** actifs — week-end, seule la crypto cote (149 en semaine) |
| tâches ouvertes | 2, toutes deux `in_progress`, aucune en backlog |

Les deux tâches restantes sont des **critères d'accumulation**, pas du code :
`803b129b` (une semaine de marchés ouverts) et `394a10ce` (20 clôtures par
contexte). Elles ne peuvent pas être fermées à la main, seulement atteintes.

---

## 4. Les stratégies d'exécution — la question posée ce matin

C'est ce que Prime n'a pas pu répondre. Mesuré sur **100 clôtures live**,
compte démo, lecture seule. Rapport complet :
`collab/CLAUDE_PERF_EXECUTION_20260815.md`.

### Ordres limites — la seule brique dont le bénéfice tient

```
189 placés · 65 remplis (34,4 %) · 124 expirés
économie réalisée  +0,0995 R par fill · +5,8 R sur les 59 positions closes
slippage           −0,0016 R, négligeable et favorable
```

Les trades entrés à la limite font **−0,1829 R** contre **−0,2610 R** pour
l'ensemble : attendre un meilleur prix ne sélectionne pas de moins bons setups.
Sans les limites, le cumul serait de l'ordre de −32 R au lieu de −26 R.

### Le quorum de piliers est inversé — le résultat qui compte

```
S=2   64 clôtures · −0,1767 R ± 0,1104 · PF 0,622
S=3   35 clôtures · −0,4419 R ± 0,1353 · PF 0,287
```

Les setups à 3 piliers perdent **deux fois et demie plus** que ceux à 2. Or
`titanium/confiance.py` augmente le risque avec le nombre de piliers :
0,50 % à 2/4, ~1,13 % à 3/4, 1,75 % à 4/4. **On mise deux fois plus gros sur la
strate qui perd le plus.**

`confiance.py` annonçait lui-même *« c'est un pari, pas une optimisation
démontrée, le registre d'edge est vide »*. Il ne l'est plus, et il dit que le
pari est à l'envers.

### Le secours displacement (G5) alimente cette strate

```
displacement S=2   17 · −0,0158 R · PF 0,960   ← meilleure cellule mesurée
displacement S=3   17 · −0,6262 R · PF 0,112   ← la pire
formes       S=3    3 · −0,6414 R              ← même signe, autre source
```

Le displacement n'est pas mauvais : c'est le passage en S=3 qui l'est, et il
frappe les deux sources pareillement. Mais le correctif a **multiplié par six**
le nombre de trades S=3 en fournissant le troisième pilier.

### La matrice d'exécution dry-run ne mesure pas un edge

`results/execution_matrix_full/` classe 15 politiques et met `cancel_replace` en
tête. Son propre encart « Limites de fidélité » précise que les quotes L1, les
profondeurs et les chemins intrabarres sont **synthétiques** : ce classement
mesure un simulateur. Sa recommandation — comparer `market`, `limit_passive` et
`adaptive` sur des quotes broker archivées avant toute promotion — est la bonne
lecture, et elle n'a pas encore été faite.

### Ce qui attend l'arbitrage de Prime

**Geler la modulation du risque par piliers à plat** le temps d'accumuler.
Asymétrie favorable : si l'hypothèse d'origine est vraie, une modulation neutre
ne coûte presque rien ; si elle est fausse, elle arrête l'hémorragie. Aucun écart
rapporté ici n'atteint |z| = 2 — je ne propose **pas** d'inverser la modulation
sur un z de 1,52.

Ce qui a changé de statut n'est pas la certitude, c'est la **cohérence** : le
même signe apparaît dans trois découpages indépendants.

---

## 5. Garde-fous inchangés

PAPER/DEMO uniquement. Aucun ordre réel, aucune modification de `.env`, aucun
armement, aucun redémarrage de service ou de MT5 sans accord explicite de
Florent. Aucun secret sur le bus, dans un journal ou dans une sortie de test.
Aucun changement de seuil ou de quorum sans arbitrage Prime. Florent reste
l'autorité humaine finale.
