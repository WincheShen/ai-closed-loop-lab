"""新浪分页失败时，已有足够真票不得整单作废。"""
from src.stock_analyzer.data_source.akshare_client import sina_should_abort


def test_sina_keeps_partial_when_enough_stocks() -> None:
    assert sina_should_abort(5546, 6) is False
    assert sina_should_abort(1000, 20) is False


def test_sina_aborts_when_too_few_and_many_failed_pages() -> None:
    assert sina_should_abort(200, 6) is True
    assert sina_should_abort(0, 6) is True


def test_sina_tolerates_few_failed_pages_while_filling() -> None:
    assert sina_should_abort(200, 3) is False
