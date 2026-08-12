# Protocole GitNexus commun V14

## But

GitNexus est la carte partagee du code V14 pour Codex, Claude, Hermes et Prime.
Le Hub conserve les decisions et les taches; GitNexus conserve les symboles,
dependances, appels et flux d'execution. Il ne remplace ni Git, ni les tests, ni
la revue humaine.

## Source commune

- Depot: `C:\Users\flore\Desktop\V14`
- Nom GitNexus: `titanium-v14`
- MCP commun en lecture/analyse: `http://127.0.0.1:4750/mcp`
- Synchroniseur/verrou Windows: `tools/gitnexus_team.ps1`
- Etat local non versionne: `.gitnexus/team-sync-state.json`
- Runtime Windows utilisateur: `C:\Users\flore\AppData\Local\Programs\OpenSSL-3.5.7`

OpenSSL 3.5.7 est inscrit dans le `PATH` utilisateur avec `OPENSSL_HOME` et
`OPENSSL_CONF`. La distribution FireDaemon a ete acceptee uniquement apres
verification de la signature Authenticode et du SHA-256 officiel. La copie
portable `.gitnexus/runtime/openssl-3.5.7` reste le secours isole du projet.

Le synchroniseur calcule une empreinte du code suivi **et non commite**. Il ne
se contente donc pas de comparer le commit `HEAD`, defaut qui avait laisse
l'index V12 vieillir silencieusement. Un mutex Windows empeche deux agents de
reconstruire la base LadybugDB simultanement.

## Boucle obligatoire pour chaque mission LLM

1. Depuis la racine V14, avant l'analyse ou l'edition:

   ```powershell
   powershell -ExecutionPolicy Bypass -File tools/gitnexus_team.ps1 sync
   ```

2. Lire le contexte GitNexus, puis utiliser `query`/`context` pour comprendre le
   chemin concerne.
3. Avant de modifier un symbole existant, lancer `impact` en direction upstream.
   Une alerte HIGH ou CRITICAL doit etre annoncee avant l'edition.
4. Apres les changements, lancer `detect_changes`, les tests cibles puis la
   suite proportionnee au risque.
5. Avant le compte rendu ou le passage de relais, relancer `sync`. Publier dans
   le Hub l'empreinte, les fichiers, les tests et le risque residuel.

Une synchronisation dont l'empreinte est inchangee est instantanee et ne
reconstruit pas l'index. Si du code change pendant une reconstruction, le script
effectue une seconde passe de stabilisation.

## Acces par agent

- **Codex**: serveur `gitnexus` dans `.codex/config.toml`.
- **Claude**: serveur `gitnexus` dans `.mcp.json`.
- **Hermes**: connexion MCP `gitnexus` configuree vers le port 4750.
- **Prime**: meme protocole depuis son shell; s'il ne charge pas le MCP, il
  utilise l'action `cli` de `tools/gitnexus_team.ps1`.

Les sessions Codex/Claude deja ouvertes doivent etre relancees une fois pour
charger la nouvelle connexion MCP. Hermes reconnecte son client HTTP. Prime lit
`AGENTS.md` et son skill projet a chaque mission.

## Service commun

```powershell
powershell -ExecutionPolicy Bypass -File tools/gitnexus_team.ps1 serve-start
powershell -ExecutionPolicy Bypass -File tools/gitnexus_team.ps1 status
powershell -ExecutionPolicy Bypass -File tools/gitnexus_team.ps1 serve-stop
```

Le service ecoute uniquement sur `127.0.0.1`. Aucun secret, aucune API LLM et
aucune autorite d'execution trading ne lui sont fournis. Le synchroniseur peut
l'arreter quelques secondes pendant une reconstruction pour eviter un verrou
concurrent LadybugDB, puis le remet dans son etat initial.

## Regles de securite et de qualite

- Ne jamais indexer `.env`, `results/`, les caches, les notifications ou les
  sessions; les exclusions sont dans `.gitnexusignore`.
- Ne jamais prendre l'absence d'un lien dans le graphe comme preuve absolue:
  appels dynamiques, configuration et I/O externes exigent une lecture/tests.
- Pas de `clean --force`, de suppression d'index ou de mise a jour majeure de
  GitNexus sans diagnostic et sauvegarde.
- Si GitNexus signale un FTS incoherent, utiliser l'action `cli analyze
  --repair-fts`, puis relancer `sync`; ne pas boucler sur des reconstructions
  completes avant cette reparation ciblee.
- GitNexus n'accorde aucun droit: PAPER/DEMO only et mur demo/reel inchanges.

## Preuve minimale de fonctionnement

```powershell
powershell -ExecutionPolicy Bypass -File tools/gitnexus_team.ps1 status
powershell -ExecutionPolicy Bypass -File tools/gitnexus_team.ps1 cli context RiskGate --repo titanium-v14
powershell -ExecutionPolicy Bypass -File tools/gitnexus_team.ps1 cli query "trading orchestration execution" --repo titanium-v14
hermes mcp test gitnexus
```

Le statut doit etre synchronise, `context RiskGate` doit trouver
`titanium/risk/riskgate.py`, la requete doit retourner des resultats sans alerte
FTS et Hermes doit annoncer une connexion reussie.
