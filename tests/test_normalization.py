from decimal import Decimal

import pytest

from src.utils.normalization import absolute_url, integer, number, text


@pytest.mark.parametrize(("value", "expected"), [
    ("R$ 1.234.567,89", Decimal("1234567.89")),
    ("85,5 m²", Decimal("85.5")),
    ("12.5", Decimal("12.5")),
    (None, None),
    (0, Decimal("0")),
])
def test_number(value: object, expected: Decimal | None) -> None:
    assert number(value) == expected


@pytest.mark.parametrize("value", ["-10", "R$ consultar", "1.2.3", True, float("nan")])
def test_invalid_number(value: object) -> None:
    with pytest.raises(ValueError):
        number(value)


def test_text_integer_and_url() -> None:
    assert text("  Centro   Histórico ") == "Centro Histórico"
    assert text("  ") is None
    assert integer("2") == 2
    assert absolute_url("/imovel/1", "https://example.com/busca") == "https://example.com/imovel/1"
    with pytest.raises(ValueError):
        integer("2,5")
    with pytest.raises(ValueError):
        absolute_url("javascript:alert(1)", "https://example.com")
