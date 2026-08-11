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

## Prime Agent (harnais RLM, installe le 09/08/2026)

`PrimeIntellect-ai/prime-agent` v0.7.1 est installe et cable sur la racine V14. C'est un
outil de developpement : il ecrit du code et de la documentation, il n'a aucune autorite
de trading et n'est cable a aucune boucle d'execution.

- Lancer : `PRIME_V14.bat` ou `tools/prime_agent_v14.sh` (jamais `prime-agent` nu : le
  lanceur fixe la racine, la cle Gemini et le python du kernel).
- Reglages projet : `.prime/agent/settings.json`. Skill projet :
  `.prime/agent/skills/v14-boucle-dev/SKILL.md`.
- Installation, bug Windows du kernel et garde-fous : `docs/PRIME_AGENT.md`.
- Prime Agent lit AGENTS.md et CLAUDE.md : les regles ci-dessus s'appliquent a lui.
  PAPER/DEMO only, `.env` inaccessible a l'agent, executeur MT5 desarme. Son acces de
  developpement elargi est defini dans `.prime/agent/APPEND_SYSTEM.md`.
- Mode `--autonomous` : toujours borne par `--autonomous-gate` = suite pytest du projet.
