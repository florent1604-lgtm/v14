"""An API outage is not a cognitive verdict; retries remain strictly bounded."""

import json
import time
from datetime import datetime, timezone

import pytest

from titanium.avis import Demande, demandes_en_attente, deposer


def setup_queue(tmp_path, age=601., count=1, **overrides):
    requests, replies = tmp_path / 'requests.ndjson', tmp_path / 'replies.ndjson'
    request = Demande(symbol='EURUSD', side=1, bar_time='2026-09-07T14:00:00Z')
    deposer(request, requests)
    row = {'decision_ref': request.sceller().decision_ref, 'symbol': 'EURUSD',
           'bar_time': request.bar_time, 'source': 'hermes-unavailable',
           'action': 'WAIT', 'model_version': 'none',
           'rendu_a': datetime.fromtimestamp(time.time()-age, timezone.utc).isoformat(),
           **overrides}
    replies.write_text((json.dumps(row)+'\n')*count, encoding='utf-8')
    return requests, replies


def test_outage_retry_does_not_modify_or_purge_history(tmp_path):
    request, reply = setup_queue(tmp_path)
    before = (request.read_bytes(), reply.read_bytes())
    assert len(demandes_en_attente(request, reply)) == 1
    assert before == (request.read_bytes(), reply.read_bytes())


@pytest.mark.parametrize('age,count', [(0., 1), (599., 1), (1000., 2), (-1., 1)])
def test_cooldown_budget_and_clock_rollback_are_fail_closed(tmp_path, age, count):
    assert demandes_en_attente(*setup_queue(tmp_path, age=age, count=count)) == []


@pytest.mark.parametrize('action', ['ALLOW', 'WAIT', 'BLOCK'])
def test_real_verdict_is_not_retried(tmp_path, action):
    assert demandes_en_attente(*setup_queue(tmp_path, action=action,
        source='hermes-cortex/claude-opus-5', model_version='hermes:claude-opus-5')) == []


@pytest.mark.parametrize('stamp', ['', 'broken', '2026-09-07T14:00:00'])
def test_unverifiable_time_cannot_reset_cooldown(tmp_path, stamp):
    assert demandes_en_attente(*setup_queue(tmp_path, rendu_a=stamp)) == []


def test_later_failure_cannot_reopen_completed_verdict(tmp_path):
    request, reply = setup_queue(tmp_path)
    row = json.loads(reply.read_text())
    final = {**row, 'source': 'hermes-cortex/claude-opus-5', 'action': 'BLOCK',
             'model_version': 'hermes:claude-opus-5'}
    reply.write_text(json.dumps(final)+'\n'+json.dumps(row)+'\n', encoding='utf-8')
    assert demandes_en_attente(request, reply) == []
