---
name: v14-boucle-dev
description: Boucle de développement de Titanium V14 (fork TradingAgents + briques déterministes V12). À charger dès qu'une tâche touche le code de V14 : portes de confluence, RiskGate, features, délibération, vendeur MT5, exécution, orchestrateur, dashboard, tests. Donne l'environnement exact, les commandes de vérification, les garde-fous non négociables et le critère de rentabilité à respecter.
---

# Boucle de développement V14

V14 est un **fork intégral** de `TauricResearch/TradingAgents` v0.3.1 étendu avec les
briques déterministes de V12. La thèse : **les portes ET décident SI on peut entrer,
la délibération LLM décide COMBIEN et COMMENT, le RiskGate a le dernier mot.**

Lire `CLAUDE.md` (racine) avant toute modification structurelle — il est la source de
vérité sur l'architecture. Ce skill décrit la *méthode*, pas l'architecture.

## Les trois règles non négociables

1. **Le LLM n'a jamais l'autorité d'exécution.** Il module la taille et le plan d'entrée
   d'un setup *déjà validé*. Les portes gardent le droit de véto.
2. **Aucun appel LLM dans le chemin critique temps réel.** La délibération sert le swing
   et le dimensionnement, jamais le déclenchement.
3. **Fail-closed conservé.** LLM indisponible, lent ou incohérent ⇒ retour au comportement
   déterministe, jamais un pari. (Le RiskGate de V12 était fail-OPEN ; celui de V14 est
   fail-closed — ne pas régresser.)

## Interdits durs — aucune tâche ne les lève

- Aucun ordre réel. L'exécuteur MT5 (`titanium/execution/mt5_executor.py`) est **désarmé** :
  ne pas l'armer, ne pas contourner le mur démo↔réel.
- Ne pas lire, afficher ou modifier `.env` (clés API). Utiliser uniquement les identifiants
  injectés par le lanceur, sans les publier.
- L'accès de développement élargi défini dans `.prime/agent/APPEND_SYSTEM.md` autorise Prime
  à piloter le dashboard et les services de collaboration. Il n'autorise pas à redémarrer
  MT5, `live_demo` ou une boucle de trading, ni à accepter une élévation UAC.
- Ne pas archiver `results/positions.json` tant que MT5 n'a pas confirmé zéro position V14 :
  c'est un état vivant, pas un journal ; le déplacer orpheline les tickets.

## Environnement

- **Toujours travailler depuis la racine V14** — la résolution des clés utilise
  `find_dotenv(usecwd=True)`.
- Python du projet : `.venv\Scripts\python.exe` (3.12, install éditable). Le kernel IPython
  de Prime Agent est un **venv séparé** (`~/.prime/agent/kernel-venv`) : il n'a *pas*
  `titanium` ni `tradingagents` importables. Pour exécuter du code du projet, passer par un
  sous-shell :

```bash
%%bash
.venv/Scripts/python -m pytest tests/ -q
```

Le kernel sert à lire, transformer, agréger et piloter ; le venv du projet sert à exécuter
le code de V14. Ne pas installer les dépendances du projet dans le kernel.

- Fournisseur LLM par défaut : **Google Gemini Flash** (`GEMINI_API_KEY`). OpenAI et
  Anthropic sont à sec, Gemini Pro est hors free tier.
- Machine **CPU seul** (Ryzen 7 7730U, 15 Go RAM) : aucun LLM local dans le chemin de
  décision, et pas de charge lourde parallèle pendant qu'un balayage MT5 tourne.

## Vérifier — dans cet ordre

| Étape | Commande |
|---|---|
| Suite complète | `.venv/Scripts/python -m pytest tests/ -q` |
| Relevé autoritatif (alimente le dashboard) | `.venv/Scripts/python tools/dashboard.py --tests` |
| Chaîne déterministe bout en bout | `.venv/Scripts/python scan_v14.py` (n'exécute jamais) |
| Analyse multi-agents ponctuelle | `.venv/Scripts/python run_v14.py TICKER DATE` |

Référence actuelle : **1488 passed, 2 skipped** (555 titanium + 933 socle). Une suite qui
descend sous cette barre est une régression, pas un aléa.

Toute nouvelle brique doit être inscrite dans `BRIQUES` (`tools/dashboard.py`) : sinon elle
n'apparaît ni sur la page ni dans le compteur, et ses tests sont oubliés en silence.

## Mode autonome — bornes obligatoires

N'utiliser `--autonomous` qu'avec la suite de tests comme porte de sortie :

```
prime-agent --autonomous \
  --autonomous-gate ".venv/Scripts/python -m pytest tests/ -q" \
  --autonomous-max-turns 12
```

Une porte qui n'exécute pas les tests du projet ne prouve rien. Jamais de porte qui passe
un ordre, appelle MT5 en écriture ou touche `.env`.

## Méthode RLM sur ce dépôt

- Garder le contexte du parent **focalisé** : déléguer les lectures larges (audit d'un
  module, relecture de tests, comparaison V12↔V14) à des enfants `rlm(...)` nommés, puis
  ne remonter que la conclusion.
- Les enfants sont utiles quand le travail est **séparable** (audit portes / audit risque /
  audit données). Ils ne le sont pas pour une modification unique et locale.
- Écrire dans l'état de harnais (`/refine`) ce qui est **durable et vérifié** : une mesure,
  un piège reproductible, une convention. Pas une impression de session.

## Pièges de V12 à ne pas reproduire

1. **Un module = un fichier.** V12 a laissé vivre `core/confluence_adapter.py` et
   `fusion/confluence_adapter.py` divergents de 845 lignes, chacun portant un correctif que
   l'autre n'avait pas.
2. **Pas de docstring qui ment.** Un `riskgate.py` marqué « NON CÂBLÉ » alors que le code
   l'appelle a produit une conclusion d'audit fausse.
3. **Pas de `_archive/` dans l'arbre de travail** (1 601 `.py` morts polluant chaque
   recherche).
4. **Pas de fail-OPEN sur une porte de risque.**

## Le critère de rentabilité — ce qui compte réellement

Une modification n'est un progrès que si elle améliore l'**espérance OOS nette des coûts
Axi réels** (spread + swap). Constats déjà mesurés, à ne pas re-litiger sans nouveau
backtest :

- **Le coût est le tueur dominant** (baseline PF ≈ 0.20 en réel). Un `edge_ok` négatif
  bloque même en mode explore.
- **Élargir les cibles ne sauve rien** — sweep TP fait, R:R 1.5 est l'optimum mesuré.
- **Winrate élevé ≠ profit.** Des configs superbes en Python ont échoué au testeur natif
  (XAUUSD : PF 2.31 → 0.90).
- Le levier restant est la **sélectivité** : ne trader que les contextes mesurés
  profitables. C'est précisément ce que la délibération doit servir.

Proposer une amélioration « de logique » sans chiffre OOS après coûts n'est pas une
amélioration : c'est une hypothèse. La nommer comme telle.

## Collaboration

Prime est responsable technique principal et code owner de V14. Claude, Codex et Hermes
co-développent et assurent des revues consultatives ; Florent arbitre le métier et les
garde-fous réservés. Avant tout échange
inter-agent, lire `collab/HERMES_BRIDGE.md`. Secours hors ligne :
`node tools/collab_bus.mjs send --from claude --to codex --task V14 --content "..."`.
Ne jamais publier de secret sur le bus.

## GitNexus partage

Avant l'analyse ou l'edition, puis avant le passage de relais, executer :

```powershell
powershell -ExecutionPolicy Bypass -File tools/gitnexus_team.ps1 sync
```

Utiliser ensuite GitNexus pour explorer les flux, lancer `impact` upstream avant
toute edition d'un symbole existant et `detect_changes` avant livraison. Voir
`collab/GITNEXUS_TEAM_PROTOCOL.md`. Si le MCP n'est pas charge par le harnais,
utiliser `tools/gitnexus_team.ps1 cli <commande>` depuis le shell de Prime.
