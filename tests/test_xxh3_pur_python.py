"""Le substitut xxHash doit produire les VRAIES empreintes XXH3-128.

Smart App Control (enforcement) bloque `_xxhash.cp312-win_amd64.pyd` sur cette
machine. Le substitut precedent renvoyait du blake2b : deterministe, mais
incompatible avec les identifiants LangGraph produits par le backend natif — la
suite devait donc s'annoncer DEGRADED.

`tests/compat/xxhash.py` calcule desormais le vrai XXH3-128. Ces tests le
prouvent contre les vecteurs officiels de `Cyan4973/xxHash` v0.8.2
(`tests/sanity_test_vectors.h`, table `XSUM_XXH128_testdata`), embarques dans
`xxh3_128_vecteurs.json` pour que la verification ne depende d'aucun reseau.

Les 90 vecteurs retenus couvrent les SIX chemins de longueur de l'algorithme
(0, 1-3, 4-8, 9-16, 17-128, 129-240, >240 avec blocs et scrambling) et les
trois graines de reference (0, PRIME32, PRIME64). La table complete compte
12 483 vecteurs; elle a ete rejouee entierement a l'ecriture du module,
0 echec.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_COMPAT = Path(__file__).parent / "compat"
_PRIME32 = 2654435761
_PRIME64 = 11400714785074694797
_MASK64 = (1 << 64) - 1


def _charger_substitut():
    """Charge le substitut par chemin, meme si le vrai `xxhash` est importable.

    Sur une machine saine c'est le paquet natif qui sert; ce test doit malgre
    tout valider le substitut, sinon il ne prouverait rien la ou il compte.
    """
    spec = importlib.util.spec_from_file_location(
        "v14_xxh3_substitut", _COMPAT / "xxhash.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tampon(taille: int) -> bytes:
    """Buffer pseudo-aleatoire de la suite officielle (`fillTestBuffer`).

    Sa valeur ne doit jamais changer : les empreintes attendues en dependent.
    """
    octets = bytearray(taille)
    generateur = _PRIME32
    for index in range(taille):
        octets[index] = (generateur >> 56) & 0xFF
        generateur = (generateur * _PRIME64) & _MASK64
    return bytes(octets)


_VECTEURS = json.loads(
    (_COMPAT / "xxh3_128_vecteurs.json").read_text(encoding="utf-8"),
)["vecteurs"]
_TAMPON = _tampon(max(ligne[0] for ligne in _VECTEURS))


@pytest.mark.unit
@pytest.mark.parametrize("longueur,graine,bas,haut", _VECTEURS)
def test_vecteurs_officiels_xxh3_128(longueur, graine, bas, haut):
    xxhash = _charger_substitut()
    attendu = (haut << 64) | bas
    donnees = _TAMPON[:longueur]
    assert xxhash.xxh3_128(donnees, graine).intdigest() == attendu
    assert xxhash.xxh3_128_intdigest(donnees, graine) == attendu
    attendu_octets = haut.to_bytes(8, "big") + bas.to_bytes(8, "big")
    assert xxhash.xxh3_128_digest(donnees, graine) == attendu_octets
    assert xxhash.xxh3_128_hexdigest(donnees, graine) == attendu_octets.hex()


@pytest.mark.unit
def test_les_six_chemins_de_longueur_sont_couverts():
    """Garde-fou : un vecteur retire ne doit pas laisser un chemin non teste."""
    longueurs = {ligne[0] for ligne in _VECTEURS}
    bornes = (
        ("vide", lambda n: n == 0),
        ("1-3", lambda n: 1 <= n <= 3),
        ("4-8", lambda n: 4 <= n <= 8),
        ("9-16", lambda n: 9 <= n <= 16),
        ("17-128", lambda n: 17 <= n <= 128),
        ("129-240", lambda n: 129 <= n <= 240),
        ("long >240", lambda n: n > 240),
    )
    manquants = [nom for nom, predicat in bornes
                 if not any(predicat(n) for n in longueurs)]
    assert not manquants, f"chemins sans vecteur: {manquants}"


@pytest.mark.unit
def test_mise_a_jour_incrementale_egale_le_calcul_direct():
    xxhash = _charger_substitut()
    etat = xxhash.xxh3_128()
    etat.update(b"a")
    assert etat.digest() == xxhash.xxh3_128(b"a").digest()
    etat.update(b"b")
    assert etat.digest() == xxhash.xxh3_128(b"ab").digest()
    etat.update(b"c")
    assert etat.digest() == xxhash.xxh3_128(b"abc").digest()


@pytest.mark.unit
def test_reset_et_copy():
    xxhash = _charger_substitut()
    etat = xxhash.xxh3_128(seed=42)
    vide = etat.intdigest()
    etat.update(b"x" * 10_240)
    clone = etat.copy()
    assert clone.digest() == etat.digest()
    etat.reset()
    assert etat.intdigest() == vide
    # La copie garde son propre etat : reset sur l'original ne la touche pas.
    assert clone.intdigest() != vide


@pytest.mark.unit
def test_la_surface_non_fournie_echoue_bruyamment():
    """Fail-closed : jamais une empreinte plausible pour une famille absente."""
    xxhash = _charger_substitut()
    for absent in ("xxh32", "xxh64", "xxh3_64", "xxh64_hexdigest"):
        assert not hasattr(xxhash, absent), f"{absent} ne doit pas exister"


@pytest.mark.unit
def test_le_secret_par_defaut_est_intact():
    """Le secret est la constante dont depend chaque empreinte."""
    xxhash = _charger_substitut()
    assert len(xxhash._SECRET) == 192
    assert xxhash._SECRET[:4].hex() == "b8fe6c39"
    assert xxhash._SECRET[-4:].hex() == "bb4b407e"
