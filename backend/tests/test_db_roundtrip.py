from contextlib import contextmanager
from types import SimpleNamespace
import logging
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

# The diagnostic script is intentionally outside the installed backend package.
_spec = spec_from_file_location('db_roundtrip', Path(__file__).parents[1] / 'diagnostics' / 'db_roundtrip.py')
db_roundtrip = module_from_spec(_spec)
_spec.loader.exec_module(db_roundtrip)


def fake_pool(fail=False):
    actions = []
    @contextmanager
    def transaction():
        actions.append('begin')
        try:
            yield
        except BaseException:
            actions.append('rollback')
            raise
        else:
            actions.append('commit')
    def execute(statement):
        assert statement in ('SET TRANSACTION READ ONLY', "SET LOCAL statement_timeout = '10s'", 'SELECT 1')
        actions.append(statement)
        if fail and statement == 'SELECT 1': raise RuntimeError('password-secret signed-url personal@example.com')
        return SimpleNamespace(fetchone=lambda: {'?column?': 1})
    connection = SimpleNamespace(transaction=transaction, execute=execute)
    pool = SimpleNamespace(getconn=lambda: connection, putconn=lambda c: actions.append('returned'))
    return pool, actions


def test_baseline_is_read_only_and_separates_first_and_steady_samples():
    pool, actions = fake_pool()
    result = db_roundtrip.probe(pool, 4)
    assert result['select_steady']['count'] == 3
    assert len(result['select_samples_ms']) == 4
    assert result['select_first_ms'] == result['select_samples_ms'][0]
    assert actions.index('SET TRANSACTION READ ONLY') < actions.index('SELECT 1')
    assert actions.count('returned') == 2 and actions.count('commit') == 1


def test_failure_rolls_back_returns_connection_and_never_prints_credentials(monkeypatch, capsys, caplog):
    pool, actions = fake_pool(fail=True)
    monkeypatch.setenv('DATABASE_URL', 'password-secret')
    monkeypatch.setattr(db_roundtrip.online, 'pool', lambda: pool)
    monkeypatch.setattr(db_roundtrip.online, 'close_pool', lambda: actions.append('closed'))
    disabled = logging.root.manager.disable
    assert db_roundtrip.main(['--samples', '2']) == 1
    assert logging.root.manager.disable == disabled
    assert actions[-3:] == ['rollback', 'returned', 'closed']
    output = capsys.readouterr().out + caplog.text
    assert 'baseline failed' in output
    assert 'password-secret' not in output and 'personal@example.com' not in output


def test_summary_reports_tail_and_median():
    result = db_roundtrip.summarize([1] * 18 + [250, 2500])
    assert result['median_ms'] == 1
    assert result['p95_ms'] == 250
    assert result['max_ms'] == 2500
