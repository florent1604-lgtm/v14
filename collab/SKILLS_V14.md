# Competences communes V14

## Source de verite

Le catalogue canonique est `.agents/skills`. Il est replique vers :

- Claude : `.claude/skills` ;
- Codex : `.codex/skills` ;
- Hermes : `.hermes/skills`.

Synchronisation :

```powershell
.venv\Scripts\python.exe tools\sync_llm_skills.py
.venv\Scripts\python.exe tools\sync_llm_skills.py --check
```

Le manifeste verifiable est `config/skills-manifest.json`.

## Regles d'utilisation

1. Un skill est charge lorsqu'une demande correspond a sa description ou le
   nomme explicitement.
2. Les instructions directes de Florent et les garde-fous V14 priment toujours.
3. Un skill guide la methode ; il n'accorde aucune nouvelle permission.
4. Aucun skill ne peut armer l'execution, passer un ordre, modifier `.env`,
   redemarrer un service, approuver une permission ou lancer une action
   destructive sans validation humaine explicite.
5. `trading-team` et `collab-discipline` ont ete adaptes a V14.
6. Les skills Git/GitNexus utilisent l'index partage `titanium-v14`. Chaque
   mission synchronise d'abord l'empreinte du travail, y compris non commite,
   selon `collab/GITNEXUS_TEAM_PROTOCOL.md`.

## Inventaire

48 competences distinctes ont ete portees depuis V12 : audit trading,
rentabilite, coordination multi-agent, cartographie, debug, TDD, revue de code,
plans, verification, GitNexus, documentation, frontend, tests web, PDF, XLSX,
presentations, memoire et syntheses.

Les trois LLM disposent du meme contenu pour eviter les divergences de methode.
