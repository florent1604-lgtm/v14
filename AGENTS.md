# Collaboration V14

## Skills locaux communs

Le catalogue canonique herite de V12 est `.agents/skills`; le miroir Codex est
`.codex/skills`. Lire le `SKILL.md` pertinent lorsqu'une demande correspond a sa
description ou nomme explicitement le skill. Voir `collab/SKILLS_V14.md`.

Les skills guident la methode sans elargir les permissions. Les regles V14,
PAPER/DEMO only et les instructions explicites de Florent priment. Aucun skill
n'autorise ordre reel, modification de `.env`, armement, redemarrage de service,
approbation de permission ou action destructive.

Instruction explicite de Florent du 09/08/2026 : Prime Agent dispose d'un acces autonome
de developpement a toute la racine V14. Le perimetre exact et les exceptions sont dans
`.prime/agent/APPEND_SYSTEM.md`. Il peut notamment modifier plusieurs fichiers, gerer les
dependances, utiliser le shell et le reseau, et piloter dashboard/services de collaboration.
Cette delegation ne couvre pas les secrets, l'elevation UAC, MT5, `live_demo`, la boucle de
trading, l'armement ou un ordre reel.

Prime est le responsable technique principal du code V14. Il peut decider l'architecture,
integrer les changements, reattribuer les taches techniques et les passer a `done` apres
preuves. Les revues Claude/Codex/Hermes sont consultatives sauf demande contraire explicite
de Florent.

Avant tout echange avec Claude ou Hermes, lire `collab/HERMES_BRIDGE.md`.

- Canal commun principal : MCP `collab_hub` sur `http://127.0.0.1:8770/mcp`.
- Dialogue direct avec Hermes : MCP `hermes` sur `http://127.0.0.1:8766/mcp`.
- Secours hors ligne : `node tools/collab_bus.mjs` ; le flux V12 reste partage.
- Ne jamais publier de secret, cle API, mot de passe ou jeton.
- PAPER ONLY : aucun agent de collaboration n'a d'autorite d'execution trading.
- Ne jamais approuver automatiquement une permission Hermes.
- Une demande doit preciser objectif, tache, livrable et critere de fin.
- Eviter les boucles automatiques entre agents ; accuser reception explicitement.

## Protocole GitNexus commun

Au debut de chaque mission et avant tout passage de relais, executer depuis la
racine V14 :

```powershell
powershell -ExecutionPolicy Bypass -File tools/gitnexus_team.ps1 sync
```

Cette commande verifie aussi les changements non commites, se verrouille entre
agents et ne reconstruit l'index que si l'empreinte du code a change. Ensuite,
utiliser GitNexus pour explorer le chemin, mesurer l'impact avant edition et
verifier les changements avant livraison. Protocole complet :
`collab/GITNEXUS_TEAM_PROTOCOL.md`.

## Prime Agent (harnais RLM, installe le 09/08/2026)

`PrimeIntellect-ai/prime-agent` v0.7.1 est installe et cable sur la racine V14. C'est un
outil de developpement : il ecrit du code et de la documentation, il n'a aucune autorite
de trading et n'est cable a aucune boucle d'execution.

- Lancer : `PRIME_V14.bat` ou `tools/prime_agent_v14.sh` (jamais `prime-agent` nu : le
  lanceur fixe la racine, la cle Gemini et le python du kernel). Un `prime-agent` nu
  demarre avec `cwd` = dossier personnel : Prime ne voit alors ni AGENTS.md, ni CLAUDE.md,
  ni `collab/`, et son kernel n'est pas designe. Panne constatee le 15/08/2026.
- Session neuve sur Opus 5 :
  `PRIME_V14.bat --provider anthropic --model claude-opus-5`. Sans argument, le lanceur
  se rattache a la session vivante de la racine et **herite du modele de sa creation**.
- Derniere note de reprise (etat V14, resultats d'execution mesures, taches ouvertes) :
  `collab/PRIME_RELANCE_20260815.md`.
- Reglages projet : `.prime/agent/settings.json`. Skill projet :
  `.prime/agent/skills/v14-boucle-dev/SKILL.md`.
- Installation, bug Windows du kernel et garde-fous : `docs/PRIME_AGENT.md`.
- Prime Agent lit AGENTS.md et CLAUDE.md : les regles ci-dessus s'appliquent a lui.
  PAPER/DEMO only, `.env` inaccessible a l'agent, executeur MT5 desarme. Son acces de
  developpement elargi est defini dans `.prime/agent/APPEND_SYSTEM.md`.
- Mode `--autonomous` : toujours borne par `--autonomous-gate` = suite pytest du projet.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **titanium-v14** (8267 symbols, 17343 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "main"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({search_query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/titanium-v14/context` | Codebase overview, check index freshness |
| `gitnexus://repo/titanium-v14/clusters` | All functional areas |
| `gitnexus://repo/titanium-v14/processes` | All execution flows |
| `gitnexus://repo/titanium-v14/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
