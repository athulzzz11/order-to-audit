"""The section 5 scenario, end to end, through the service layer."""
from app.benchmark import checks, run_scenario


def test_section5_benchmark_all_checks_pass(db):
    run_scenario(db, log=lambda *_: None)
    results = checks(db)
    failed = [r for r in results if not r["ok"]]
    assert not failed, failed
    assert len(results) == 11
