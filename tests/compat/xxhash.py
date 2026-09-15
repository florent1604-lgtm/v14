"""XXH3-128 exact, en Python pur — pour les machines ou le binaire natif est bloque.

POURQUOI CE MODULE EXISTE
--------------------------
Smart App Control (etat 1, *enforcement*, mesure du 08/09/2026 sur
`HKLM:\\SYSTEM\\CurrentControlSet\\Control\\CI\\Policy`) refuse de charger
`_xxhash.cp312-win_amd64.pyd` : la roue PyPI n'est pas signee Authenticode et sa
prevalence est trop faible pour l'Intelligent Security Graph. Verifie le
08/09 : la version 4.0.1, installee dans un prefixe isole, est bloquee de la
meme facon. Ce n'est donc pas une question de version.

    ImportError: DLL load failed while importing _xxhash:
    Une strategie de controle d'application a bloque ce fichier.

V14 n'importe jamais `xxhash` lui-meme (verifie sur tout `titanium/` et
`tools/`). La dependance est transitive :
`tradingagents -> langchain -> langsmith._internal._uuid`, plus `langgraph`
(identifiants de taches et de checkpoints) et `redis` (optionnel).

CE QUI DISTINGUE CE MODULE DU SUBSTITUT PRECEDENT
--------------------------------------------------
`tests/compat/xxhash.py` renvoyait des empreintes blake2b : deterministes, mais
DIFFERENTES de xxHash. Tout identifiant LangGraph produit ainsi etait
incompatible avec un checkpoint ecrit par le backend natif, et la suite devait
l'annoncer en clair (« DEGRADED »).

Ce module calcule le VRAI XXH3-128. Les empreintes sont identiques, bit pour
bit, a celles de la bibliotheque native. Elles sont verifiees contre les
vecteurs officiels de `Cyan4973/xxHash` v0.8.2 (`tests/sanity_test_vectors.h`)
par `tests/test_xxh3_pur_python.py`, sur les quatre chemins de longueur de
l'algorithme et sur les trois graines de reference.

Consequence : plus de mode degrade, plus d'avertissement, et la compatibilite
des checkpoints est prouvee au lieu d'etre supposee.

PORTEE ET LIMITES, DELIBEREES
------------------------------
Seule la famille XXH3-128 est fournie, parce que c'est la seule que les
dependances installees consomment (`xxh3_128`, `xxh3_128_digest`,
`xxh3_128_hexdigest`, `xxh3_128_intdigest`). XXH32, XXH64 et XXH3-64 levent
`AttributeError` : un futur appelant qui en aurait besoin doit echouer
bruyamment plutot que recevoir en silence une valeur qui ressemble a une
empreinte sans en etre une. C'est le principe fail-closed du depot.

Ce module est LENT (Python pur, ~100x le natif). Il ne sert qu'a des
identifiants courts hors chemin critique. Il n'est jamais charge par la boucle
de trading, qui n'importe pas `langchain`.

CE QU'IL NE FAUT PAS EN CONCLURE
---------------------------------
Ce module traite un symptome precis. La cause reste structurelle : les
extensions natives du venv (numpy, pandas, scipy, sklearn) ne passent
aujourd'hui que par reputation ISG, et rien ne garantit qu'elles la
conserveront. Le correctif de fond reste d'executer la suite hors du perimetre
Smart App Control, ou de signer les binaires. Aucune securite Windows n'a ete
desactivee ni contournee ici : on remplace un binaire refuse par du code
Python, on ne force pas son chargement.
"""

from __future__ import annotations

import struct

VERSION = "0.8.2-xxh3-python-v14"
XXHASH_VERSION = VERSION

_MASK64 = 0xFFFFFFFFFFFFFFFF
_MASK32 = 0xFFFFFFFF

_PRIME32_1 = 0x9E3779B1
_PRIME32_2 = 0x85EBCA77
_PRIME32_3 = 0xC2B2AE3D
_PRIME64_1 = 0x9E3779B185EBCA87
_PRIME64_2 = 0xC2B2AE3D27D4EB4F
_PRIME64_3 = 0x165667B19E3779F9
_PRIME64_4 = 0x85EBCA77C2B2AE63
_PRIME64_5 = 0x27D4EB2F165667C5
_PRIME_MX1 = 0x165667919E3779F9
_PRIME_MX2 = 0x9FB21C651E98DF25

_SECRET_DEFAULT_SIZE = 192
_SECRET_SIZE_MIN = 136
_STRIPE_LEN = 64
_ACC_NB = 8
_SECRET_CONSUME_RATE = 8
_SECRET_MERGEACCS_START = 11
_SECRET_LASTACC_START = 7
_MIDSIZE_MAX = 240
_MIDSIZE_STARTOFFSET = 3
_MIDSIZE_LASTOFFSET = 17

#: Secret par defaut de la specification xxHash, octet pour octet.
_SECRET = bytes.fromhex(
    "b8fe6c3923a44bbe7c01812cf721ad1cded46de9839097db7240a4a4b7b3671f"
    "cb79e64eccc0e578825ad07dccff7221b8084674f743248ee03590e6813a264c"
    "3c2852bb91c300cb88d0658b1b532ea371644897a20df94e3819ef46a9deacd8"
    "a8fa763fe39c343ff9dcbbc7c70b4f1d8a51e04bcdb45931c89f7ec9d9787364"
    "eac5ac8334d3ebc3c581a0fffa1363eb170ddd51b7f0da49d316552629d4689e"
    "2b16be587d47a1fc8ff8b8d17ad031ce45cb3a8f95160428afd7fbcabb4b407e"
)

_INIT_ACC = (
    _PRIME32_3, _PRIME64_1, _PRIME64_2, _PRIME64_3,
    _PRIME64_4, _PRIME32_2, _PRIME64_5, _PRIME32_1,
)


def _r64(data: bytes, offset: int) -> int:
    return struct.unpack_from("<Q", data, offset)[0]


def _r32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _swap64(value: int) -> int:
    return int.from_bytes((value & _MASK64).to_bytes(8, "little"), "big")


def _swap32(value: int) -> int:
    return int.from_bytes((value & _MASK32).to_bytes(4, "little"), "big")


def _rotl32(value: int, bits: int) -> int:
    value &= _MASK32
    return ((value << bits) | (value >> (32 - bits))) & _MASK32


def _mul128_fold64(lhs: int, rhs: int) -> int:
    product = (lhs & _MASK64) * (rhs & _MASK64)
    return (product & _MASK64) ^ ((product >> 64) & _MASK64)


def _xorshift64(value: int, shift: int) -> int:
    value &= _MASK64
    return value ^ (value >> shift)


def _avalanche(value: int) -> int:
    """XXH3_avalanche — melange rapide, pour des bits deja partiellement mixes."""
    value = _xorshift64(value, 37)
    value = (value * _PRIME_MX1) & _MASK64
    return _xorshift64(value, 32)


def _avalanche64(value: int) -> int:
    """XXH64_avalanche — melange fort, pour des bits NON prealablement mixes.

    A ne pas confondre avec `_avalanche` : XXH3 emploie les deux, a des
    endroits differents (par ex. la longueur nulle et la queue 4-8 octets
    utilisent celle-ci). Les confondre donne une empreinte plausible mais
    fausse — defaut constate et corrige a l'ecriture de ce module, attrape par
    les vecteurs officiels.
    """
    value &= _MASK64
    value ^= value >> 33
    value = (value * _PRIME64_2) & _MASK64
    value ^= value >> 29
    value = (value * _PRIME64_3) & _MASK64
    return value ^ (value >> 32)


def _mix16(data: bytes, offset: int, secret: bytes, secret_offset: int,
           seed: int) -> int:
    low = _r64(data, offset)
    high = _r64(data, offset + 8)
    return _mul128_fold64(
        low ^ ((_r64(secret, secret_offset) + seed) & _MASK64),
        high ^ ((_r64(secret, secret_offset + 8) - seed) & _MASK64),
    )


def _mix32(acc_low: int, acc_high: int, data: bytes, offset_a: int,
           offset_b: int, secret: bytes, secret_offset: int,
           seed: int) -> tuple[int, int]:
    acc_low = (acc_low + _mix16(data, offset_a, secret, secret_offset, seed)) & _MASK64
    acc_low ^= (_r64(data, offset_b) + _r64(data, offset_b + 8)) & _MASK64
    acc_high = (acc_high + _mix16(data, offset_b, secret, secret_offset + 16, seed)) & _MASK64
    acc_high ^= (_r64(data, offset_a) + _r64(data, offset_a + 8)) & _MASK64
    return acc_low, acc_high


def _len_1to3(data: bytes, secret: bytes, seed: int) -> tuple[int, int]:
    length = len(data)
    c1, c2, c3 = data[0], data[length >> 1], data[length - 1]
    combined_low = ((c1 << 16) | (c2 << 24) | c3 | (length << 8)) & _MASK32
    combined_high = _rotl32(_swap32(combined_low), 13)
    flip_low = ((_r32(secret, 0) ^ _r32(secret, 4)) + seed) & _MASK64
    flip_high = ((_r32(secret, 8) ^ _r32(secret, 12)) - seed) & _MASK64
    return (_avalanche64(combined_low ^ flip_low),
            _avalanche64(combined_high ^ flip_high))


def _len_4to8(data: bytes, secret: bytes, seed: int) -> tuple[int, int]:
    length = len(data)
    seed ^= (_swap32(seed & _MASK32) << 32) & _MASK64
    input_64 = (_r32(data, 0) + (_r32(data, length - 4) << 32)) & _MASK64
    flip = ((_r64(secret, 16) ^ _r64(secret, 24)) + seed) & _MASK64
    keyed = input_64 ^ flip
    product = keyed * ((_PRIME64_1 + (length << 2)) & _MASK64)
    low = product & _MASK64
    high = (product >> 64) & _MASK64
    high = (high + (low << 1)) & _MASK64
    low ^= high >> 3
    low = _xorshift64(low, 35)
    low = (low * _PRIME_MX2) & _MASK64
    low = _xorshift64(low, 28)
    return low, _avalanche(high)


def _len_9to16(data: bytes, secret: bytes, seed: int) -> tuple[int, int]:
    length = len(data)
    flip_low = ((_r64(secret, 32) ^ _r64(secret, 40)) - seed) & _MASK64
    flip_high = ((_r64(secret, 48) ^ _r64(secret, 56)) + seed) & _MASK64
    input_low = _r64(data, 0)
    input_high = _r64(data, length - 8)
    product = (input_low ^ input_high ^ flip_low) * _PRIME64_1
    m_low = product & _MASK64
    m_high = (product >> 64) & _MASK64
    m_low = (m_low + ((length - 1) << 54)) & _MASK64
    input_high ^= flip_high
    m_high = (m_high + input_high
              + ((input_high & _MASK32) * ((_PRIME32_2 - 1) & _MASK64))) & _MASK64
    m_low ^= _swap64(m_high)
    result = m_low * _PRIME64_2
    out_low = result & _MASK64
    out_high = (((result >> 64) & _MASK64) + (m_high * _PRIME64_2)) & _MASK64
    return _avalanche(out_low), _avalanche(out_high)


def _len_0to16(data: bytes, secret: bytes, seed: int) -> tuple[int, int]:
    length = len(data)
    if length > 8:
        return _len_9to16(data, secret, seed)
    if length >= 4:
        return _len_4to8(data, secret, seed)
    if length:
        return _len_1to3(data, secret, seed)
    low = _avalanche64((seed ^ _r64(secret, 64) ^ _r64(secret, 72)) & _MASK64)
    high = _avalanche64((seed ^ _r64(secret, 80) ^ _r64(secret, 88)) & _MASK64)
    return low, high


def _len_17to128(data: bytes, secret: bytes, seed: int) -> tuple[int, int]:
    length = len(data)
    acc_low = (length * _PRIME64_1) & _MASK64
    acc_high = 0
    if length > 32:
        if length > 64:
            if length > 96:
                acc_low, acc_high = _mix32(acc_low, acc_high, data, 48,
                                           length - 64, secret, 96, seed)
            acc_low, acc_high = _mix32(acc_low, acc_high, data, 32,
                                       length - 48, secret, 64, seed)
        acc_low, acc_high = _mix32(acc_low, acc_high, data, 16,
                                   length - 32, secret, 32, seed)
    acc_low, acc_high = _mix32(acc_low, acc_high, data, 0,
                               length - 16, secret, 0, seed)
    low = (acc_low + acc_high) & _MASK64
    high = ((acc_low * _PRIME64_1) + (acc_high * _PRIME64_4)
            + ((length - seed) * _PRIME64_2)) & _MASK64
    return _avalanche(low), (-_avalanche(high)) & _MASK64


def _len_129to240(data: bytes, secret: bytes, seed: int) -> tuple[int, int]:
    length = len(data)
    acc_low = (length * _PRIME64_1) & _MASK64
    acc_high = 0
    for i in range(4):
        acc_low, acc_high = _mix32(acc_low, acc_high, data, 32 * i,
                                   32 * i + 16, secret, 32 * i, seed)
    acc_low = _avalanche(acc_low)
    acc_high = _avalanche(acc_high)
    for i in range(4, length // 32):
        acc_low, acc_high = _mix32(
            acc_low, acc_high, data, 32 * i, 32 * i + 16, secret,
            _MIDSIZE_STARTOFFSET + 32 * (i - 4), seed,
        )
    acc_low, acc_high = _mix32(
        acc_low, acc_high, data, length - 16, length - 32, secret,
        _SECRET_SIZE_MIN - _MIDSIZE_LASTOFFSET - 16, (-seed) & _MASK64,
    )
    low = (acc_low + acc_high) & _MASK64
    high = ((acc_low * _PRIME64_1) + (acc_high * _PRIME64_4)
            + ((length - seed) * _PRIME64_2)) & _MASK64
    return _avalanche(low), (-_avalanche(high)) & _MASK64


def _accumulate_512(acc: list[int], data: bytes, offset: int,
                    secret: bytes, secret_offset: int) -> None:
    for i in range(_ACC_NB):
        data_val = _r64(data, offset + 8 * i)
        key_val = data_val ^ _r64(secret, secret_offset + 8 * i)
        acc[i ^ 1] = (acc[i ^ 1] + data_val) & _MASK64
        acc[i] = (acc[i] + (key_val & _MASK32) * (key_val >> 32)) & _MASK64


def _scramble_512(acc: list[int], secret: bytes, secret_offset: int) -> None:
    for i in range(_ACC_NB):
        value = _xorshift64(acc[i], 47)
        value ^= _r64(secret, secret_offset + 8 * i)
        acc[i] = (value * _PRIME32_1) & _MASK64


def _hash_long_internal(data: bytes, secret: bytes) -> list[int]:
    acc = list(_INIT_ACC)
    length = len(data)
    stripes_per_block = (len(secret) - _STRIPE_LEN) // _SECRET_CONSUME_RATE
    block_len = _STRIPE_LEN * stripes_per_block
    nb_blocks = (length - 1) // block_len
    for block in range(nb_blocks):
        for stripe in range(stripes_per_block):
            _accumulate_512(acc, data, block * block_len + stripe * _STRIPE_LEN,
                            secret, stripe * _SECRET_CONSUME_RATE)
        _scramble_512(acc, secret, len(secret) - _STRIPE_LEN)
    nb_stripes = ((length - 1) - block_len * nb_blocks) // _STRIPE_LEN
    for stripe in range(nb_stripes):
        _accumulate_512(acc, data, nb_blocks * block_len + stripe * _STRIPE_LEN,
                        secret, stripe * _SECRET_CONSUME_RATE)
    _accumulate_512(acc, data, length - _STRIPE_LEN, secret,
                    len(secret) - _STRIPE_LEN - _SECRET_LASTACC_START)
    return acc


def _mix2accs(acc: list[int], index: int, secret: bytes, offset: int) -> int:
    return _mul128_fold64(acc[index] ^ _r64(secret, offset),
                          acc[index + 1] ^ _r64(secret, offset + 8))


def _merge_accs(acc: list[int], secret: bytes, offset: int, start: int) -> int:
    result = start & _MASK64
    for i in range(4):
        result = (result + _mix2accs(acc, 2 * i, secret, offset + 16 * i)) & _MASK64
    return _avalanche(result)


def _hash_long_128(data: bytes, secret: bytes) -> tuple[int, int]:
    acc = _hash_long_internal(data, secret)
    length = len(data)
    low = _merge_accs(acc, secret, _SECRET_MERGEACCS_START,
                      (length * _PRIME64_1) & _MASK64)
    high = _merge_accs(
        acc, secret, len(secret) - 64 - _SECRET_MERGEACCS_START,
        (~(length * _PRIME64_2)) & _MASK64,
    )
    return low, high


def _custom_secret(seed: int) -> bytes:
    if seed == 0:
        return _SECRET
    out = bytearray(_SECRET_DEFAULT_SIZE)
    for i in range(_SECRET_DEFAULT_SIZE // 16):
        low = (_r64(_SECRET, 16 * i) + seed) & _MASK64
        high = (_r64(_SECRET, 16 * i + 8) - seed) & _MASK64
        struct.pack_into("<Q", out, 16 * i, low)
        struct.pack_into("<Q", out, 16 * i + 8, high)
    return bytes(out)


def _xxh3_128(data: bytes, seed: int = 0) -> tuple[int, int]:
    """Renvoie (low64, high64) — exactement XXH3_128bits_withSeed."""
    seed &= _MASK64
    length = len(data)
    if length <= 16:
        return _len_0to16(data, _SECRET, seed)
    if length <= 128:
        return _len_17to128(data, _SECRET, seed)
    if length <= _MIDSIZE_MAX:
        return _len_129to240(data, _SECRET, seed)
    return _hash_long_128(data, _custom_secret(seed))


class xxh3_128:  # noqa: N801 - nom impose par l'API de `xxhash`
    """Etat incremental compatible avec `xxhash.xxh3_128`.

    L'accumulation reelle est differee : les blocs sont concatenes puis hashes
    au moment du digest. C'est correct — XXH3 est une fonction pure de
    (donnees, graine) — et suffisant pour les identifiants courts qui sont le
    seul usage ici. Une version en flux serait plus economique en memoire et
    n'apporterait rien d'autre.
    """

    __slots__ = ("_buffer", "_seed")

    digest_size = 16
    block_size = 64
    name = "xxh3_128"

    def __init__(self, data: bytes = b"", seed: int = 0) -> None:
        self._seed = int(seed) & _MASK64
        self._buffer = bytearray()
        if data:
            self.update(data)

    def update(self, data: bytes) -> None:
        self._buffer.extend(data)

    def digest(self) -> bytes:
        low, high = _xxh3_128(bytes(self._buffer), self._seed)
        return high.to_bytes(8, "big") + low.to_bytes(8, "big")

    def hexdigest(self) -> str:
        return self.digest().hex()

    def intdigest(self) -> int:
        low, high = _xxh3_128(bytes(self._buffer), self._seed)
        return (high << 64) | low

    def reset(self) -> None:
        self._buffer = bytearray()

    def copy(self) -> xxh3_128:
        clone = xxh3_128(seed=self._seed)
        clone._buffer = bytearray(self._buffer)
        return clone

    @property
    def seed(self) -> int:
        return self._seed


def xxh3_128_digest(data: bytes, seed: int = 0) -> bytes:
    return xxh3_128(data, seed).digest()


def xxh3_128_hexdigest(data: bytes, seed: int = 0) -> str:
    return xxh3_128(data, seed).hexdigest()


def xxh3_128_intdigest(data: bytes, seed: int = 0) -> int:
    return xxh3_128(data, seed).intdigest()
