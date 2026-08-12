# Audit coûts et rapprochement V14 — Codex

Date : 2026-08-11, 21:06 Paris

## Convention du spread

### Verdict

Le chemin principal backtest/live reste cohérent : un spread ask-bid complet
est payé une fois au total. Le backtest applique un demi-spread à l'entrée et
un demi-spread à la sortie ; le live rapporte `spread_prix / distance_stop`.

Les deux corrections déjà livrées restent valides :

1. la documentation de `titanium/backtest.py` décrit désormais les deux
   demi-spreads au lieu de dire « appliqué deux fois » ;
2. `titanium/selection.py::_cout_relatif` ne double plus le spread complet dans
   le classement hors ligne.

Test de non-régression relancé le 11/08 :

```text
pytest tests/test_cost_conventions.py tests/test_echelle.py tests/test_sizing.py
       tests/test_stop_temporel.py tests/test_reconciliation.py
       tests/test_journal_live.py
142 passed in 3.33s
```

## Rapprochement journal ↔ MT5

Fenêtre : 2026-08-10 10:00 UTC → 2026-08-11 19:06 UTC.

- positions MT5 closes : **26** ;
- lignes live du journal : **26** ;
- appariées : **26** ;
- manquantes : **0** ;
- orphelines : **0** ;
- doublons : **0** ;
- divergences PnL : **0** ;
- net MT5 : **-226,48 EUR** ;
- net stratégie : **-226,48 EUR** ;
- coûts exacts manquants : **26/26**.

Rapport machine : `results/reconciliation_mt5_recent.json`.

`position_manager.manage_once()` effectue déjà ce rapprochement après chaque
disparition d'un ticket, conserve le contexte et réessaie en cas de
`JOURNAL_GAP`. Le rapport strict sort volontairement en échec tant que les
coûts exacts manquent ; il ne signale actuellement aucun trou de journal.

## Pourquoi `exact_cost` reste faux

Le comportement est intentionnel dans le code actuel : `live_demo` enregistre
le spread observé avant l'ordre avec `spread_exact=false`. L'historique des
deals fournit ensuite le PnL net, la commission, le swap et les frais exacts,
mais pas la cotation bid/ask opposée exactement au moment de chaque fill.

Un probe read-only sur trois tickets récents confirme que les ticks historiques
sont disponibles, mais que le tick le plus proche se situe entre **16 ms et
555 ms** du deal. Cette donnée permet une estimation meilleure et une mesure de
slippage, pas une décomposition « exacte » sans convention de tolérance
documentée. Marquer rétroactivement ces 26 lignes `exact_cost=true` inventerait
une précision que le broker ne fournit pas.

La voie correcte pour les futures observations est un contrat versionné qui
journalise les quotes bid/ask et leur `time_msc` aux fills d'entrée et de
sortie, sépare spread et slippage, et reste fail-closed hors tolérance. Ce
changement touche le critère de promotion et exige une revue Prime avant
implémentation ; il ne faut pas réécrire le journal append-only existant.

## Verdict de tâche

L'automatisation du rapprochement et la convention de spread sont validées.
La tâche d'ingénierie peut passer `done`; le manque de coûts exacts reste un
bloqueur de promotion suivi par la tâche P0 de collecte, pas un défaut
d'appariement.
