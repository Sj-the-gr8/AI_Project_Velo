import pytest
from google.auth.exceptions import TransportError

from scripts.watch_inbox import watch


def test_checks_repeatedly_and_sleeps_between_checks():
    calls, sleeps = [], []
    watch(60, max_checks=3, run=lambda quiet: calls.append(quiet) or [], sleep=sleeps.append)
    assert calls == [True, True, True]
    assert sleeps == [60, 60]


def test_transient_error_is_retried_on_next_check():
    outcomes = iter([TransportError("network down"), ConnectionError("reset"), None])
    calls = []

    def run(quiet):
        calls.append(quiet)
        outcome = next(outcomes)
        if outcome:
            raise outcome
        return []

    watch(1, max_checks=3, run=run, sleep=lambda _: None)
    assert len(calls) == 3


def test_unexpected_error_stops_the_watcher():
    def run(quiet):
        raise RuntimeError("token revoked")

    with pytest.raises(RuntimeError):
        watch(1, max_checks=3, run=run, sleep=lambda _: None)


def test_warns_once_when_the_code_changes_while_running(caplog):
    import logging
    versions = iter([100.0, 100.0, 200.0, 200.0])
    with caplog.at_level(logging.WARNING):
        watch(1, max_checks=3, run=lambda quiet: [], sleep=lambda _: None, version=lambda: next(versions))
    warnings = [record for record in caplog.records if "code changed" in record.getMessage()]
    assert len(warnings) == 1


def test_code_version_tracks_velos_own_files(tmp_path):
    import os
    from scripts.watch_inbox import code_version
    (tmp_path / "notify").mkdir()
    (tmp_path / "config.py").write_text("x = 1")
    notifier = tmp_path / "notify" / "notifier.py"
    notifier.write_text("y = 1")
    os.utime(tmp_path / "config.py", (1000, 1000))
    os.utime(notifier, (2000, 2000))
    assert code_version(tmp_path) == 2000
