"""Mise à plat des positions hors crypto avant la fermeture hebdomadaire.

Une position d'indice, de FX, de métal ou d'énergie laissée ouverte du
vendredi soir au dimanche soir ne travaille pas : le marché est fermé, le
stop ne peut pas être géré avant la réouverture, et le swap court pendant
tout ce temps. Mesuré le 12/09/2026 sur `DAX40.fs` #108485347 : **+6.38 EUR
de gain brut contre −45.25 EUR de swap**, soit une position gagnante rendue
perdante par le seul portage. La clôture est donc demandée **même en perte** :
le coût du portage est certain, le retour du prix ne l'est pas.

La crypto est **exemptée**. Elle cote sans interruption — mesuré le
12/09/2026 : 29 marchés ouverts sur les 149 du courtier, et les 29 sont des
cryptos. La mettre à plat reviendrait à renoncer au seul marché du week-end.

L'HEURE EST CELLE DU SERVEUR, JAMAIS CELLE DU POSTE
---------------------------------------------------
Les sessions du courtier sont définies en heure serveur (Axi = GMT+3, mesuré).
Un calcul contre l'horloge locale décale la fenêtre de trois heures : la
clôture partirait à 22:00 UTC alors que le marché ferme à 21:00 UTC, donc
**jamais**. C'est le même piège que `sizing.RETARD_MAX_MIN` documente pour la
fraîcheur des ticks.

On ne calcule pas ce décalage, on **lit l'horloge**. Le tick d'un actif qui
cote en continu porte déjà l'heure serveur : `datetime.fromtimestamp(
tick.time, tz=utc)` la rend directement. Estimer un décalage rend `0` quand
la mesure échoue, et `0` est indiscernable d'un serveur réellement en UTC —
une ambiguïté qui ferait manquer la fenêtre en silence.

SENS DE L'ERREUR
----------------
Horloge illisible ⇒ **aucune clôture**. Envoyer un ordre est irréversible et
coûte le spread ; ne pas l'envoyer coûte un week-end de swap. Face à une
horloge dont on ne sait rien, on ne touche pas au marché.

Classe d'actif inconnue ⇒ **mise à plat**. Le sens est inversé par rapport à
l'horloge, et c'est délibéré : une crypto tombée en classe vide serait
reprise par le moteur au tour suivant, alors qu'un indice gardé par erreur
saigne 48 h de swap sans gestion de stop possible. La classification crypto
est fiable — les 29 cryptos ouvertes du 12/09 sont toutes résolues.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

#: Convention `datetime.weekday()` : lundi = 0, vendredi = 4.
LUNDI = 0
VENDREDI = 4

_MINUTES_PAR_JOUR = 24 * 60
_MINUTES_PAR_SEMAINE = 7 * _MINUTES_PAR_JOUR

#: Classes qui cotent sans interruption et ne sont jamais mises à plat.
CLASSES_EXEMPTEES = frozenset({"crypto"})

#: Symboles lus pour dater le serveur. Ils doivent coter le week-end, sinon
#: leur dernier tick serait figé au vendredi et daterait le serveur dans le
#: passé — l'horloge indiquerait éternellement vendredi soir.
SYMBOLES_HORLOGE_CONTINUE = ("BTCUSD", "ETHUSD")

#: Décalage plausible entre un fuseau de courtier et UTC. Au-delà, ce qu'on
#: lit n'est pas une horloge mais un tick mort : on préfère ne rien savoir.
DECALAGE_PLAUSIBLE_MAX_S = 14 * 3600

#: Retard maximal du tick d'un symbole sur l'horloge serveur avant de le
#: déclarer fermé. Même seuil que `sizing.RETARD_MAX_MIN`, même raison.
RETARD_MAX_MIN = 20.0


@dataclass(frozen=True)
class WeekendFlatParams:
    """Fenêtre de mise à plat, exprimée en **heure serveur**.

    Le début est volontairement placé avant la fermeture et non collé à
    elle : les trente dernières minutes du vendredi sont les moins liquides
    de la semaine, et sortir dedans paie un spread élargi. Vendredi 22:00
    serveur = 19:00 UTC = 15:00 à New York — la séance américaine est encore
    pleine, la sortie se fait au spread normal.

    La fin déborde sur lundi 01:00 serveur pour couvrir la réouverture du
    dimanche soir sans rouvrir une position dans le trou de liquidité de
    l'ouverture.
    """

    actif: bool = True
    jour_debut: int = VENDREDI
    heure_debut: int = 22
    minute_debut: int = 0
    jour_fin: int = LUNDI
    heure_fin: int = 1
    minute_fin: int = 0


@dataclass(frozen=True)
class WeekendFlatDecision:
    """Verdict de mise à plat pour une position."""

    should_exit: bool = False
    reason: str = ""


def _minutes_de_semaine(jour: int, heure: int, minute: int) -> int | None:
    """Position dans la semaine en minutes depuis lundi 00:00, ou ``None``."""
    try:
        jour, heure, minute = int(jour), int(heure), int(minute)
    except (TypeError, ValueError):
        return None
    if not (0 <= jour <= 6 and 0 <= heure <= 23 and 0 <= minute <= 59):
        return None
    return jour * _MINUTES_PAR_JOUR + heure * 60 + minute


def dans_fenetre(serveur_maintenant: datetime,
                 params: WeekendFlatParams | None = None) -> bool | None:
    """L'instant serveur tombe-t-il dans la fenêtre ? ``None`` si indécidable.

    La fenêtre enjambe la fin de semaine, donc elle **boucle** : début
    vendredi 22:00 (7080 min) et fin lundi 01:00 (60 min) veut dire
    « après le début OU avant la fin », jamais « entre les deux ».
    """
    params = params or WeekendFlatParams()
    if not isinstance(serveur_maintenant, datetime):
        return None

    debut = _minutes_de_semaine(
        params.jour_debut, params.heure_debut, params.minute_debut)
    fin = _minutes_de_semaine(
        params.jour_fin, params.heure_fin, params.minute_fin)
    if debut is None or fin is None or debut == fin:
        return None

    maintenant = _minutes_de_semaine(
        serveur_maintenant.weekday(),
        serveur_maintenant.hour,
        serveur_maintenant.minute,
    )
    if maintenant is None:
        return None

    if debut < fin:
        return debut <= maintenant < fin
    return maintenant >= debut or maintenant < fin


def decide_weekend_flat(classe_actif: str,
                        serveur_maintenant: datetime | None,
                        params: WeekendFlatParams | None = None,
                        ) -> WeekendFlatDecision:
    """Faut-il mettre cette position à plat pour le week-end ?

    Fonction **pure** : aucun appel MT5, aucune lecture de fichier, aucune
    horloge locale. Tout ce qui décide est dans les arguments, donc tout est
    testable hors ligne — c'est la même séparation que `decide_new_sl`.
    """
    params = params or WeekendFlatParams()

    if not params.actif:
        return WeekendFlatDecision(False, "DESACTIVE")

    classe = str(classe_actif or "").strip().lower()
    if classe in CLASSES_EXEMPTEES:
        return WeekendFlatDecision(False, "CLASSE_EXEMPTEE")

    if serveur_maintenant is None:
        return WeekendFlatDecision(False, "HORLOGE_INCONNUE")

    verdict = dans_fenetre(serveur_maintenant, params)
    if verdict is None:
        return WeekendFlatDecision(False, "FENETRE_INVALIDE")
    if not verdict:
        return WeekendFlatDecision(False, "HORS_FENETRE")
    return WeekendFlatDecision(True, "FENETRE_WEEKEND")


def heure_serveur_mt5(mt5, symboles=SYMBOLES_HORLOGE_CONTINUE,
                      *, maintenant: datetime | None = None,
                      ) -> datetime | None:
    """Horloge murale du serveur, lue du tick le plus avancé. ``None`` sinon.

    Coquille d'I/O mince autour de `decide_weekend_flat` : elle ne décide
    rien, elle date. On retient le tick le PLUS AVANCÉ — un symbole endormi
    sous-estimerait l'heure, le maximum ne se laisse pas tromper.

    Ne lève jamais : une horloge illisible rend ``None``, et l'appelant
    s'abstient d'agir.
    """
    reference = maintenant or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        return None

    candidats = []
    for symbole in [s for s in symboles if s]:
        try:
            tick = mt5.symbol_info_tick(symbole)
            epoch = float(getattr(tick, "time", 0) or 0)
        except Exception:  # noqa: BLE001 — dater ne casse jamais un tour
            continue
        if epoch <= 0:
            continue
        lu = datetime.fromtimestamp(epoch, tz=timezone.utc)
        if abs((lu - reference).total_seconds()) <= DECALAGE_PLAUSIBLE_MAX_S:
            candidats.append(lu)

    return max(candidats) if candidats else None


def marche_cote(mt5, symbole: str, serveur_maintenant: datetime | None,
                *, retard_max_min: float = RETARD_MAX_MIN) -> bool:
    """Le symbole cote-t-il encore ? Comparé à l'horloge **serveur**.

    Sans cette garde, une position hors crypto encore ouverte le samedi ferait
    partir une demande de clôture à chaque tour sur un marché fermé : dix
    secondes d'intervalle pendant trente-six heures, soit plus de douze mille
    ordres refusés. Un ordre qui ne peut pas aboutir ne doit pas être émis.

    Rend ``False`` dès que le doute existe — tick absent, horloge inconnue,
    lecture en erreur. Ne pas envoyer est le sens sûr : la position est de
    toute façon inatteignable tant que le marché dort.
    """
    if serveur_maintenant is None:
        return False
    try:
        tick = mt5.symbol_info_tick(symbole)
        epoch = float(getattr(tick, "time", 0) or 0)
    except Exception:  # noqa: BLE001 — une sonde ne casse jamais un tour
        return False
    if epoch <= 0:
        return False
    retard = (serveur_maintenant
              - datetime.fromtimestamp(epoch, tz=timezone.utc)).total_seconds()
    return retard <= retard_max_min * 60.0
