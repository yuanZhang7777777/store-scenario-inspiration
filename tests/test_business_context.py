"""New business-fact validation tests. No real vision/network assertions."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import sys
import pytest

try:
    from store_scenario_inspiration.pipeline import business
except ModuleNotFoundError:
    module_path = Path(__file__).resolve().parents[1] / 'payload/backend/business.py'
    if not module_path.is_file():
        raise
    spec = importlib.util.spec_from_file_location('business_under_test', module_path)
    business = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = business
    spec.loader.exec_module(business)


def row(**kwargs):
    return {'product_name': '电视底座', 'orders': 25, 'units_sold': 30,
            'gmv': 300.25, 'currency': 'PHP', 'period_text': '近30天',
            'raw_text': '订单数 25，销量 30，成交额 ₱300.25，近30天', **kwargs}


def test_missing_values_do_not_become_zero():
    result = business.normalize_metrics({})
    assert result == dict(ado=None, adg=None, currency=None, period_start=None, period_end=None)


def test_zero_is_preserved():
    result = business.normalize_metrics({'ado': 0, 'adg': 0})
    assert result['ado'] == 0 and result['adg'] == 0


@pytest.mark.parametrize('value', [True, False, '12', '1,234', -1, float('nan'), float('inf'), [], {}])
def test_invalid_metrics_rejected(value):
    with pytest.raises(ValueError):
        business.normalize_metrics({'ado': value})


@pytest.mark.parametrize('value', [0, 0.5, 125, 253.25])
def test_valid_daily_values(value):
    assert business.normalize_metrics({'ado': value})['ado'] == value


def test_currency_and_period():
    assert business.normalize_metrics({'currency': 'php', 'period_start': '2026-09-01', 'period_end': '2026-09-21'})['currency'] == 'PHP'


@pytest.mark.parametrize('values', [
    {'period_start': '2026-09-01'}, {'period_end': '2026-09-21'},
    {'period_start': '2026-09-22', 'period_end': '2026-09-21'},
    {'period_start': '2026-02-30', 'period_end': '2026-03-02'},
    {'period_start': '20260901', 'period_end': '20260921'},
    {'currency': '₱'}, {'currency': 'P1P'}, {'unknown': 1},
])
def test_bad_metadata_rejected(values):
    with pytest.raises(ValueError):
        business.normalize_metrics(values)


def test_valid_sales_preserve_metric_meanings():
    rows, issues = business.read_sales_rows([row()], 'sales.png')
    assert not issues
    assert rows[0]['orders'] == 25
    assert rows[0]['units_sold'] == 30
    assert rows[0]['source_image'] == 'sales.png'
    assert rows[0]['gmv'] == 300.25


def test_unknown_order_is_not_filled_from_units():
    rows, _ = business.read_sales_rows([row(orders=None)], 'sales.png')
    assert rows[0]['orders'] is None and rows[0]['units_sold'] == 30


def test_ambiguous_numeric_strings_are_not_guessed():
    rows, issues = business.read_sales_rows([row(gmv='3.000,50')], 'sales.png')
    assert rows[0]['gmv'] is None and issues


def test_invalid_optional_metric_preserves_valid_metrics():
    rows, issues = business.read_sales_rows([row(orders=True, units_sold=42)], 'sales.png')
    assert rows[0]['orders'] is None and rows[0]['units_sold'] == 42 and issues


def test_order_count_must_be_integer():
    rows, issues = business.read_sales_rows([row(orders=0.5)], 'sales.png')
    assert rows[0]['orders'] is None and issues


def test_unreadable_record_does_not_destroy_other_records():
    rows, issues = business.read_sales_rows([row(raw_text=None), row()], 'sales.png')
    assert len(rows) == 1 and issues


def test_no_numeric_data_is_not_counted_as_sales_evidence():
    rows, _ = business.read_sales_rows([row(orders=None, units_sold=None, gmv=None)], 'sales.png')
    assert rows == []


def test_approximate_values_stay_approximate():
    rows, _ = business.read_sales_rows([row(units_sold=2000, approximate=True, raw_text='2mil+ Sold/Month')], 'sales.png')
    assert rows[0]['approximate'] and rows[0]['raw_text'] == '2mil+ Sold/Month'


def test_false_approximate_string_is_not_truthy_converted():
    rows, issues = business.read_sales_rows([row(approximate='false')], 'sales.png')
    assert rows == [] and issues


def test_different_screenshots_not_merged_or_summed():
    context = business.build_business_context({}, {'images': [
        {'filename': 'a.png', 'sales_rows': [row()]},
        {'filename': 'b.png', 'sales_rows': [row()]},
    ]})
    assert len(context['sales_rows']) == 2
    assert context['store_metrics']['ado'] is None
    assert 'total_orders' not in context


def test_no_reporting_period_fabricated_from_manual_period():
    context = business.build_business_context({'business_metrics': {'period_start': '2026-09-01', 'period_end': '2026-09-21'}},
        {'images': [{'filename': 'x.png', 'sales_rows': [row(period_text=None)]}]})
    assert context['sales_rows'][0]['period_text'] is None


def test_same_name_different_ids_not_merged():
    rows, _ = business.read_sales_rows([row(product_id='1'), row(product_id='2')], 'sales.png')
    assert len(rows) == 2


def test_no_data_status_is_explicit():
    context = business.build_business_context({'business_metrics': business.normalize_metrics({})}, {'images': []})
    assert context['metric_source'] == 'not_provided'


def test_manual_metrics_passthrough():
    context = business.build_business_context({'business_metrics': {'ado': 12.5, 'adg': 1234, 'currency': 'PHP'}}, {'images': []})
    assert context['store_metrics']['ado'] == 12.5
    assert context['metric_source'] == 'operator_input'


def test_invalid_historical_metrics_warn_without_inventing_numbers():
    context = business.build_business_context({'business_metrics': {'ado': '1,200'}}, {'images': []})
    assert context['store_metrics']['ado'] is None and context['warnings']


def test_excluded_product_cannot_become_sales_priority():
    context = business.build_business_context({}, {'images': [{'filename': 'a.png', 'sales_rows': [row()]}]})
    filtered = business.business_for_analysis(context, ['电视底座'])
    assert not filtered['sales_rows']
    assert len(context['sales_rows']) == 1  # retain the unmodified raw record


def test_malformed_optional_sales_rows_only_warn():
    assert business.read_sales_rows('not a table', 'x.png')[0] == []
    assert business.read_sales_rows('not a table', 'x.png')[1]


def test_bounded_rows_are_not_silent_full_coverage():
    rows, issues = business.read_sales_rows([row()] * 101, 'x.png')
    assert len(rows) == 100 and issues


def test_unknown_context_schema_not_used():
    assert business.business_for_analysis({'schema': 'unknown'}, []) == {}
