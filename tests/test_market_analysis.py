import json
from decimal import Decimal
from pathlib import Path

import pytest

from src.market_analysis import analyze_properties, classify_segment
from src.models.property import Property
from src.utils.persistence import load_properties


def test_rates_statistics_and_quality_do_not_mutate_properties() -> None:
    properties = [
        Property(id_anuncio="1", preco=Decimal("100000"), area_m2=Decimal("100")),
        Property(id_anuncio="2", preco=Decimal("240000"), area_m2=Decimal("120")),
        Property(id_anuncio="3", preco=Decimal("200000"), area_m2=Decimal("0")),
        Property(id_anuncio="4", preco=Decimal("90000"), area_m2=Decimal("1")),
        Property(id_anuncio="5", preco=Decimal("50000"), area_m2=Decimal("0.01")),
        Property(id_anuncio="6", preco=Decimal("0"), area_m2=Decimal("10")),
        Property(id_anuncio="7", preco=None, area_m2=Decimal("20")),
        Property(id_anuncio="8", preco=Decimal("100"), area_m2=None),
    ]
    before = [prop.model_dump() for prop in properties]
    result = analyze_properties(properties)

    assert [item["preco_por_m2"] for item in result["assessments"]] == [
        Decimal("1000"), Decimal("2000"), None, None, None, Decimal("0"), None, None
    ]
    assert result["market"]["count"] == 8
    assert result["market"]["preco"]["count"] == 7
    assert result["market"]["preco"]["mean"] == Decimal("680100") / 7
    assert result["market"]["preco"]["median"] == Decimal("90000")
    assert result["market"]["area_m2"]["count"] == 7
    assert result["market"]["area_m2"]["median"] == Decimal("10")
    assert result["market"]["preco_por_m2"] == {
        "count": 3, "minimum": Decimal("0"), "maximum": Decimal("2000"),
        "mean": Decimal("1000"), "median": Decimal("1000"),
    }
    assert [result["assessments"][i]["quality"] for i in (2, 3, 4, 7)] == [
        "invalid_for_area_analysis"
    ] * 4
    assert "preco_zero" in result["assessments"][5]["reasons"]
    assert "preco_ausente" in result["assessments"][6]["reasons"]
    assert [prop.model_dump() for prop in properties] == before


def test_grouping_counts_and_observation_denominators() -> None:
    properties = [
        Property(preco=100, area_m2=10, tipo_imovel="Apto", bairro="Centro",
                 cidade="Cidade A", quartos=1, vagas=0),
        Property(preco=300, area_m2=20, tipo_imovel="Apto", bairro="Centro",
                 cidade="Cidade A", quartos=1, vagas=1),
        Property(preco=600, area_m2=0, tipo_imovel="Casa", bairro="Norte",
                 cidade="Cidade B", quartos=2, vagas=1),
        Property(preco=None, area_m2=None),
    ]
    result = analyze_properties(properties)
    apartment = next(group for group in result["by_type"] if group["key"] == "Apto")
    assert apartment["count"] == 2
    assert apartment["preco"]["mean"] == apartment["preco"]["median"] == Decimal("200")
    assert apartment["area_m2"]["mean"] == apartment["area_m2"]["median"] == Decimal("15")
    assert apartment["preco_por_m2"]["median"] == Decimal("12.5")
    center = next(group for group in result["by_neighborhood"] if group["key"] == "Centro")
    assert center["count"] == 2 and center["preco"]["median"] == Decimal("200")
    assert next(g for g in result["by_city"] if g["key"] == "Cidade B")["preco_por_m2"]["count"] == 0
    bedroom = next(g for g in result["by_bedrooms"] if g["key"] == 1)
    assert bedroom["preco"]["median"] == Decimal("200")
    assert bedroom["area_m2"]["median"] == Decimal("15")
    assert bedroom["preco_por_m2"]["median"] == Decimal("12.5")
    parking = next(g for g in result["by_parking"] if g["key"] == 1)
    assert parking["count"] == 2
    assert parking["preco"]["median"] == Decimal("450")
    assert parking["area_m2"]["median"] == Decimal("10")
    assert parking["preco_por_m2"]["median"] == Decimal("15")
    assert next(g for g in result["by_type"] if g["key"] is None)["preco"]["count"] == 0


def test_iqr_flags_each_metric_without_treating_outliers_as_invalid() -> None:
    properties = [
        Property(id_anuncio=str(i), preco=10000, area_m2=10)
        for i in range(4)
    ] + [Property(id_anuncio="extremo", preco=1000000, area_m2=100)]
    result = analyze_properties(properties)
    for field in ("preco", "area_m2", "preco_por_m2"):
        assert [item["id_anuncio"] for item in result["outliers"][field]["records"]] == ["extremo"]
        assert f"outlier_{field}" in result["assessments"][-1]["reasons"]
    assert result["assessments"][-1]["quality"] == "attention"
    assert result["assessments"][-1]["preco_por_m2"] == Decimal("10000")


def test_positive_area_threshold_and_valid_quality() -> None:
    properties = [
        Property(preco=100, area_m2=Decimal("0.999")),
        Property(preco=100, area_m2=Decimal("1")),
        Property(preco=100, area_m2=Decimal("1.001")),
    ]
    result = analyze_properties(properties)
    assert [item["preco_por_m2"] is None for item in result["assessments"]] == [True, True, False]
    assert result["assessments"][2]["quality"] == "valid"
    assert result["quality_counts"]["invalid_for_area_analysis"] == 2


@pytest.mark.parametrize(("title", "area", "expected"), [
    ("Sala 38 m²", "0", True),
    ("Sala 42 m2", "43", True),
    ("Sala 40 m²", "40.49", False),
    ("Sala 40 m²", "40.5", False),
    ("Sala 1.396 m²", "1396", False),
    ("Área útil 37 m², total 45 m²", "45", False),
    ("Sem metragem no título", "45", False),
])
def test_title_area_comparison(title: str, area: str, expected: bool) -> None:
    prop = Property(titulo=title, preco=100, area_m2=Decimal(area))
    reasons = analyze_properties([prop])["assessments"][0]["reasons"]
    assert ("area_titulo_divergente" in reasons) is expected


def test_unparseable_area_in_title_is_reported() -> None:
    prop = Property(titulo="Sala 1,23,4 m²", preco=100, area_m2=30)
    assessment = analyze_properties([prop])["assessments"][0]
    assert assessment["quality"] == "attention"
    assert "area_titulo_nao_interpretavel" in assessment["reasons"]


def test_negative_values_are_reported_and_not_used_in_statistics() -> None:
    invalid_price = Property.model_construct(preco=Decimal("-1"), area_m2=Decimal("10"), vagas=-1)
    invalid_area = Property.model_construct(preco=Decimal("100"), area_m2=Decimal("-2"))
    result = analyze_properties([invalid_price, invalid_area])
    assert result["market"]["preco"]["count"] == 1
    assert result["market"]["area_m2"]["count"] == 1
    assert result["market"]["preco_por_m2"]["count"] == 0
    assert {"preco_negativo", "vagas_negativo"} <= set(result["assessments"][0]["reasons"])
    assert "area_m2_negativo" in result["assessments"][1]["reasons"]
    assert result["assessments"][1]["quality"] == "invalid_for_area_analysis"


def test_empty_and_missing_observations_are_explicit() -> None:
    empty = analyze_properties([])
    assert empty["market"]["count"] == 0
    assert empty["market"]["preco"]["median"] is None
    assert empty["outliers"]["preco"]["q1"] is None
    assert empty["assessments"] == []

    missing = analyze_properties([Property()])
    assert missing["market"]["preco_por_m2"]["count"] == 0
    assert missing["assessments"][0]["reasons"] == ["preco_ausente", "area_ausente"]
    assert missing["assessments"][0]["quality"] == "invalid_for_area_analysis"


def test_analysis_of_persisted_properties_does_not_change_file(tmp_path: Path) -> None:
    path = tmp_path / "properties.json"
    content = json.dumps([Property(id_anuncio="1", preco=100, area_m2=10).model_dump(mode="json")])
    path.write_text(content, encoding="utf-8")
    before = path.read_bytes()
    result = analyze_properties(load_properties(path))
    assert result["market"]["count"] == 1
    assert result["market"]["preco_por_m2"]["median"] == Decimal("10")
    assert path.read_bytes() == before


@pytest.mark.parametrize(("property_type", "segment"), [
    ("Apto", "RESIDENCIAL"), ("Casa", "RESIDENCIAL"),
    ("Cobertura", "RESIDENCIAL"), ("Sobrado", "RESIDENCIAL"),
    ("Sala", "COMERCIAL"), ("Conjunto", "COMERCIAL"), ("Loja", "COMERCIAL"),
    (" PAVILHÃO ", "COMERCIAL"), ("Sobrado Comercial", "COMERCIAL"),
    ("Box", "COMERCIAL"), ("Terreno", "OUTROS"), ("desconhecido", "OUTROS"),
    (None, "OUTROS"),
])
def test_segment_classification(property_type: str | None, segment: str) -> None:
    assert classify_segment(property_type) == segment


def test_segment_and_type_comparison_preserves_input_and_observation_counts() -> None:
    properties = [
        Property(tipo_imovel="Apto", preco=100, area_m2=10),
        Property(tipo_imovel="Apto", preco=300, area_m2=20),
        Property(tipo_imovel="Sala", preco=500, area_m2=0),
        Property(tipo_imovel="Terreno", preco=100, area_m2=10),
    ]
    before = [prop.model_dump() for prop in properties]
    result = analyze_properties(properties)
    segments = {group["key"]: group for group in result["by_segment"]}
    assert segments["RESIDENCIAL"]["count"] == 2
    assert segments["RESIDENCIAL"]["percentage"] == Decimal("50")
    assert segments["RESIDENCIAL"]["preco"]["mean"] == Decimal("200")
    assert segments["RESIDENCIAL"]["area_m2"]["median"] == Decimal("15")
    assert segments["RESIDENCIAL"]["preco_por_m2"]["median"] == Decimal("12.5")
    assert segments["COMERCIAL"]["percentage"] == Decimal("25")
    assert segments["COMERCIAL"]["preco_por_m2"]["count"] == 0
    assert segments["OUTROS"]["count"] == 1
    apartment = next(group for group in result["by_type"] if group["key"] == "Apto")
    assert apartment["percentage"] == Decimal("50")
    assert apartment["preco_por_m2"]["count"] == 2
    assert result["assessments"][2]["segmento"] == "COMERCIAL"
    assert [prop.model_dump() for prop in properties] == before


def test_ranking_uses_eligible_rate_count_and_keeps_small_groups_in_table() -> None:
    properties = [
        *[Property(bairro="A", preco=400, area_m2=10) for _ in range(4)],
        Property(bairro="A", preco=400, area_m2=0),
        *[Property(bairro="B", preco=200, area_m2=10) for _ in range(5)],
        *[Property(bairro="C", preco=300, area_m2=10) for _ in range(2)],
    ]
    result = analyze_properties(properties)
    assert {group["key"] for group in result["by_neighborhood"]} == {"A", "B", "C"}
    assert [group["key"] for group in result["neighborhood_ranking"]] == ["B"]
    a = next(group for group in result["by_neighborhood"] if group["key"] == "A")
    assert a["count"] == 5 and a["preco_por_m2"]["count"] == 4
    assert [group["key"] for group in analyze_properties(
        properties, min_observations=2
    )["neighborhood_ranking"]] == ["A", "C", "B"]


@pytest.mark.parametrize("minimum", [0, -1, True, 1.5])
def test_invalid_ranking_minimum_is_rejected(minimum: int) -> None:
    with pytest.raises(ValueError, match="min_observations"):
        analyze_properties([], min_observations=minimum)


def test_center_comparison_is_exact_and_preserves_missing_neighborhood() -> None:
    properties = [
        Property(bairro="Centro", preco=100, area_m2=10),
        Property(bairro="Centro Histórico", preco=200, area_m2=10),
        Property(bairro="Centro Historico", preco=300, area_m2=0),
        Property(bairro=None, preco=400, area_m2=10),
    ]
    groups = {group["key"]: group for group in analyze_properties(properties)["center_vs_others"]}
    assert groups["Centro"]["count"] == 1
    assert groups["Centro"]["preco"]["median"] == Decimal("100")
    assert groups["Demais bairros"]["count"] == 2
    assert groups["Demais bairros"]["preco"]["median"] == Decimal("250")
    assert groups["Demais bairros"]["preco_por_m2"]["count"] == 1
    assert groups[None]["count"] == 1


def test_price_range_boundaries_and_missing_prices_are_accounted_for() -> None:
    prices = [
        "0", "100000", "100000.01", "250000", "250000.01",
        "500000", "500000.01", "1000000", "1000000.01",
    ]
    properties = [Property(preco=Decimal(value), area_m2=10) for value in prices]
    properties.append(Property(preco=None, area_m2=10))
    properties.append(Property.model_construct(preco=Decimal("-1"), area_m2=Decimal("10")))
    result = analyze_properties(properties)
    groups = {group["key"]: group for group in result["by_price_range"]}
    assert [groups[key]["count"] for key in (
        "<=100000", ">100000 a <=250000", ">250000 a <=500000",
        ">500000 a <=1000000", ">1000000", "SEM_PRECO", "PRECO_INVALIDO",
    )] == [2, 2, 2, 2, 1, 1, 1]
    assert groups["<=100000"]["percentage"] == Decimal("200") / 11
    assert groups["<=100000"]["preco"]["median"] == Decimal("50000")
    assert groups["SEM_PRECO"]["preco"]["count"] == 0
    assert groups["PRECO_INVALIDO"]["preco"]["count"] == 0
    assert sum(group["count"] for group in groups.values()) == len(properties)


def test_price_range_does_not_round_subcent_values() -> None:
    result = analyze_properties([Property(preco=Decimal("100000.005"), area_m2=10)])
    group = next(group for group in result["by_price_range"] if group["count"])
    assert group["key"] == ">100000 a <=250000"


def test_empty_comparisons_have_undefined_percentage_and_no_division_by_zero() -> None:
    result = analyze_properties([])
    assert result["neighborhood_ranking"] == []
    assert all(group["count"] == 0 and group["percentage"] is None for group in result["by_segment"])
    assert all(group["percentage"] is None for group in result["by_price_range"])
    assert all(group["preco_por_m2"]["median"] is None for group in result["center_vs_others"])


def test_location_rankings_and_sample_size_boundaries() -> None:
    properties = [
        Property(bairro=f"B{size:02}", tipo_imovel="Apto", preco=size * 100, area_m2=10)
        for size in (4, 5, 9, 10, 19, 20)
        for _ in range(size)
    ]
    properties.append(Property(bairro="B05", tipo_imovel="Apto", preco=500, area_m2=1))
    location = analyze_properties(properties)["locations"]["market"]
    ranking = location["ranking_by_price_m2"]
    assert [group["key"] for group in ranking] == ["B20", "B19", "B10", "B09", "B05"]
    assert [group["sample_size"] for group in ranking] == [
        "grande", "média", "média", "pequena", "pequena"
    ]
    smallest = ranking[-1]
    assert smallest["count"] == 6 and smallest["preco_por_m2"]["count"] == 5
    assert smallest["preco"]["median"] == Decimal("500")
    assert smallest["area_m2"]["median"] == Decimal("10")
    assert smallest["preco_por_m2"]["median"] == Decimal("50")
    assert [group["key"] for group in location["ranking_by_count"]] == [
        "B20", "B19", "B10", "B09", "B05", "B04"
    ]
    relaxed = analyze_properties(properties, min_observations=2)
    assert "B04" in [group["key"] for group in relaxed["neighborhood_ranking"]]
    assert "B04" not in [
        group["key"] for group in relaxed["locations"]["market"]["ranking_by_price_m2"]
    ]
    strict = analyze_properties(properties, min_observations=10)
    assert [group["key"] for group in strict["locations"]["market"]["ranking_by_price_m2"]] == [
        "B20", "B19", "B10"
    ]


def test_segment_locations_and_center_comparisons_remain_separate() -> None:
    properties = [
        *[Property(tipo_imovel="Apto", bairro="Centro", preco=100, area_m2=10) for _ in range(5)],
        *[Property(tipo_imovel="Casa", bairro="Centro Historico", preco=300, area_m2=10) for _ in range(5)],
        *[Property(tipo_imovel="Sala", bairro="Centro", preco=500, area_m2=10) for _ in range(5)],
        *[Property(tipo_imovel="Box", bairro="Centro Histórico", preco=600, area_m2=10) for _ in range(4)],
        Property(tipo_imovel="Loja", bairro="Centro Histórico", preco=600, area_m2=0),
        Property(tipo_imovel="Terreno", bairro="Centro", preco=9999, area_m2=10),
        Property(tipo_imovel="Apto", bairro=None, preco=100, area_m2=10),
    ]
    result = analyze_properties(properties)
    residential = result["locations"]["residential"]
    commercial = result["locations"]["commercial"]
    assert residential["concentration"]["count"] == 11
    assert commercial["concentration"]["count"] == 10
    assert [group["key"] for group in residential["ranking_by_price_m2"]] == [
        "Centro Historico", "Centro"
    ]
    assert [group["key"] for group in commercial["ranking_by_price_m2"]] == ["Centro"]
    commercial_other = next(g for g in commercial["by_neighborhood"] if g["key"] == "Centro Histórico")
    assert commercial_other["count"] == 5 and commercial_other["preco_por_m2"]["count"] == 4
    residential_comparison = {g["key"]: g for g in residential["center_vs_others"]}
    commercial_comparison = {g["key"]: g for g in commercial["center_vs_others"]}
    assert residential_comparison["Centro"]["preco_por_m2"]["median"] == Decimal("10")
    assert residential_comparison["Demais bairros"]["preco_por_m2"]["median"] == Decimal("30")
    assert residential_comparison[None]["count"] == 1
    assert commercial_comparison["Centro"]["preco"]["median"] == Decimal("500")
    assert commercial_comparison["Centro"]["area_m2"]["median"] == Decimal("10")
    assert commercial_comparison["Demais bairros"]["preco_por_m2"]["median"] == Decimal("60")
    assert commercial_comparison["Demais bairros"]["preco_por_m2"]["count"] == 4


def test_geographical_concentration_uses_all_properties_as_denominator() -> None:
    counts = {"A": 10, "B": 9, "C": 8, "D": 7, "E": 6, "F": 5,
              "G": 4, "H": 3, "I": 2, "J": 1, "K": 1}
    properties = [
        Property(tipo_imovel="Apto", bairro=name, preco=100, area_m2=10)
        for name, count in counts.items() for _ in range(count)
    ] + [Property(tipo_imovel="Apto", bairro=None) for _ in range(2)]
    location = analyze_properties(properties)["locations"]["residential"]
    concentration = location["concentration"]
    assert concentration["count"] == 58
    assert concentration["missing_neighborhood"] == 2
    assert concentration["top_neighborhood"] == "A"
    assert concentration["top_1_percentage"] == Decimal("1000") / 58
    assert concentration["top_5_percentage"] == Decimal("4000") / 58
    assert concentration["top_10_percentage"] == Decimal("5500") / 58
    assert len(location["ranking_by_count"]) == 11
    assert any(group["key"] is None and group["count"] == 2 for group in location["by_neighborhood"])


def test_concentration_empty_and_unknown_neighborhoods() -> None:
    empty = analyze_properties([])["locations"]["market"]["concentration"]
    assert empty["top_neighborhood"] is None
    assert empty["top_1_percentage"] is None
    assert empty["top_5_percentage"] is None
    assert empty["top_10_percentage"] is None
    unknown = analyze_properties([Property()])["locations"]["market"]
    assert unknown["concentration"]["missing_neighborhood"] == 1
    assert unknown["concentration"]["top_1_percentage"] == Decimal(0)
    assert unknown["ranking_by_count"] == []
