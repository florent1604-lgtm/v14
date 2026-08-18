"""Exécution MT5 — derrière le mur démo↔réel.

C'est le seul module de V14 autorisé à envoyer un ordre. Il est **désarmé par
défaut** et refuse de fonctionner tant que trois conditions ne sont pas
simultanément réunies.

LE MUR — trois verrous indépendants
-----------------------------------
1. **Armé explicitement** (`ExecutionPolicy.enabled`). Le défaut est `False` :
   importer ce module ne peut rien déclencher.
2. **Le courtier dit DEMO** (`account.trade_mode == 0`). C'est la vérité
   autoritative : elle vient du serveur, pas d'une config locale. Un compte
   « concours » (mode 1) n'est pas un compte démo.
3. **Le login correspond à celui attendu** (`expected_demo_login`). Protège du
   cas où l'opérateur change de compte dans le terminal sans toucher au code.

Les trois sont vérifiés à **chaque** ordre, jamais mis en cache. Le terminal
peut être rebasculé sur le compte réel entre deux appels — c'est précisément
ce qui est arrivé sur cette machine aujourd'hui (compte 60261188 RÉEL le matin,
50061786 DEMO l'après-midi).

Passer en réel demande `allow_real_account=True`, qui n'existe que pour rendre
la décision **explicite et cherchable**. Ce n'est pas un drapeau de confort.

CALCUL DU LOT
-------------
`perte_par_lot = distance_stop / tick_size × tick_value`, puis
`lot = risque_argent / perte_par_lot`, arrondi **vers le bas** au pas du
courtier. Déduire la valeur monétaire du seul `trade_contract_size` est faux dès
que la devise de cotation diffère de celle du compte.

Cas particulier hérité de V12 : quand le lot calculé tombe **sous le minimum du
courtier**, entrer quand même signifie risquer plus que prévu. Sur un compte à
109 USD avec un lot minimum de 0.01, c'est fréquent. Le défaut est de
**refuser** ; `allow_min_lot_overrisk` permet d'accepter en connaissance de
cause, et le dépassement est alors rapporté dans le résultat.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from titanium.data.mt5_vendor import SymbolSpec, ensure_symbol, mt5_session, account_snapshot

# Codes de refus — stables, journalisables, testables.
WALL_DISARMED = "EXEC_DISARMED"
WALL_NOT_DEMO = "WALL_NOT_DEMO"
WALL_LOGIN_MISMATCH = "WALL_LOGIN_MISMATCH"
WALL_NO_EXPECTED_LOGIN = "WALL_NO_EXPECTED_LOGIN"

MT5_TRADE_MODE_DEMO = 0


class ExecutionRefused(RuntimeError):
    """Le mur a refusé. Porte un code stable pour le journal et les tests."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


class LotComputationError(ValueError):
    """Le lot ne peut pas être calculé ou tombe hors des bornes du courtier."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class ExecutionPolicy:
    """Conditions d'armement. Tous les défauts sont les défauts sûrs."""
    enabled: bool = False
    expected_demo_login: int | None = None
    allow_real_account: bool = False
    allow_min_lot_overrisk: bool = False
    magic: int = 14_000
    deviation_points: int = 20
    comment: str = "titanium-v14"

    #: Seule graphie qui ouvre le compte réel. Aucun synonyme n'est accepté :
    #: « yes », « true », « 1 » sont refusés. Cette phrase existe pour rendre la
    #: décision **cherchable** dans l'historique, pas pour le confort.
    REAL_ACCOUNT_PHRASE = "I_UNDERSTAND_THIS_IS_REAL_MONEY"

    @classmethod
    def from_config(cls, config: dict | None = None) -> "ExecutionPolicy":
        """Construit la politique depuis LA config de V14 (`DEFAULT_CONFIG`).

        Une seule source de vérité : les variables ``TITANIUM_*`` du `.env` sont
        déjà projetées dans `DEFAULT_CONFIG` par `_apply_env_overrides`. Ce
        module ne relit pas l'environnement de son côté — c'est ce qui évite
        d'avoir deux systèmes de configuration qui se contredisent.

        Toute valeur absente ou illisible retombe sur le défaut **sûr** : une
        variable mal orthographiée désarme, elle n'arme jamais.
        """
        if config is None:
            from tradingagents.default_config import DEFAULT_CONFIG

            config = DEFAULT_CONFIG

        def _int(cle, defaut):
            try:
                return int(config.get(cle, defaut))
            except (TypeError, ValueError):
                return defaut

        login = _int("demo_login", 0)

        return cls(
            enabled=bool(config.get("exec_enabled", False)),
            # 0 / absent = aucun login déclaré → le mur refusera.
            expected_demo_login=login if login > 0 else None,
            allow_real_account=(str(config.get("allow_real_account", "")).strip()
                                == cls.REAL_ACCOUNT_PHRASE),
            allow_min_lot_overrisk=bool(config.get("allow_min_lot_overrisk", False)),
            magic=_int("magic", 14_000),
            deviation_points=_int("deviation_points", 20),
        )


@dataclass
class OrderResult:
    """Résultat d'une tentative d'ordre. Toujours retourné, jamais None."""
    sent: bool = False
    reason: str = ""
    ticket: int | None = None
    symbol: str = ""
    side: int = 0
    lot: float = 0.0
    price: float | None = None
    sl: float | None = None
    tp: float | None = None
    retcode: int | None = None
    risk_money_requested: float = 0.0
    risk_money_effective: float = 0.0
    overrisk_ratio: float = 1.0  # >1 = le lot minimum force plus de risque que voulu
    idempotency_key: str = ""
    checks: list[dict] = field(default_factory=list)

    def _add(self, gate: str, passed: bool, detail: str = "") -> None:
        self.checks.append({"gate": gate, "passed": bool(passed), "detail": detail})


def assert_can_trade(policy: ExecutionPolicy, account=None):
    """Applique le mur. Lève ``ExecutionRefused`` au premier verrou fermé.

    ⚠️ **Appeler CE mur avant de lire le compte, jamais l'inverse.** Le premier
    verrou est `policy.enabled` et il ne demande rien au courtier : un exécuteur
    désarmé ne doit pas interroger MT5 ni prendre son verrou. Les appelants qui
    lisaient le snapshot d'abord transformaient en plus un `EXEC_DISARMED`
    propre en `WALL_ERREUR` opaque dès que MT5 était injoignable — un motif de
    refus est un dataset, il doit nommer la cause réelle. Défaut relevé par
    Claude le 18/08/2026 sur `mt5_executor` et `limit_orders`, plus un troisième
    appelant, `titanium/web/state.py`, qui rendait « INDISPONIBLE » au lieu de
    « désarmé » sur le tableau de bord.

    Pour que l'ordre correct ne coûte pas un second aller-retour, la fonction
    **rend le snapshot qu'elle a validé** : `acc = assert_can_trade(policy)`
    remplace `acc = account_snapshot(); assert_can_trade(policy, acc)`.

    Args:
        policy: conditions d'armement.
        account: snapshot déjà lu (évite un second aller-retour). Relu si None.

    Returns:
        Le snapshot de compte validé. ``None`` n'est jamais rendu : soit le mur
        s'ouvre et le compte est connu, soit il lève.

    Raises:
        ExecutionRefused: avec un code stable identifiant le verrou.
    """
    if not policy.enabled:
        raise ExecutionRefused(WALL_DISARMED, "exécution désarmée (policy.enabled=False)")

    acc = account if account is not None else account_snapshot()

    if acc.trade_mode != MT5_TRADE_MODE_DEMO and not policy.allow_real_account:
        raise ExecutionRefused(
            WALL_NOT_DEMO,
            f"compte {acc.login} sur {acc.server} : trade_mode={acc.trade_mode} "
            f"(0=démo attendu). Refus absolu.",
        )

    if policy.expected_demo_login is None:
        raise ExecutionRefused(
            WALL_NO_EXPECTED_LOGIN,
            "aucun login démo déclaré : impossible de vérifier l'identité du compte",
        )

    if int(acc.login) != int(policy.expected_demo_login):
        raise ExecutionRefused(
            WALL_LOGIN_MISMATCH,
            f"connecté au compte {acc.login}, attendu {policy.expected_demo_login}",
        )

    return acc


def compute_lot(spec: SymbolSpec, stop_distance: float, risk_money: float,
                *, allow_min_lot_overrisk: bool = False) -> tuple[float, float]:
    """Convertit un risque en argent en volume, aux contraintes du courtier.

    Returns:
        ``(lot, overrisk_ratio)``. ``overrisk_ratio`` vaut 1.0 quand le risque
        demandé est respecté, et >1 quand le lot minimum du courtier force à
        risquer davantage.

    Raises:
        LotComputationError: specs inexploitables, ou lot hors bornes.
    """
    if not (stop_distance > 0 and math.isfinite(stop_distance)):
        raise LotComputationError("STOP_INVALIDE", f"stop_distance={stop_distance!r}")
    if not (risk_money > 0 and math.isfinite(risk_money)):
        raise LotComputationError("RISQUE_INVALIDE", f"risk_money={risk_money!r}")
    if spec.tick_size <= 0 or spec.tick_value <= 0:
        raise LotComputationError(
            "SPECS_INCOMPLETES",
            f"tick_size={spec.tick_size} tick_value={spec.tick_value} — "
            "le courtier n'expose pas la valeur du tick pour ce symbole",
        )
    if spec.volume_step <= 0:
        raise LotComputationError("SPECS_INCOMPLETES", f"volume_step={spec.volume_step}")

    loss_per_lot = (stop_distance / spec.tick_size) * spec.tick_value
    if loss_per_lot <= 0:
        raise LotComputationError("PERTE_PAR_LOT_NULLE", f"loss_per_lot={loss_per_lot}")

    brut = risk_money / loss_per_lot

    # Arrondi VERS LE BAS au pas : on ne dépasse jamais le risque par arrondi.
    pas = spec.volume_step
    lot = math.floor(brut / pas + 1e-9) * pas
    lot = round(lot, 8)

    if lot < spec.volume_min:
        risque_au_min = spec.volume_min * loss_per_lot
        ratio = risque_au_min / risk_money
        if not allow_min_lot_overrisk:
            raise LotComputationError(
                "LOT_SOUS_MINIMUM",
                f"lot calculé {brut:.6f} < minimum {spec.volume_min}. "
                f"Entrer au minimum risquerait {risque_au_min:.2f} au lieu de "
                f"{risk_money:.2f} (×{ratio:.2f}).",
            )
        return spec.volume_min, round(ratio, 4)

    if lot > spec.volume_max:
        lot = spec.volume_max

    return lot, 1.0


def _pick_filling_mode(mt5, symbol: str) -> int:
    """Choisit un mode d'exécution supporté par le symbole.

    Le masque ``filling_mode`` diffère d'un courtier et d'un symbole à l'autre ;
    envoyer un mode non supporté produit un `Unsupported filling mode` opaque.
    On lit le masque et on prend le premier mode compatible.
    """
    info = mt5.symbol_info(symbol)
    masque = int(getattr(info, "filling_mode", 0) or 0)
    # Bit 1 = FOK, bit 2 = IOC (constantes SYMBOL_FILLING_*).
    if masque & 2:
        return mt5.ORDER_FILLING_IOC
    if masque & 1:
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


# Clés d'idempotence déjà envoyées dans CE processus. Volontairement en mémoire :
# une idempotence durable exige un journal persistant (brique suivante). Documenté
# comme tel pour que personne ne prenne ceci pour une garantie après redémarrage.
_sent_keys: set[str] = set()


def reset_idempotency() -> None:
    """Vide le cache d'idempotence (tests, ou nouvelle session de trading)."""
    _sent_keys.clear()


def place_market_order(symbol: str, side: int, risk_money: float,
                       stop_distance: float, *, policy: ExecutionPolicy,
                       tp_distance: float | None = None,
                       idempotency_key: str = "") -> OrderResult:
    """Envoie un ordre au marché, SL/TP attachés, après passage du mur.

    Ne lève jamais : tout refus est un ``OrderResult`` avec ``sent=False`` et un
    motif nommé. Un moteur de trading ne doit pas s'arrêter parce qu'un ordre a
    été refusé.

    Args:
        symbol: instrument tel que nommé par le courtier.
        side: +1 achat, −1 vente.
        risk_money: montant à risquer si le stop est touché, en devise du compte.
        stop_distance: distance du stop en unités de prix (positive).
        policy: conditions d'armement — le mur.
        tp_distance: distance du take-profit. None = pas de TP attaché.
        idempotency_key: si fournie et déjà vue dans ce processus, l'ordre est
            refusé au lieu d'être dupliqué.
    """
    r = OrderResult(symbol=symbol, side=int(side or 0),
                    risk_money_requested=float(risk_money or 0.0),
                    idempotency_key=idempotency_key)

    if r.side not in (-1, 1):
        r.reason = "SIDE_INVALIDE"
        r._add("side", False, f"side={side!r}")
        return r
    r._add("side", True)

    if idempotency_key and idempotency_key in _sent_keys:
        r.reason = "DEJA_ENVOYE"
        r._add("idempotency", False, f"clé déjà vue : {idempotency_key}")
        return r
    r._add("idempotency", True)

    # ── LE MUR. Il passe AVANT toute lecture du compte : désarmé, on ne
    #    demande rien au courtier et le motif reste EXEC_DISARMED.
    try:
        acc = assert_can_trade(policy)
        r._add("wall", True, f"compte {acc.login} {acc.server} trade_mode={acc.trade_mode}")
    except ExecutionRefused as exc:
        r.reason = exc.code
        r._add("wall", False, exc.detail)
        return r
    except Exception as exc:  # noqa: BLE001 — fail-closed
        r.reason = "WALL_ERREUR"
        r._add("wall", False, f"{type(exc).__name__}: {exc}")
        return r

    # ── Lot.
    try:
        spec = ensure_symbol(symbol)
        lot, overrisk = compute_lot(spec, stop_distance, risk_money,
                                    allow_min_lot_overrisk=policy.allow_min_lot_overrisk)
    except LotComputationError as exc:
        r.reason = exc.code
        r._add("lot", False, exc.detail)
        return r
    except Exception as exc:  # noqa: BLE001 — fail-closed
        r.reason = "LOT_ERREUR"
        r._add("lot", False, f"{type(exc).__name__}: {exc}")
        return r

    r.lot = lot
    r.overrisk_ratio = overrisk
    r.risk_money_effective = round(risk_money * overrisk, 2)
    r._add("lot", True, f"lot={lot} overrisk=×{overrisk}")

    # ── Envoi.
    try:
        with mt5_session() as mt5:
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                r.reason = "PAS_DE_PRIX"
                r._add("send", False, "symbol_info_tick vide")
                return r

            if r.side > 0:
                price, order_type = float(tick.ask), mt5.ORDER_TYPE_BUY
            else:
                price, order_type = float(tick.bid), mt5.ORDER_TYPE_SELL
            sign = 1 if r.side > 0 else -1

            sl = round(price - sign * stop_distance, spec.digits)
            tp = (round(price + sign * tp_distance, spec.digits)
                  if tp_distance and tp_distance > 0 else 0.0)

            requete = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": lot,
                "type": order_type,
                "price": price,
                "sl": sl,
                "tp": tp,
                "deviation": policy.deviation_points,
                "magic": policy.magic,
                "comment": policy.comment,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": _pick_filling_mode(mt5, symbol),
            }
            res = mt5.order_send(requete)

            if res is None:
                r.reason = "ORDER_SEND_NUL"
                r._add("send", False, f"last_error={mt5.last_error()}")
                return r

            r.retcode = int(res.retcode)
            r.price = float(getattr(res, "price", price) or price)
            r.sl, r.tp = sl, (tp or None)

            if r.retcode != mt5.TRADE_RETCODE_DONE:
                r.reason = f"RETCODE_{r.retcode}"
                r._add("send", False, f"{getattr(res, 'comment', '')}")
                return r

            r.sent = True
            r.ticket = int(getattr(res, "order", 0)) or None
            r.reason = "OK"
            r._add("send", True, f"ticket={r.ticket} @ {r.price}")
            if idempotency_key:
                _sent_keys.add(idempotency_key)
            return r

    except Exception as exc:  # noqa: BLE001 — fail-closed
        r.reason = "ENVOI_ERREUR"
        r._add("send", False, f"{type(exc).__name__}: {exc}")
        return r
