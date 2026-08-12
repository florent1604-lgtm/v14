# Bus commun V12 pour V14

V14 reutilise le plan de collaboration de V12 sans dupliquer son historique.

## Competences communes

Claude, Codex et Hermes utilisent le meme catalogue de 48 skills, documente dans
`collab/SKILLS_V14.md`. Les miroirs sont `.claude/skills`, `.codex/skills` et
`.hermes/skills`. Un skill ne cree aucune autorisation et ne declenche jamais
automatiquement une tache, un service ou une execution.

## Canaux

- **CollabHub commun** : `http://127.0.0.1:8770/mcp`
  - stockage SQLite/WAL de V12, source de verite ;
  - outils autorises : `collab_publish`, `collab_read`, `collab_ack`,
    `collab_presence`, `collab_health` ;
  - aucun ordre de trading, commande systeme, ecriture Git ou approbation.
- **Hermes** : `http://127.0.0.1:8766/mcp`
  - `permissions_respond` reste retire : une permission exige toujours une
    validation humaine explicite.
- **Secours append-only** : `node tools/collab_bus.mjs ...`
  - V14 reutilise `../v12/collab/messages/*.ndjson` si V12 est present ;
  - aucun secret, cle API, mot de passe ou jeton dans les messages.

## Demarrage

Depuis la racine de V14 :

```powershell
powershell -ExecutionPolicy Bypass -File tools/collab_services.ps1 start
powershell -ExecutionPolicy Bypass -File tools/collab_services.ps1 status
```

Le lanceur ne demarre que Hermes et CollabHub. Il ne demarre ni le moteur de
trading V12, ni Titanium MCP, ni la porte d'ecriture GitNexus.

Claude et Codex doivent etre relances dans le dossier V14 apres modification
de `.mcp.json` et `.codex/config.toml` pour recharger les deux connexions.

## Intelligence de code commune GitNexus

Codex, Claude, Hermes et Prime partagent l'index `titanium-v14` via
`http://127.0.0.1:4750/mcp`. Au debut de chaque mission et avant le passage de
relais, l'agent qui dispose du shell execute :

```powershell
powershell -ExecutionPolicy Bypass -File tools/gitnexus_team.ps1 sync
```

Hermes est deja configure comme client MCP `gitnexus`; il consulte le graphe
mais reste C1 sans autorite d'ecriture ou d'execution. Le protocole, le verrou
multi-agent et les preuves attendues sont dans
`collab/GITNEXUS_TEAM_PROTOCOL.md`.

## Format d'un echange utile

Chaque demande contient :

1. l'objectif ;
2. la tache precise ;
3. le livrable attendu ;
4. le critere de fin ;
5. les preuves ou fichiers concernes.

Exemple de secours :

```powershell
node tools/collab_bus.mjs send --from codex --to claude --task V14-AUDIT --content "Objectif: verifier le RiskGate. Tache: relire titanium/riskgate.py. Livrable: anomalies avec preuves. Fin: verdict et tests proposes."
node tools/collab_bus.mjs read --to claude
```

## Regles non negociables

- PAPER ONLY tant que la rentabilite n'est pas demontree hors echantillon.
- Aucun secret dans le bus, les journaux ou les preuves.
- Hermes reste un observateur/conseiller C1 : aucune autorite d'execution.
- Pas de boucle automatique Claude -> Codex -> Claude.
- Un agent accuse reception avec `collab_ack` ou la commande `ack` du secours.
