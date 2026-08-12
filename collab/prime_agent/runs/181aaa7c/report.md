# Prime — 181aaa7c : mise en production, redemarrage des services, reindexation

Date : 2026-08-12 (reprise de session Prime Agent)
Perimetre : PAPER/DEMO uniquement. Aucun ordre reel, aucun `.env` lu ou modifie,
aucun redemarrage MT5 ni de la boucle de trading pendant cette reprise.

## 1. Etat Git

```
063c98e 2026-08-12 08:35 V14: contexte gitnexus regenere apres reindexation complete (6006 noeuds, 13096 aretes)
5782a55 2026-08-12 08:33 V14: ordre du jour collab + assertions ollama insensibles au formatage rich
5c5884e 2026-08-12 08:29 V14: ordres limites, contexte pending, enrichissement ICT, conventions de cout, skills unifies
```
`git status --porcelain -b` : `## master`, arbre de travail propre, rien a committer.

## 2. Services (verification, pas de redemarrage)

Tous les services tournent deja sur le code de 5c5884e/5782a55, demarres le 12/08 a 08:33-08:34,
soit apres le commit de mise en production. Aucun redemarrage n'etait donc necessaire.

| Service | PID | Demarre | Etat |
|---|---|---|---|
| `tools/dashboard.py` | 10168 | 12/08 08:33:34 | actif |
| `tools/analystes.py` | 7260 | 12/08 08:33:42 | actif |
| `tools/live_demo.py --armer` | 28088 | 12/08 08:33:46 | actif, DEMO 10055401 |
| `collab_terminal/server.py` :8097 | 27496 | 12/08 12:13 | actif (HTTP 200) |
| collab-hub :8770 | 24540 | — | ACTIF (`collab_services.ps1 status`) |
| hermes-mcp :8766 | 18736 | — | ACTIF |

## 3. Reindexation GitNexus

`tools/gitnexus_team.ps1 sync` : « GitNexus deja synchronise pour l'etat de travail courant
(0B7E4B71...D230B6) ». L'index `titanium-v14` couvre bien le code neuf (6006 symboles,
13096 relations), reindexe et committe en 063c98e.

## 4. Suite de tests complete

```
.venv\Scripts\python.exe -m pytest -q
1576 passed, 2 skipped, 18 warnings, 69 subtests passed in 162.53s
```
1578 tests collectes. Les 2 skips sont attendus : `langchain_aws` absent, `DEEPSEEK_API_KEY`
non defini. Zero echec. L'objectif « 1042 tests » de la fiche est depasse (+534).

## 5. Mesure de la boucle sur le code neuf (heartbeat 2026-08-12T10:28:41.317043+00:00)

- armee, DEMO, equity 4407.87 EUR, 43 portables, 213 tours depuis le restart
- flux : catalogue 31737 -> selectionnes 13288 -> portables 8974 (non portables 4314)
- verdicts : ENTER 1515, BLOCK 7299, WAIT 117
- piliers manquants / 8974 portables : G4_OTE_OB 8181 (91.2%),
  G5_CANDLE 8055 (89.8%),
  G3_LIQUIDITY 6000 (66.9%),
  G2_FAIR_VALUE 5502 (61.3%)
- refus post-ENTER : RISKGATE_DENY 690, EXECUTION 541,
  MAX_PAR_SYMBOLE 155, DERIVE 128, COUT_SPREAD 1
- **0 ordre envoye** depuis le restart : les 1515 ENTER sont tous refuses en aval.

Reference avant commit (ordre du jour, 29780 evaluations) : G4 27769 = 93.2 %, G5 27338 = 91.8 %.
Apres commit : G4 91.2%, G5 89.8%.
L'enrichissement ICT de 5c5884e n'apporte donc qu'un gain marginal (~1,6 pt sur G4, ~1,7 pt sur G5) :
le blocage structurel des piliers reste entier. C'est la matiere de la tache Claude `ba74e58a`.

## 6. Etat de la collecte DEMO

- 4 positions ouvertes (CHFSEK, AUDSGD, USDSGD, EURSGD), compte 10055401, mode explore, quorum 2.
- Dernier rapprochement (`reconciliation_mt5_recent.json`, 11/08 19:06 UTC) : 26 clotures MT5 /
  26 au journal, 26/26 rapprochees, 0 manquante, 0 orpheline, 0 doublon, net -226,48 EUR
  identique des deux cotes. `ok:false` provient du seul champ `exact_cost_missing` (26/26),
  limitation courtier deja documentee.
- Aucune cloture nouvelle depuis le 11/08 21:06 et aucun ordre depuis le restart : la collecte
  edge (`394a10ce`) et la semaine de donnees (`803b129b`) restent bornees par le temps de marche,
  pas par le code.

## 7. Chemin des ordres limites

`tools/live_demo.py` importe bien `titanium.execution.pending_context.save_pending_context`
(l. 660/677), `titanium.execution.limit_orders.place_limit_order` (l. 695) et
`reconcile_pending_contexts` (l. 697/830). Le chemin est cable, mais **non exerce en production**
faute d'ordre envoye depuis le restart. Point a mesurer par Codex (`fd5be523`) des la prochaine
serie d'ordres.

## 8. Risques residuels

1. **531 refus EXECUTION** sur 1487 ENTER : deuxieme cause de refus post-ENTER, non expliquee a ce
   jour. A instrumenter (sous-motif) avant toute conclusion sur l'edge.
2. Piliers G4/G5 toujours manquants a ~90 % : le tunnel ne produit presque aucune entree valide.
3. Aucune preuve terrain du chemin ordres limites.
4. Cout exact indisponible 26/26 : l'expectancy nette reste une estimation basse.

## 9. Conclusion

Le contenu de la fiche `181aaa7c` est realise et prouve : code en production, services actifs sur
le code neuf, index GitNexus a jour, suite complete verte (1576/1578, 0 echec). Tache passee a
`done`. Les trois autres P0 (`46c06912` Hermes, `fd5be523` Codex, `ba74e58a` Claude) sont relances
avec la mesure de reference ci-dessus.
