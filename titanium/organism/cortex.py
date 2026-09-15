"""Contrat d'autorite decisionnelle du cortex Hermes, sans execution directe.

Le cortex travaille hors du chemin critique. Il choisit parmi les candidats
scelles et publie une autorisation temporaire ALLOW/WAIT/BLOCK. Il ne peut ni
inventer un candidat, ni choisir une taille, ni toucher au SL/TP, ni appeler
MT5. Les murs d'execution valident ensuite la faisabilite technique.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any

from titanium.organism.contracts import DecisionIdentity, digest

CORTEX_ROLE = "hermes-cortex"
CORTEX_POLICY_VERSION = "hermes-policy-v2"
CORTEX_POLICY_TTL_S = 300
ALLOWED_ACTIONS = frozenset({"ALLOW", "WAIT", "BLOCK"})

#: Duree d'une barre, en minutes. UNE seule table, lue par `policy_ttl_s` et
#: par `market_observed_at` : deux tables auraient fini par diverger, et la
#: fraicheur serait alors calculee sur une duree que la cloture ne connait pas.
BARRE_MINUTES = {
    **{f"M{n}": n for n in (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30)},
    **{f"H{n}": 60 * n for n in (1, 2, 3, 4, 6, 8, 12)},
    "D1": 1440, "W1": 10080,
}

#: Part de la barre pendant laquelle un avis reste pertinent.
#:
#: MESURE DU 07/09/2026 QUI A IMPOSE CE CHANGEMENT. Le TTL etait une constante
#: de 300 s pour tout horizon superieur a M5. Sur 40 demandes consecutives,
#: ZERO n'atteignait Hermes : le decalage minimal barre -> demande est de 466 s
#: (la boucle balaie ~50 portables par tour), donc superieur au TTL dans 100 %
#: des cas. Sur H1 la fenetre valait 5 minutes sur 60 ; sur H4, 5 sur 240.
#:
#: Ce n'etait pas un defaut isole : l'orchestration multi-horizons a deplace
#: les actifs vers H1/H4 pendant que le TTL restait cale sur du M1/M5. Chaque
#: changement etait correct seul ; ensemble ils affamaient le cortex.
#:
#: Le demi-barre retenu le 07/09/2026 a leve une partie du premier goulot, mais
#: il rend encore une politique impossible lorsqu'une demande nait dans la
#: seconde moitie de la barre suivante. Mesure live du 10/09/2026 : une demande
#: M15 creee 13 minutes apres la cloture a recu ALLOW d'Hermes en 96 secondes,
#: puis a ete rejetee CORTEX_POLICY_STALE des sa publication. La garde d'entree
#: confirmait pourtant qu'aucune barre plus recente n'existait.
#:
#: Mesure historique sur 800 demandes reelles, en prenant leur age A LA
#: CREATION — le bon repere, car c'est a cet instant que la garde de fraicheur
#: les juge :
#:
#:     age median a la creation   H1 41 % de la barre · M15 59 % · H4 25 %
#:
#: La boucle balaie 149 actifs en rotation : une demande nait donc rarement en
#: debut de barre, elle nait ou le balayage en est. D'ou la part servable :
#:
#:     TTL  25 % -> 32 % des demandes      TTL  75 % -> 72 %
#:     TTL  50 % -> 64 % des demandes      TTL 100 % -> 96 %
#:
#: La politique vit donc jusqu'a la cloture suivante : c'est exactement la
#: periode pendant laquelle sa source reste la derniere barre cloturee. Elle
#: expire quand une nouvelle barre devient disponible, jamais apres la
#: condition de marche qui l'a produite.
CORTEX_TTL_FRACTION_BARRE = 1.0

#: Planchers hérités, conservés tels quels : sur ces deux horizons le quart de
#: barre (15 s et 75 s) serait plus court que la valeur eprouvee.
CORTEX_TTL_PLANCHER = {"M1": 60, "M5": 120}


def _horizon(context_key: str) -> str:
    """Horizon d'execution porte par la cle de contexte (`...|tf=H1>H4`)."""
    horizon = context_key.rsplit("|tf=", 1)[-1].split(">", 1)[0]
    if "|tf=" not in context_key or not horizon:
        raise ValueError("timeframe cortex manquante")
    return horizon


def policy_ttl_s(context_key: str) -> int:
    """TTL proportionnel a la barre. Exact horizon matching: M15 is not M1."""
    horizon = _horizon(context_key)
    minutes = BARRE_MINUTES.get(horizon)
    if minutes is None:
        # Horizon inconnu : on garde l'ancienne constante, jamais plus large.
        return CORTEX_POLICY_TTL_S
    barre_s = minutes * 60
    ttl = max(int(barre_s * CORTEX_TTL_FRACTION_BARRE),
              CORTEX_TTL_PLANCHER.get(horizon, 0))
    # La barre elle-meme est le plafond absolu : un plancher herite ne doit
    # jamais faire survivre une politique a la condition qui l'a produite.
    # Sans ce min, une barre M2 heritait de 300 s pour 120 s de vie reelle.
    return min(ttl, barre_s)


def _ttl_borne(context_key: str) -> int:
    """Plafond de TTL applicable a ce contexte, sans jamais lever.

    Une cle sans `|tf=` — politique ancienne, doublure de test — retombe sur
    la borne historique. Le plafond ne doit pas dependre d'un format de cle.
    """
    try:
        return policy_ttl_s(str(context_key))
    except (TypeError, ValueError):
        return CORTEX_POLICY_TTL_S


def market_observed_at(bar_time: str, context_key: str, requested_at: str) -> datetime:
    """Date of source bar close, never refreshed by queueing or LLM latency."""
    horizon = context_key.rsplit("|tf=", 1)[-1].split(">", 1)[0]
    minutes = BARRE_MINUTES
    opened = datetime.fromisoformat(bar_time.replace("Z", "+00:00"))
    requested = datetime.fromisoformat(requested_at.replace("Z", "+00:00"))
    if opened.tzinfo is None or requested.tzinfo is None:
        raise ValueError("observation sans fuseau")
    if opened > requested:
        raise ValueError("barre future")
    if "|tf=" not in context_key:
        raise ValueError("timeframe cortex manquante")
    if horizon == "MN1":
        closed = opened.replace(day=1, month=opened.month % 12 + 1,
                                year=opened.year + int(opened.month == 12))
    elif horizon in minutes:
        closed = opened + timedelta(minutes=minutes[horizon])
    else:
        raise ValueError("timeframe cortex inconnue")
    # The request timestamp bounds availability, including a partial source bar.
    return min(closed, requested).astimezone(timezone.utc)


def request_is_current(bar_time: str, context_key: str, requested_at: str,
                       *, now: datetime | None = None) -> bool:
    """La demande porte-t-elle encore sur la derniere barre cloturee ?

    MESURE DU 08/09/2026 QUI A IMPOSE CETTE FONCTION. La garde d'entree de
    `_traiter_lot` comparait l'age depuis la cloture au TTL DE POLITIQUE. Les
    deux repondent a des questions differentes :

        TTL de politique   combien de temps une decision DEJA RENDUE reste
                           valable — un contrat de consommation ;
        garde de demande   les faits envoyes au cortex sont-ils les plus
                           recents qui existent — un contrat d'entree.

    Confondre les deux rejetait des faits qu'aucune donnee plus fraiche ne
    pouvait remplacer. La boucle balaie ~149 actifs, une demande nait donc la
    ou le balayage en est, pas a l'ouverture de barre. Releve sur les 20 000
    dernieres demandes reelles de la memoire centrale :

        H1   95 % portent sur la derniere barre cloturee, garde actuelle 46 %
        H4   97 %                                          garde actuelle 48 %
        M15  97 %                                          garde actuelle 48 %
        M30 100 %                                          garde actuelle  0 %

    Soit 53 % des demandes ecartees avant tout appel alors qu'attendre ne
    pouvait rien apporter : la barre suivante n'avait pas cloture. C'etait le
    premier motif de silence du cortex, devant le quota et les pannes reunis.

    L'invariant de sureté est INCHANGE et vit ou il doit vivre : c'est
    `build_cortex_policy` qui cale `expires_at` sur `source_observed_at + ttl`,
    donc sur la CLOTURE des faits et jamais sur l'heure de reponse. Une demande
    servie tardivement produit une politique d'autant plus courte ; elargir
    l'entree ne prolonge aucune autorisation d'une seconde.

    Retard tolere : la barre en cours (partielle, deja bornee par
    `market_observed_at`) et la derniere barre cloturee. Des qu'une barre plus
    recente a cloture, les faits sont reellement depasses -> False.
    """
    horizon = context_key.rsplit("|tf=", 1)[-1].split(">", 1)[0]
    if "|tf=" not in context_key or not horizon:
        return False
    minutes = BARRE_MINUTES.get(horizon)
    if minutes is None:
        return False
    try:
        opened = datetime.fromisoformat(str(bar_time).replace("Z", "+00:00"))
        requested = datetime.fromisoformat(str(requested_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if opened.tzinfo is None or requested.tzinfo is None:
        return False
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    opened = opened.astimezone(timezone.utc)
    requested = requested.astimezone(timezone.utc)
    # Une barre future, ou une demande antidatee, reste un contrat casse.
    if opened > requested or requested > current + timedelta(seconds=5):
        return False
    barre = timedelta(minutes=minutes)
    # Nombre de barres ecoulees depuis l'ouverture de la barre source. 0 = la
    # barre se forme encore, 1 = elle vient de cloturer, >= 2 = depassee.
    return current - opened < 2 * barre


@dataclass(frozen=True)
class CortexPolicy:
    """Politique cognitive reutilisable, bornee a un contexte et un TTL."""

    policy_ref: str
    source_decision_ref: str
    symbol: str
    side: int
    context_key: str
    action: str
    confidence: float
    summary: str
    evidence_digest: str
    model_version: str
    decision_model_version: str
    prompt_version: str
    policy_version: str
    producer: str
    source_observed_at: str
    created_at: str
    expires_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_cortex_policy(
    identity: DecisionIdentity,
    *,
    context_key: str,
    action: str,
    confidence: float,
    summary: str,
    evidence_digest: str,
    ttl_s: int | None = None,
    now: datetime | None = None,
    producer: str = CORTEX_ROLE,
    source_observed_at: str = "",
    decision_model_version: str = "",
) -> CortexPolicy:
    """Construit une politique scellee; rejette tout contrat ambigu."""
    normalized_action = str(action).upper()
    if normalized_action not in ALLOWED_ACTIONS:
        raise ValueError(f"action cortex interdite: {normalized_action}")
    if not context_key:
        raise ValueError("context_key cortex vide")
    if not evidence_digest:
        raise ValueError("evidence cortex non scellee")
    if not isfinite(float(confidence)):
        raise ValueError("confiance cortex non finie")
    # Le plafond suit l'horizon du contexte, pas une constante unique : une
    # politique H4 valide 3 600 s reste bornee par SA barre, et une politique
    # M1 reste bornee a 60 s. Un plafond global aurait relache les deux.
    #
    # Le defaut suit le meme horizon. Il valait 300 s en dur, ce qui devenait
    # contradictoire des que le plafond a cesse d'etre constant : un appelant
    # sans `ttl_s` sur un contexte M1 demandait 300 s pour une borne de 60.
    borne = _ttl_borne(context_key)
    ttl = borne if ttl_s is None else int(ttl_s)
    if ttl < 1 or ttl > borne:
        raise ValueError(f"TTL cortex hors borne: {ttl}")
    created = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    observed = created
    if source_observed_at:
        try:
            parsed = datetime.fromisoformat(str(source_observed_at))
            if parsed.tzinfo is None:
                raise ValueError("observation cortex sans fuseau")
            observed = parsed.astimezone(timezone.utc)
        except (TypeError, ValueError) as exc:
            raise ValueError("source_observed_at cortex invalide") from exc
    if observed > created + timedelta(seconds=5):
        raise ValueError("observation cortex situee dans le futur")
    # Une reponse tardive ne rajeunit jamais les faits qui l'ont produite.
    expires = min(
        created + timedelta(seconds=ttl),
        observed + timedelta(seconds=ttl),
    )
    stable = {
        "source_decision_ref": identity.decision_ref,
        "symbol": identity.symbol,
        "side": identity.side,
        "context_key": str(context_key),
        "action": normalized_action,
        "confidence": round(max(0.0, min(1.0, float(confidence))), 6),
        "summary": str(summary)[:240],
        "evidence_digest": str(evidence_digest),
        "model_version": identity.model_version,
        "decision_model_version": str(decision_model_version or identity.model_version),
        "prompt_version": identity.prompt_version,
        "policy_version": CORTEX_POLICY_VERSION,
        "producer": str(producer),
        "source_observed_at": observed.isoformat(),
        "created_at": created.isoformat(),
        "expires_at": expires.isoformat(),
    }
    return CortexPolicy(policy_ref=digest(stable), **stable)


def policy_is_fresh(policy: CortexPolicy, *, now: datetime | None = None) -> bool:
    try:
        expiry = datetime.fromisoformat(policy.expires_at)
        created = datetime.fromisoformat(policy.created_at)
        observed = datetime.fromisoformat(policy.source_observed_at)
        if expiry.tzinfo is None or created.tzinfo is None or observed.tzinfo is None:
            return False
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if policy.policy_version != CORTEX_POLICY_VERSION:
            return False
        if created > current + timedelta(seconds=5):
            return False
        if not isfinite(policy.confidence) or not 0.0 <= policy.confidence <= 1.0:
            return False
        if observed > created + timedelta(seconds=5):
            return False
        borne = _ttl_borne(policy.context_key)
        if expiry > created + timedelta(seconds=borne):
            return False
        if expiry > observed + timedelta(seconds=borne):
            return False
        return current <= expiry
    except (TypeError, ValueError):
        return False


EXECUTION_FIELDS = frozenset({
    "lot", "quantity", "price", "entry", "sl", "tp", "order_type",
    "magic", "deviation", "ticket",
})
