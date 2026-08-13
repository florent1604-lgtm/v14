# Validation Prime — 21 dossiers, 13/08/2026

Base : `master` **d1d7f5e** (poussee de production faite pendant cette passe).
Portes franchies avant toute poussee : ruff `E9,F63,F7,F82` propre, suite complete
**1695 passes / 2 skips / 0 echec**, hook pre-commit vert (21 tests de surete).
Services actifs : live_demo 26324, dashboard 8108, analystes 25104 ; boucle armee,
equity 4171.28, 26 tours au moment du controle.

## Ce qui est pousse en production

Commit `d1d7f5e` :

- `tools/rejeu_breakeven.py` + `tests/test_rejeu_breakeven.py` — rejeu du contrefactuel
  qui debite enfin les gagnants coupes ; `ts_exit` ignore quand l horloge de la ligne
  n est pas UTC.
- `titanium/execution/position_manager.py` — marqueur `horloge: "utc"` ecrit aussi sur
  le journal d excursions.
- `docs/LECONS.md`, `collab/CLAUDE_BREAKEVEN_REJEU.md` — la table de seuils n est pas
  monotone ; BE 0.40 est le pire candidat ; seul 0.30 survit au retrait du trade le plus
  influent. **Aucun seuil de production ne bouge.**
- `collab/PRIME_ETAT_RELANCE_20260813.md`, `AGENTS.md`, `CLAUDE.md`.

## Verdicts

### Confirmes en production — 16

| # | Tache | Agent | Preuve rejouee |
|---|---|---|---|
| 1 | `942a3468` filtre de cout | codex | telemetrie COUT_SPREAD distincte dans le heartbeat neuf |
| 2 | `e922f10a` edge net 20 trades | codex | confirme **avec reserve** : chiffres d origine sur horloge fausse |
| 3 | `f4ff06a5` blocage post-ENTER | codex | refus = repetitions, pas opportunites |
| 4 | `96599d0b` debit de deliberation | claude | tests timeout verts dans la suite du 13/08 |
| 5 | `c9770e16` piliers manquants | claude | meme hierarchie G4 > G5 > G3 > G2 post-relance |
| 6 | `46c06912` audit lourd Hermes | hermes | rapport + walk-forward 20260812 et 59edde5 |
| 7 | `ba74e58a` enrichissement ICT | claude | secours displacement cable, A/B sur barres identiques |
| 8 | `fd5be523` collecte DEMO code neuf | codex | 29 limites placees post-relance, plus d ORDER_SEND_NUL |
| 9 | `8083328a` fill naturel d une limite | claude | chaine ETHUSD 89325926 intacte, journal passe a 62 evenements |
| 10 | `f6d05cca` edge directionnel | hermes | rapport + preuve 65bb599 + audit independant meme cote |
| 11 | `2369c40d` suffixes courtier | claude | test verrouille, symboles resolus dans le run neuf |
| 12 | `337bc993` axes de rentabilite | hermes | cadrage consomme par trois taches mesurables |
| 13 | `aff04060` filtre de correlation | claude | `grappes.json` g1..g12, risque de grappe journalise |
| 14 | `fcad5dc6` conventions de cout | codex | `test_cost_conventions.py` dans le hook, vert a la poussee |
| 15 | `c06934eb` scan corrige | claude | telemetrie post-relance coherente (948 / 243 / 692 / 13) |
| 16 | `9e64e360` journal de grappe | claude | 793 lignes, tous les champs presents |

### Renvoyes en revue — 3

**`5a6d72c6` — rapprochement journal/MT5.** Rejoue sur 3 jours :
44 clotures MT5, 43 journalisees, 0 orpheline, 0 doublon, 0 ecart de PnL —
et **une manquante**. La position `89198681` (EURAUD, magic 14000, `titanium-v14`)
est ouverte **et** fermee au stop dans la meme seconde, le 12/08 a 15:30:01Z, pour
−22,48 EUR. La boucle passe toutes les 60 s et ne journalise une cloture que
lorsqu un ticket **suivi** disparait : une position nee et morte entre deux passages
n existe pour personne. Le biais n est pas neutre — ces vies ultra-courtes sont des
stops, donc l esperance mesuree est flattee. Tache P0 ouverte : `cb599499`.

**`f384244f` — cycle de vie des limites.** Remesure post-relance : 29 placees,
7 remplies, 20 expirees, **fill_rate 0,259**, economie moyenne realisee +0,0915R,
slippage −0,0017R, net −2,9883R. Le 0,667 annonce en prod etait un artefact de trois
placements. Le mecanisme est prouve ; le taux d expiration de 69 % est le vrai sujet.

**`65541466` — cout des gagnants coupes.** Le **code** est valide et pousse ;
le **chiffre** ne l est pas : 37 des 43 lignes du journal n ont aucun marqueur
d horloge, 6 clotures seulement sont en vrai UTC.

### Ouvertes avec base propre — 2

**`394a10ce`** — seules **6** clotures portent `horloge=utc` (depuis le 12/08 16:49Z) ;
esperance −0,6742R sur ces 6, aucun contexte au-dela d une cloture. Promotion **fermee**.

**`fd2331ec`** — audit Hermes de l arret non planifie, passe en cours et documente.

## Nouvelle tache P0

`cb599499` (codex) — journaliser les clotures eclair invisibles a la boucle de 60 s.
Preuve attendue : rapprochement a 0 manquante sur 7 jours.
