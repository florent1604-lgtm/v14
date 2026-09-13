# Cortex ingénieur V14 — Claude + Codex sur abonnement

**Date :** 13 septembre 2026
**Objet :** rendre le cortex sensiblement plus puissant **sans** facture API, en s'appuyant sur les
deux forfaits mensuels Claude et Codex, avec un contrat de sortie exploitable par le moteur.
**Prérequis :** `DOSSIER_PRODUCTION_V14_20260913.md` §6 (le cortex reste hors du chemin de l'ordre).

---

## 1. Ce qui existe déjà, mesuré

`titanium/hermes_cortex.py` (792 lignes) n'est pas un simple appel HTTP. Il porte déjà :

| Mécanisme | Détail |
|---|---|
| **Trois fournisseurs** | `hermes-cli` (abonnement Claude), `ollama-local`, `deepseek-api` |
| **Disjoncteur** | `_CIRCUIT` avec `retry_at`, `_trip()`, backoff distinct pour quota (`HERMES_QUOTA_BACKOFF_S`) |
| **Refus préalable** | `_refus_prealable()` — un refus 4xx avant génération ne compte **pas** comme une panne |
| **Lotissement** | `_ask_par_lots()` — un gros lot est **retenté plus petit** au lieu d'être abandonné |
| **Erreurs normalisées** | `_safe_cli_error()` — classe 429 / quota / timeout sans stocker de secret |
| **Purge de l'environnement** | `_env_abonnement()` — retire `ANTHROPIC_API_KEY` du sous-processus |

**Le fait le plus important du dépôt sur ce sujet** est documenté dans `_env_abonnement` : la cause
racine mesurée le 08/09. Le CLI, dès qu'il voit `ANTHROPIC_API_KEY`, **abandonne l'abonnement** et
facture la clé API. Preuve enregistrée, même prompt, même binaire :

```
sans ANTHROPIC_API_KEY   rc=0, verdicts JSON, ~10 s
avec ANTHROPIC_API_KEY   HTTP 401: API key is invalid
```

Ce diagnostic a été confirmé le jour où `PRIME_V14.bat` a cessé d'exporter la clé. C'est un résultat
**empirique**, et la documentation officielle en donne la règle générale (§2).

---

## 2. La règle d'or, confirmée par la documentation

> **N'utilisez jamais `--bare` si vous voulez rester sur l'abonnement.**

La documentation Claude Code est explicite
([docs officielles](https://code.claude.com/docs/en/headless)) :

- `claude -p` (« print », non interactif) **utilise le login par abonnement** par défaut ;
- `--bare` **n'utilise pas le login par abonnement** — il exige `ANTHROPIC_API_KEY` et ne lit jamais
  les identifiants OAuth ni le trousseau système.

Conséquence directe pour V14 : le reçu de la campagne « purge de la clé » et la règle actuelle de la
documentation disent **la même chose par deux chemins indépendants**. On ne met donc **pas** `--bare`
dans le cortex, et on garde la purge d'environnement — non par superstition, mais parce que c'est ce
qui décide *qui paie*.

Deuxième conséquence, à assumer : **sans `--bare`, `-p` charge le contexte du dossier** — hooks,
skills, MCP, `CLAUDE.md`. Un `-p` lancé dans le dépôt V14 chargera donc `.claude/` **et** `.agents/`,
`.codex/`, `.hermes/`. C'est du contexte payé en tokens à chaque appel. Deux parades : lancer les
workers depuis un **dossier de travail dédié** (pas la racine du dépôt), et passer
`--append-system-prompt-file` avec le seul contrat du rôle au lieu de compter sur la découverte.

Pour Codex ([docs officielles](https://learn.chatgpt.com/docs/non-interactive-mode)) : `codex exec`
réutilise **l'authentification CLI enregistrée** par défaut — donc l'abonnement — et expose
`--ephemeral` (aucun fichier de session écrit), `--ignore-user-config` et `--ignore-rules` pour un
environnement d'automatisation propre.

**Honnêteté sur les conditions d'utilisation :** on reste strictement sur les **CLI officiels des
éditeurs**, utilisés par le titulaire du forfait, en non-interactif — usage explicitement documenté
pour les scripts et la CI. On n'écrit **aucun** client qui imite l'application officielle : c'est
exactement ce que les conditions interdisent, et ce qui fait bannir des comptes. Deux points à
accepter : le quota est **partagé** avec votre usage interactif, et un cortex qui interroge en boucle
consommera le forfait avant la fin du mois.

---

## 3. Architecture cible : deux bassins, un routeur, un contrat

```
        demande (rôle, symbole, faits bornés)
                    │
        ┌───────────▼───────────┐
        │  routeur cortex.py    │  cache TTL + disjoncteur PAR fournisseur
        └───┬───────────────┬───┘
            │               │
   ┌────────▼──────┐  ┌─────▼──────────┐  ┌───────────────┐
   │ Claude  -p    │  │ Codex exec     │  │ Ollama local  │
   │ raisonnement  │  │ JSON Schema    │  │ secours hors   │
   │ long, jugement│  │ verdicts bornés│  │ ligne          │
   └───────────────┘  └────────────────┘  └───────────────┘
                    │
        verdict JSON validé, journalisé, publié scellé
        (ALLOW / WAIT / BLOCK)  — JAMAIS sur le chemin de l'ordre
```

### 3.1 Pourquoi deux bassins et pas un

Parce que les deux outils ne sont pas bons au même endroit :

- **Codex** est le meilleur producteur de **sortie structurée** : `--output-schema ./schema.json`
  contraint la réponse finale à un **JSON Schema**, et `--json` émet un flux JSONL
  (`thread.started`, `turn.started`, `item.completed`, `turn.completed` avec
  `usage.input_tokens` / `output_tokens`). Pour un cortex qui doit rendre un verdict **borné** —
  sévérité, confiance, refus — c'est exactement l'outil. Plus besoin de parser du texte libre.
- **Claude** est le meilleur pour le **jugement long** : analyser un rapport de session, écrire un
  audit, challenger une hypothèse. C'est ce qu'il fait déjà, via Hermes.

**Conséquence de conception : le verdict structuré passe par Codex, l'analyse passe par Claude.**
Cela divise par deux le besoin de « faire parler » un modèle en JSON — et donc les échecs de parsing,
qui sont la première cause de `WAIT` involontaire.

### 3.2 Le contrat de sortie (le point qui compte)

Un schéma unique, versionné dans le dépôt, imposé à Codex et demandé à Claude :

```json
{
  "type": "object",
  "required": ["verdict", "severite", "confiance", "motifs", "refus"],
  "additionalProperties": false,
  "properties": {
    "verdict":  { "enum": ["ALLOW", "WAIT", "BLOCK"] },
    "severite": { "enum": ["calme", "eleve", "blackout"] },
    "confiance":{ "type": "number", "minimum": 0, "maximum": 1 },
    "motifs":   { "type": "array", "items": { "type": "string" }, "maxItems": 5 },
    "refus":    { "type": "array", "items": { "type": "string" } }
  }
}
```

Trois règles qui découlent du reste du système :

1. **`additionalProperties: false`** — un champ inventé est un refus, pas une tolérance.
2. **Tout verdict illisible vaut `WAIT`**, jamais `ALLOW`. C'est la règle fail-closed déjà en place
   pour la macro ; elle doit être la même ici.
3. **Le cortex publie, il ne décide pas.** La boucle appelle déjà `run_once` avec
   `deliberate=False, execute=False` : la propriété existe, ce document ne fait que l'exiger pour la
   suite.

### 3.3 Ce qui manque aujourd'hui (mesuré)

| Manque | Effet |
|---|---|
| Aucun second bassin | Claude indisponible → disjoncteur partagé → **aucune** analyse |
| Disjoncteur **global**, pas par fournisseur | un refus DeepSeek coupe aussi la voie abonnement |
| Sortie texte à parser (`_json_object`) | un JSON mal formé = `WAIT` gratuit |
| Pas de cache **de verdict** | Nuance vérifiée : `tools/analystes.py:263` met le *délibérateur* en cache et les politiques portent un TTL (`policy_ttl_s`, borne par la date de trade, l. 232). Mais rien ne déduplique **au niveau du verdict** : la même question `(rôle, symbole, régime, barre)` peut être repayée avant que la politique n'expire |
| Pas de mesure de quota | impossible de savoir *avant* d'être à sec |

---

## 4. Les quatre agents à créer

Chacun avec un rôle, une entrée bornée, une sortie **conforme au schéma**, et une cadence.

| Agent | Question | Entrée | Cadence | Sortie |
|---|---|---|---|---|
| **Analyste d'exécution** | « Ce setup vaut-il son coût, compte tenu du régime ? » | tunnel du tour, coût par décile, régime | par lot, sur `ENTER` uniquement | `ALLOW/WAIT/BLOCK` |
| **Auditeur de risque** | « Une règle a-t-elle dérivé ? » | diffs, invariants, tests | à chaque commit | liste de motifs, `BLOCK` si rupture |
| **Calibrateur** | « Le plafond est-il bien placé ? » | espérance réelle **par décile de coût** | hebdomadaire | recommandation chiffrée, **jamais appliquée** |
| **Veille** | « Qu'est-ce qui a changé côté courtier / conditions ? » | calendrier, specs, spreads | quotidien | note + alertes |

Le **calibrateur** est le seul dont la sortie *propose* un seuil. Il ne l'écrit pas. La promotion de
seuil reste une **décision humaine explicite** — c'est déjà la règle du projet.

---

## 5. Protections de quota — le point qui décidera du succès

Un cortex qui interroge un LLM à chaque tour **épuisera un forfait Pro en quelques jours**. Dans
l'ordre d'efficacité :

1. **N'appeler que sur `ENTER`.** Aujourd'hui 2 662 `ENTER` par campagne contre 8 976 évaluations :
   appeler partout triplerait la facture pour rien.
2. **Cache de verdict avec TTL**, clé = `(rôle, symbole, régime, id de barre)` — **en plus** du cache
   de délibérateur et des TTL de politique qui existent déjà (`tools/analystes.py:263`), car ceux-ci
   ne dédupliquent pas deux questions différentes dans la même barre. La macro utilise un TTL de 5 s ;
   le même motif s'applique, avec un TTL par rôle (barre pour l'exécution, jour pour la veille).
3. **Disjoncteur par fournisseur**, avec `retry_at` distinct : un 402 DeepSeek ne doit pas couper la
   voie abonnement. Aujourd'hui `_CIRCUIT` est unique — c'est le défaut le plus coûteux du module.
4. **Budget de tokens par tour**, lu dans `turn.completed.usage` de Codex et journalisé. Un rôle qui
   dépasse son budget est mis en pause automatiquement, avec le motif écrit dans le tunnel.
5. **Un seul appel par rôle et par barre.** Au-delà, l'appel est refusé par le cache, pas par la
   chance.

---

## 6. Plan d'implémentation

| Étape | Contenu | Fichier cible | Test |
|---|---|---|---|
| **C1** | Disjoncteur **par fournisseur** (dictionnaire clé→état au lieu d'un `_CIRCUIT` unique) | `titanium/hermes_cortex.py` | un 402 sur un fournisseur laisse l'autre disponible |
| **C2** | Champ `provider` sur chaque appel + routage explicite par schéma | idem | un refus structuré tombe sur le fournisseur suivant |
| **C3** | Bassin Codex : `codex exec --json --output-schema … -o …`, `--ephemeral`, `--ignore-user-config` | nouveau `titanium/cortex_codex.py` | charge conforme validée ; charge hors schéma → `WAIT` |
| **C4** | Schéma JSON versionné + validateur | `config/schema_verdict_cortex.json` + validateur | champ inventé rejeté |
| **C5** | Cache TTL par rôle | `titanium/organism/memory.py` | deux appels dans la même barre = un seul appel LLM |
| **C6** | Budget de tokens par tour, journalisé dans le tunnel | idem + `loop_heartbeat.json` | dépassement → rôle en pause, motif écrit |
| **C7** | Les quatre agents, en tant que **skills** du catalogue `.agents/skills` | `.agents/skills/<agent>/SKILL.md` | chaque skill rend un verdict conforme |
| **C8** | Dossier de travail dédié pour les `-p` (limiter le contexte chargé) | `hermes/` hors dépôt | mesure du contexte chargé avant/après |

**Ordre imposé : C1 avant tout le reste.** Sans disjoncteur par fournisseur, ajouter un second bassin
n'apporte rien : les deux tombent ensemble.

**Ce qu'il ne faut pas faire :**

- **Ne pas** remplacer le CLI officiel par un client HTTP maison pour « économiser le sous-processus » :
  on perdrait l'authentification d'abonnement et on sortirait des conditions d'utilisation.
- **Ne pas** mettre `--bare` : c'est facturer une clé API dont le solde est vide — le défaut
  exactement diagnostiqué le 08/09.
- **Ne pas** placer un verdict LLM devant l'envoi d'ordre, même « juste pour confirmer ».
- **Ne pas** ajouter un troisième bassin avant d'avoir mesuré le taux de `WAIT` des deux premiers.

---

## 7. Ce qu'il faut mesurer avant et après

| Indicateur | Avant | Cible |
|---|---|---|
| Taux de `WAIT` dû à un JSON illisible | non mesuré | **0** (sortie contrainte par schéma) |
| Refus fournisseur coupant **toutes** les voies | oui (disjoncteur unique) | **non** |
| Appels LLM par campagne | non mesuré | ≤ nombre de `ENTER` distincts par barre |
| Tokens par rôle et par jour | non mesuré | publié dans le tunnel |
| Temps de réponse p95 d'un verdict | ~10 s (mesuré le 08/09 sur le CLI) | inchangé, et **hors** du chemin de l'ordre |

**Critère de sortie du chantier :** un verdict structuré, validé par schéma, publié scellé, obtenu par
**deux voies indépendantes**, avec un quota mesuré et un disjoncteur qui ne les couple pas — et
toujours **zéro** ligne de code où un LLM se trouve devant l'envoi d'un ordre.
