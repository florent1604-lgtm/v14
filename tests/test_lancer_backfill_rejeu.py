"""Le lanceur de backfill ne doit ni ecraser un echec ni mal decouper les lots."""
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from tools import lancer_backfill_rejeu as lbr  # noqa: E402


class _FauxProcessus:
    def __init__(self, pid):
        self.pid = pid


class TestLanceurBackfill(unittest.TestCase):
    def setUp(self):
        self.appels = []

    def _popen(self, cmd, **kw):
        self.appels.append((list(cmd), kw))
        return _FauxProcessus(1000 + len(self.appels))

    def test_un_processus_par_lot_avec_sa_part(self):
        with mock.patch.object(lbr.subprocess, "Popen", self._popen), \
                mock.patch.object(lbr, "LOGS_DEFAUT", Path(".")):
            import tempfile
            with tempfile.TemporaryDirectory() as bac:
                pids = lbr.lancer(
                    3, ltf="M15", htf="H4", pas=1, barres=0,
                    prefixe="test", logs=Path(bac), symboles=None, refaire=False)
        self.assertEqual(len(pids), 3)
        self.assertEqual(len(set(pids)), 3)
        parts = []
        for cmd, _ in self.appels:
            self.assertIn("--sur", cmd)
            self.assertEqual(cmd[cmd.index("--sur") + 1], "3")
            parts.append(cmd[cmd.index("--part") + 1])
        self.assertEqual(sorted(parts), ["0", "1", "2"])

    def test_refaire_et_symboles_sont_transmis(self):
        import tempfile
        with mock.patch.object(lbr.subprocess, "Popen", self._popen), \
                tempfile.TemporaryDirectory() as bac:
            lbr.lancer(1, ltf="M15", htf="H4", pas=2, barres=500,
                       prefixe="test", logs=Path(bac),
                       symboles=["BTCUSD", "ETHUSD"], refaire=True)
        cmd = self.appels[0][0]
        self.assertIn("--refaire", cmd)
        self.assertEqual(cmd[cmd.index("--symboles") + 1:], ["BTCUSD", "ETHUSD"])
        self.assertEqual(cmd[cmd.index("--barres") + 1], "500")

    def test_sentinelle_bloque_le_lancement(self):
        import tempfile
        with tempfile.TemporaryDirectory() as bac:
            sentinelle = Path(bac) / "_RUN_FAILED.json"
            sentinelle.write_text('{"symbol": "AAVE-USD"}', encoding="utf-8")
            lance = []
            with mock.patch.object(lbr, "SENTINELLE", sentinelle), \
                    mock.patch.object(lbr, "lancer", lambda *a, **k: lance.append(1)), \
                    mock.patch.object(sys, "argv", ["lancer_backfill_rejeu.py"]):
                code = lbr.main()
            self.assertEqual(code, 2)
            self.assertEqual(lance, [])
            self.assertTrue(sentinelle.is_file())

    def test_effacer_sentinelle_est_explicite(self):
        import tempfile
        with tempfile.TemporaryDirectory() as bac:
            sentinelle = Path(bac) / "_RUN_FAILED.json"
            sentinelle.write_text("{}", encoding="utf-8")
            lance = []
            with mock.patch.object(lbr, "SENTINELLE", sentinelle), \
                    mock.patch.object(lbr, "lancer",
                                      lambda *a, **k: lance.append(1)), \
                    mock.patch.object(sys, "argv",
                                      ["x", "--effacer-sentinelle", "--lots", "2"]):
                code = lbr.main()
            self.assertEqual(code, 0)
            self.assertEqual(lance, [1])
            self.assertFalse(sentinelle.exists())


if __name__ == "__main__":
    unittest.main()
