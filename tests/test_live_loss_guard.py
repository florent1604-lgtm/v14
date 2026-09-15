import json
from datetime import datetime, timedelta, timezone

from titanium.execution.live_loss_guard import (
    LiveLossVerdict,
    evaluate_live_loss_guard,
    live_loss_guard_path,
    persist_live_loss_quarantine,
    read_live_loss_guard,
)

NOW = datetime(2026, 9, 9, 16, 0, tzinfo=timezone.utc)


def write_trades(path, rows):
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def trade(pnl_r, closed_at, *, account="123", ticket="1"):
    return {
        "context": "US30|short|continuation|3p",
        "pnl_r": pnl_r,
        "closed_at": closed_at.isoformat(),
        "ticket": f"live:{ticket}",
        "source": "live",
        "account": account,
        "exact_net": True,
        "horloge": "utc",
    }


def test_missing_journal_blocks_arming(tmp_path):
    verdict = evaluate_live_loss_guard(tmp_path / "missing.ndjson", account="123", now=NOW)

    assert verdict.action == "WAIT"
    assert verdict.reason == "LIVE_LOSS_JOURNAL_MISSING"


def test_daily_loss_of_two_r_blocks_new_entries(tmp_path):
    path = tmp_path / "trades.ndjson"
    write_trades(path, [
        trade(-0.75, NOW - timedelta(hours=3), ticket="1"),
        trade(-1.0, NOW - timedelta(hours=2), ticket="2"),
        trade(-0.3, NOW - timedelta(hours=1), ticket="3"),
    ])

    verdict = evaluate_live_loss_guard(path, account="123", now=NOW)

    assert verdict.action == "BLOCK"
    assert verdict.reason == "DAILY_LOSS_LIMIT"
    assert verdict.daily_trades == 3
    assert verdict.daily_net_r == -2.05


def test_rolling_loss_blocks_after_daily_boundary(tmp_path):
    path = tmp_path / "trades.ndjson"
    write_trades(path, [
        trade(-1.5, NOW - timedelta(days=offset), ticket=str(offset))
        for offset in (1, 2, 3, 4)
    ])

    verdict = evaluate_live_loss_guard(path, account="123", now=NOW)

    assert verdict.action == "BLOCK"
    assert verdict.reason == "ROLLING_7D_LOSS_LIMIT"
    assert verdict.rolling_net_r == -6.0


def test_other_accounts_old_rows_and_duplicate_tickets_are_excluded(tmp_path):
    path = tmp_path / "trades.ndjson"
    write_trades(path, [
        trade(-9.0, NOW - timedelta(hours=1), account="999", ticket="other"),
        trade(-9.0, NOW - timedelta(days=8), ticket="old"),
        trade(-1.0, NOW - timedelta(hours=2), ticket="same"),
        trade(0.5, NOW - timedelta(hours=1), ticket="same"),
    ])

    verdict = evaluate_live_loss_guard(path, account="123", now=NOW)

    assert verdict.action == "ALLOW"
    assert verdict.daily_trades == 1
    assert verdict.daily_net_r == 0.5


def test_legacy_rows_outside_window_do_not_poison_current_guard(tmp_path):
    path = tmp_path / "trades.ndjson"
    write_trades(
        path,
        [
            {
                "source": "live",
                "account": "123",
                "closed_at": (NOW - timedelta(days=30)).isoformat(),
                "ticket": "live:legacy",
            },
            trade(0.2, NOW - timedelta(hours=1), ticket="current"),
        ],
    )

    verdict = evaluate_live_loss_guard(path, account="123", now=NOW)

    assert verdict.action == "ALLOW"
    assert verdict.rolling_trades == 1


def test_operator_cohort_start_excludes_prior_losses(tmp_path):
    path = tmp_path / "trades.ndjson"
    cohort_start = NOW - timedelta(minutes=30)
    write_trades(path, [
        trade(-8.0, cohort_start - timedelta(minutes=1), ticket="prior"),
    ])

    verdict = evaluate_live_loss_guard(
        path,
        account="123",
        now=NOW,
        not_before=cohort_start,
    )

    assert verdict.action == "ALLOW"
    assert verdict.reason == "WITHIN_LOSS_LIMITS"
    assert verdict.rolling_trades == 0
    assert verdict.rolling_net_r == 0.0


def test_operator_cohort_start_keeps_new_losses_guarded(tmp_path):
    path = tmp_path / "trades.ndjson"
    cohort_start = NOW - timedelta(minutes=30)
    write_trades(path, [
        trade(-8.0, cohort_start - timedelta(minutes=1), ticket="prior"),
        trade(-2.0, cohort_start, ticket="new"),
    ])

    verdict = evaluate_live_loss_guard(
        path,
        account="123",
        now=NOW,
        not_before=cohort_start,
    )

    assert verdict.action == "BLOCK"
    assert verdict.reason == "DAILY_LOSS_LIMIT"
    assert verdict.daily_trades == 1
    assert verdict.daily_net_r == -2.0


def test_invalid_operator_cohort_start_fails_closed(tmp_path):
    path = tmp_path / "trades.ndjson"
    write_trades(path, [trade(0.2, NOW - timedelta(minutes=1))])

    naive = evaluate_live_loss_guard(
        path,
        account="123",
        now=NOW,
        not_before=NOW.replace(tzinfo=None),
    )
    future = evaluate_live_loss_guard(
        path,
        account="123",
        now=NOW,
        not_before=NOW + timedelta(seconds=1),
    )
    wrong_type = evaluate_live_loss_guard(
        path,
        account="123",
        now=NOW,
        not_before="2026-09-09T15:30:00Z",  # type: ignore[arg-type]
    )

    assert naive.action == "WAIT"
    assert naive.reason == "LIVE_LOSS_JOURNAL_INVALID"
    assert future.action == "WAIT"
    assert future.reason == "LIVE_LOSS_JOURNAL_INVALID"
    assert wrong_type.action == "WAIT"
    assert wrong_type.reason == "LIVE_LOSS_JOURNAL_INVALID"


def test_malformed_or_untrusted_recent_line_fails_closed(tmp_path):
    path = tmp_path / "trades.ndjson"
    path.write_text("not-json\n", encoding="utf-8")

    verdict = evaluate_live_loss_guard(path, account="123", now=NOW)

    assert verdict.action == "WAIT"
    assert verdict.reason == "LIVE_LOSS_JOURNAL_INVALID"


def test_loss_quarantine_persists_after_rolling_window_recovers(tmp_path):
    path = tmp_path / "quarantine.json"
    blocked = LiveLossVerdict(
        action="BLOCK",
        reason="ROLLING_7D_LOSS_LIMIT",
        rolling_trades=98,
        rolling_net_r=-15.3292,
    )

    first = persist_live_loss_quarantine(
        blocked, path=path, account="10055401", now=NOW,
    )
    recovered = persist_live_loss_quarantine(
        LiveLossVerdict(action="ALLOW", reason="WITHIN_LOSS_LIMITS"),
        path=path,
        account="10055401",
        now=NOW + timedelta(days=8),
    )

    assert first.action == "BLOCK"
    assert first.reason == "PERSISTENT_LOSS_QUARANTINE"
    assert recovered.action == "BLOCK"
    assert recovered.reason == "PERSISTENT_LOSS_QUARANTINE"
    assert recovered.rolling_net_r == 0.0
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["account"] == "10055401"
    assert payload["trigger"]["rolling_net_r"] == -15.3292


def test_invalid_or_wrong_account_quarantine_fails_closed(tmp_path):
    path = tmp_path / "quarantine.json"
    path.write_text('{"schema": 1, "account": "999"}', encoding="utf-8")

    verdict = persist_live_loss_quarantine(
        LiveLossVerdict(action="ALLOW", reason="WITHIN_LOSS_LIMITS"),
        path=path,
        account="10055401",
        now=NOW,
    )

    assert verdict.action == "WAIT"
    assert verdict.reason == "LIVE_LOSS_QUARANTINE_INVALID"


def test_une_ligne_ancienne_sans_fuseau_ne_condamne_pas_le_verdict(tmp_path):
    """L'intention de `legacy_rows_outside_window` tient aussi sans fuseau.

    Une ligne ecrite avant la discipline UTC porte un horodatage nu. Six
    semaines avant la fenetre, aucune lecture de fuseau ne la ramene dedans :
    elle doit etre ignoree, pas condamner le verdict a elle seule.
    """

    path = tmp_path / "trades.ndjson"
    write_trades(path, [
        {
            "source": "live",
            "account": "123",
            "closed_at": (NOW - timedelta(days=45)).replace(tzinfo=None).isoformat(),
            "pnl_r": -9.0,
            "ticket": "live:legacy",
            "horloge": "utc",
            "exact_net": True,
        },
        trade(0.2, NOW - timedelta(hours=1), ticket="current"),
    ])

    verdict = evaluate_live_loss_guard(path, account="123", now=NOW)

    assert verdict.action == "ALLOW"
    assert verdict.daily_net_r == 0.2


def test_une_ligne_ancienne_dans_le_futur_ne_condamne_pas_le_verdict(tmp_path):
    """Deux mois dans le futur : aucune lecture ne la ramene dans la fenetre."""

    path = tmp_path / "trades.ndjson"
    write_trades(path, [
        {
            "source": "live",
            "account": "123",
            "closed_at": (NOW + timedelta(days=60)).isoformat(),
            "pnl_r": -9.0,
            "ticket": "live:avance",
            "horloge": "utc",
            "exact_net": True,
        },
        trade(0.2, NOW - timedelta(hours=1), ticket="current"),
    ])

    verdict = evaluate_live_loss_guard(path, account="123", now=NOW)

    assert verdict.action == "ALLOW"
    assert verdict.daily_net_r == 0.2


def test_une_ligne_pertinente_non_situee_fait_toujours_attendre(tmp_path):
    """Le fail-closed tient : ce qui peut tomber dans la fenetre reste exigeant.

    Un horodatage nu, illisible, ou date de trois heures dans le futur
    (constat de l'horloge serveur Axi) ne prouve pas qu'il est hors fenetre :
    il fait attendre, il n'est pas ignore en silence.
    """

    path = tmp_path / "trades.ndjson"

    nu = trade(0.2, NOW - timedelta(hours=1), ticket="nu")
    nu["closed_at"] = nu["closed_at"].replace("+00:00", "")
    write_trades(path, [nu])
    assert evaluate_live_loss_guard(path, account="123", now=NOW).reason == (
        "LIVE_LOSS_JOURNAL_INVALID"
    )

    illisible = trade(0.2, NOW - timedelta(hours=1), ticket="x")
    illisible["closed_at"] = "pas-une-date"
    write_trades(path, [illisible])
    assert evaluate_live_loss_guard(path, account="123", now=NOW).reason == (
        "LIVE_LOSS_JOURNAL_INVALID"
    )

    write_trades(path, [trade(0.2, NOW + timedelta(hours=3), ticket="futur")])
    assert evaluate_live_loss_guard(path, account="123", now=NOW).reason == (
        "LIVE_LOSS_JOURNAL_INVALID"
    )


def test_le_doublon_de_ticket_se_lit_sur_la_date_de_cloture(tmp_path):
    """Un journal reordonne ne doit pas changer le verdict.

    Meme paire de lignes, deux ordres de fichier : la date de cloture la plus
    recente gagne dans les deux cas. Sinon le meme compte passe d'ALLOW a un
    faux BLOCK selon l'ordre des lignes d'un fichier repare ou rejoue.
    """

    recente = trade(0.5, NOW - timedelta(hours=1), ticket="7")
    ancienne = trade(-3.0, NOW - timedelta(hours=2), ticket="7")

    for nom, lignes in (("ordre", [ancienne, recente]),
                        ("inverse", [recente, ancienne])):
        path = tmp_path / f"{nom}.ndjson"
        write_trades(path, lignes)

        verdict = evaluate_live_loss_guard(path, account="123", now=NOW)

        assert verdict.action == "ALLOW", nom
        assert verdict.daily_net_r == 0.5, nom


def test_le_verdict_bloque_dit_pourquoi(tmp_path):
    """Un verdict bloque porte le motif et la date du declencheur.

    Sans eux l'operateur lit un BLOCK dont les chiffres sont revenus dans les
    limites, sans la cause qui n'existait que dans le fichier du verrou.
    """

    path = tmp_path / "quarantine.json"
    pose = persist_live_loss_quarantine(
        LiveLossVerdict(
            action="BLOCK",
            reason="DAILY_LOSS_LIMIT",
            daily_trades=9,
            daily_net_r=-2.8193,
        ),
        path=path,
        account="10055401",
        now=NOW,
    )

    assert pose.action == "BLOCK"
    assert pose.quarantine_reason == "DAILY_LOSS_LIMIT"
    assert pose.quarantine_since == NOW.isoformat()

    relu = persist_live_loss_quarantine(
        LiveLossVerdict(action="ALLOW", reason="WITHIN_LOSS_LIMITS"),
        path=path,
        account="10055401",
        now=NOW + timedelta(days=8),
    )

    assert relu.action == "BLOCK"
    assert relu.reason == "PERSISTENT_LOSS_QUARANTINE"
    assert relu.quarantine_reason == "DAILY_LOSS_LIMIT"
    assert relu.quarantine_since == NOW.isoformat()


def test_le_second_lecteur_rend_le_meme_verdict_sans_ecrire(tmp_path):
    """Une regle, deux lecteurs : meme verdict, et rien n'est ecrit.

    Le lecteur partage ne recopie pas la regle et ne pose pas de verrou : il
    doit dire BLOCK quand le moteur bloque, et ne rien creer quand il n'y a
    pas de verrou.
    """

    journal = tmp_path / "trades.ndjson"
    verrou = tmp_path / "10055401.json"
    write_trades(journal, [
        trade(-3.0, NOW - timedelta(hours=1), ticket="1", account="10055401"),
    ])

    moteur = persist_live_loss_quarantine(
        evaluate_live_loss_guard(journal, account="10055401", now=NOW),
        path=verrou,
        account="10055401",
        now=NOW,
    )
    assert moteur.action == "BLOCK"

    # La fenetre redevient saine : seul le verrou distingue encore le moteur.
    write_trades(journal, [
        trade(0.5, NOW - timedelta(hours=1), ticket="1", account="10055401"),
    ])
    fenetre = evaluate_live_loss_guard(journal, account="10055401", now=NOW)
    lecteur = read_live_loss_guard(
        journal, account="10055401", quarantine_path=verrou, now=NOW,
    )

    assert fenetre.action == "ALLOW"
    assert lecteur.action == "BLOCK"
    assert lecteur.reason == "PERSISTENT_LOSS_QUARANTINE"
    assert lecteur.quarantine_reason == "DAILY_LOSS_LIMIT"

    absent = tmp_path / "jamais-pose.json"
    sans_verrou = read_live_loss_guard(
        journal, account="10055401", quarantine_path=absent, now=NOW,
    )

    assert sans_verrou.action == "ALLOW"
    assert not absent.exists()


def test_le_chemin_du_verrou_est_nomme_par_le_compte(tmp_path):
    """L'emplacement du verrou a un seul proprietaire, dans le garde-fou."""

    assert live_loss_guard_path(tmp_path, "10055401") == (
        tmp_path / "data" / "runtime" / "live_loss_quarantine" / "10055401.json"
    )
