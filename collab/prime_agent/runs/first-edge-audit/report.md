# Rapport — première mission Prime Agent

Date : 2026-08-09

## Verdict

Le défaut de comptage de la clé de contexte est déjà corrigé dans le code :

- `context_from_feats()` délègue à `confluence_gate.evaluate()` ;
- `context_from_decision()` compte les portes passées de `_SUPPORT_PILLARS` et
  ajoute `trend_sr` à la signature ;
- `test_les_deux_constructions_concordent` empêche une nouvelle divergence.

La section correspondante de `CLAUDE.md` était restée obsolète. Prime Agent a
effectué la première modification, mais sa réponse Groq suivante contenait un
appel d'outil JSON invalide. Codex a donc contrôlé et finalisé la formulation
pour qu'elle décrive exactement le comportement actuel.

## Fichiers modifiés

- `CLAUDE.md` — état du défaut synchronisé avec le code actuel.
- `collab/prime_agent/runs/first-edge-audit/report.md` — présent rapport.

`titanium/edge.py` et `tests/test_edge_and_loop.py` n'ont pas été modifiés.

## Preuves

Commande :

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_edge_and_loop.py
```

Résultat : **25 passed in 0.50s**.

Contrôle élargi effectué avant la mission : tests edge, journal live et
promotion, **102 passed in 2.80s**.

## État Prime Agent

- `prime-agent 0.7.1` installé ;
- clés fournisseurs enregistrées dans le coffre privé sans affichage ;
- Anthropic et OpenAI bloqués par absence de crédits ;
- Google limité par quota ;
- Groq opérationnel après réduction de la sortie maximale ;
- noyau Windows dédié `kernel-win` fonctionnel avec `ipykernel` et
  `prime-agent-runtime`.

## Risques résiduels

- Le daemon Prime Agent 0.7.1 produit des erreurs `EPERM fsync` sous Windows ;
  les missions sont donc lancées en mode `--no-session`.
- Le quota Groq impose des missions courtes et des sorties d'outil bornées.
- Toute modification Prime Agent doit rester relue et testée tant que ces deux
  contraintes persistent.
