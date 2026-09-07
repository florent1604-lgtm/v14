"""The emergency test stub must not pretend to implement all of xxHash."""

import importlib.util
from pathlib import Path

import pytest


def test_fallback_is_deterministic_and_explicitly_not_native():
    spec = importlib.util.spec_from_file_location(
        "v14_test_hash_stub", Path(__file__).parent / "compat" / "xxhash.py")
    stub = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stub)
    assert 'substitut' in stub.VERSION
    assert stub.xxh3_128(b'v14').digest() == stub.xxh3_128(b'v14').digest()
    assert len(stub.xxh3_128(b'v14').digest()) == 16
    assert stub.xxh3_128_hexdigest(b'v14') == stub.xxh3_128(b'v14').hexdigest()
    assert stub.xxh3_128_hexdigest(b'v14') != stub.xxh3_128_hexdigest(b'v15')
    for name in ('xxh32', 'xxh64', 'xxh3_64', 'xxh128', 'xxh3_128_digest', 'reset'):
        with pytest.raises(AttributeError, match='DETERMINISTES'):
            getattr(stub, name)
