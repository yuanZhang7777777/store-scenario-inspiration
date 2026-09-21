"""Actual persistent gate tests. No model/network calls or live repo assertions."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
try:
    from store_scenario_inspiration.app import confirmation as gate
except ModuleNotFoundError as error:
    if error.name not in {'store_scenario_inspiration', 'store_scenario_inspiration.app'}:
        raise
    spec = importlib.util.spec_from_file_location('review_gate_under_test', ROOT / 'confirmation/confirmation.py')
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)

ORDER = (*gate.INITIAL_STAGES, *gate.ANALYSIS_STAGES)

def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

@pytest.fixture
def base(tmp_path):
    for name in gate.SOURCE_FILES:
        write(tmp_path / name, {})
    write(tmp_path / 'store.json', {'store_name': 'test-store', 'country': 'PH'})
    write(tmp_path / 'sample_store.json', {'observed_product_clues': [{'clue': '折叠椅'}], 'business_context': None})
    write(tmp_path / 'clues.json', {'entries': [{'clue':'折叠椅', 'excluded':False}]})
    write(tmp_path / 'analysis_input.json', {'observed_product_clues': [{'clue': '折叠椅'}]})
    return tmp_path


def approve(base):
    gate.approve_review(base, gate.review_version(base))


def test_missing_facts_not_ready(tmp_path):
    assert gate.review_status(tmp_path)['state'] == 'not_ready'
    with pytest.raises(gate.ConfirmationRequired): gate.require_confirmation(tmp_path)


def test_recognition_completes_but_analysis_waits(base):
    assert gate.review_status(base)['state'] == 'awaiting_confirmation'
    assert gate.review_details(base)['products'][0]['clue'] == '折叠椅'
    with pytest.raises(gate.ConfirmationRequired): gate.authorize_start(base, gate.ANALYSIS_STAGES)


def test_no_sales_data_is_allowed(base):
    approve(base)
    assert gate.require_confirmation(base) == gate.review_version(base)
    assert gate.review_details(base)['business_context'] is None


def test_refresh_does_not_confirm_or_run(base):
    for _ in range(5): gate.review_details(base)
    assert not (base / gate.CONFIRMATION_FILE).exists()
    assert not (base / 'deepseek_scenes.json').exists()


def test_confirmation_persists_on_disk(base):
    approve(base)
    record = json.loads((base / gate.CONFIRMATION_FILE).read_text())
    assert record['schema'] == gate.SCHEMA
    assert gate.review_status(Path(str(base)))['confirmed']

@pytest.mark.parametrize('filename', gate.SOURCE_FILES)
def test_any_changed_fact_invalidates_confirmation(base, filename):
    approve(base)
    write(base / filename, {'changed': True})
    assert not gate.review_status(base)['confirmed']
    with pytest.raises(gate.ConfirmationRequired): gate.require_confirmation(base)


def test_equivalent_json_order_does_not_invalidate(base):
    approve(base)
    write(base / 'store.json', {'country': 'PH', 'store_name': 'test-store'})
    assert gate.review_status(base)['confirmed']

@pytest.mark.parametrize('content', ['{}', 'not json', '[]', 'null', '{"schema":"other","confirmed":true}'])
def test_corrupt_or_incomplete_approval_never_allows_analysis(base, content):
    (base / gate.CONFIRMATION_FILE).write_text(content)
    with pytest.raises(gate.ConfirmationRequired): gate.require_confirmation(base)


def test_all_products_excluded_requires_at_least_one(base):
    write(base / 'analysis_input.json', {'observed_product_clues': []})
    with pytest.raises(gate.ConfirmationRequired, match='至少'): approve(base)


def test_stale_review_tab_must_refresh(base):
    version = gate.review_version(base)
    write(base / 'store.json', {'store_name': 'changed'})
    with pytest.raises(gate.ConfirmationRequired, match='变化'): gate.approve_review(base, version)

@pytest.mark.parametrize('value', [None, ()])
def test_no_stage_argument_is_initial_only(value):
    assert gate.choose_stages(value, ORDER) == ('recognize','clues')

@pytest.mark.parametrize('stage', gate.ANALYSIS_STAGES)
def test_mixing_recognition_and_downstream_is_refused(stage):
    with pytest.raises(gate.ConfirmationRequired): gate.choose_stages(('recognize', stage), ORDER)

@pytest.mark.parametrize('stage', gate.ANALYSIS_STAGES)
def test_any_direct_downstream_request_needs_confirmation(base, stage):
    with pytest.raises(gate.ConfirmationRequired): gate.authorize_start(base, (stage,))


def test_confirmation_allows_existing_downstream_without_policy_change(base):
    approve(base)
    chosen = gate.choose_stages(gate.ANALYSIS_STAGES, ORDER)
    assert chosen == gate.ANALYSIS_STAGES
    assert gate.authorize_start(base, chosen) == gate.review_version(base)


def test_rerecognizing_invalidates_even_if_source_would_be_identical(base):
    approve(base)
    assert gate.authorize_start(base, gate.INITIAL_STAGES) is None
    assert not gate.review_status(base)['confirmed']


def test_duplicate_approval_is_idempotent(base):
    approve(base)
    old = (base / gate.CONFIRMATION_FILE).read_bytes()
    approve(base)
    assert old == (base / gate.CONFIRMATION_FILE).read_bytes()


def test_old_job_cannot_continue_after_source_changed_and_reconfirmed(base):
    approve(base)
    version = gate.require_confirmation(base)
    write(base / 'store.json', {'store_name': 'changed'})
    approve(base)
    with pytest.raises(gate.ConfirmationRequired): gate.require_confirmation(base, version)


def test_unknown_stage_rejected():
    with pytest.raises(ValueError): gate.choose_stages(('magic',), ORDER)


def test_nan_data_not_marked_as_confirmed(base):
    (base / 'store.json').write_text('{"ado":NaN}')
    assert not gate.review_status(base)['confirmed']


def test_incomplete_facts_do_not_reuse_old_approval(base):
    approve(base)
    (base / 'clues.json').unlink()
    assert gate.review_status(base)['state'] == 'not_ready'
