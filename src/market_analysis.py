"""Análise exploratória em memória, sem alterar os imóveis de entrada."""

import re
import statistics
from collections import defaultdict
from decimal import Decimal
from typing import Callable, Literal, Sequence, TypedDict

from src.models.property import Property
from src.utils.normalization import number

Quality = Literal["valid", "attention", "invalid_for_area_analysis"]
Segment = Literal["RESIDENCIAL", "COMERCIAL", "OUTROS"]
SampleSize = Literal["pequena", "média", "grande"]
GroupKey = str | int | None
AREA_IN_TITLE = re.compile(r"(?<![\d.,])(\d+(?:[.,]\d+)*)\s*m(?:2|²)(?!\w)", re.IGNORECASE)
PRICE_RANGES = (
    "<=100000", ">100000 a <=250000", ">250000 a <=500000",
    ">500000 a <=1000000", ">1000000",
)


class Statistics(TypedDict):
    count: int
    minimum: Decimal | None
    maximum: Decimal | None
    mean: Decimal | None
    median: Decimal | None


class RecordValue(TypedDict):
    index: int
    id_anuncio: str | None
    value: Decimal


class Outliers(TypedDict):
    sample_size: int
    q1: Decimal | None
    q3: Decimal | None
    lower: Decimal | None
    upper: Decimal | None
    records: list[RecordValue]


class Assessment(TypedDict):
    index: int
    id_anuncio: str | None
    segmento: Segment
    quality: Quality
    reasons: list[str]
    preco_por_m2: Decimal | None


class Group(TypedDict):
    key: GroupKey
    count: int
    percentage: Decimal | None
    preco: Statistics
    area_m2: Statistics
    preco_por_m2: Statistics


class RankedNeighborhood(Group):
    sample_size: SampleSize


class Concentration(TypedDict):
    count: int
    missing_neighborhood: int
    top_neighborhood: str | None
    top_1_percentage: Decimal | None
    top_5_percentage: Decimal | None
    top_10_percentage: Decimal | None


class LocationAnalysis(TypedDict):
    by_neighborhood: list[Group]
    ranking_by_count: list[Group]
    ranking_by_price_m2: list[RankedNeighborhood]
    center_vs_others: list[Group]
    concentration: Concentration


class Locations(TypedDict):
    market: LocationAnalysis
    residential: LocationAnalysis
    commercial: LocationAnalysis


class Market(TypedDict):
    count: int
    preco: Statistics
    area_m2: Statistics
    preco_por_m2: Statistics


class MarketAnalysis(TypedDict):
    market: Market
    locations: Locations
    by_segment: list[Group]
    by_type: list[Group]
    by_neighborhood: list[Group]
    neighborhood_ranking: list[Group]
    min_observations: int
    center_vs_others: list[Group]
    by_price_range: list[Group]
    by_city: list[Group]
    by_bedrooms: list[Group]
    by_parking: list[Group]
    outliers: dict[str, Outliers]
    quality_counts: dict[Quality, int]
    assessments: list[Assessment]


def classify_segment(property_type: str | None) -> Segment:
    """Classifica o tipo informado, sem alterar o campo original."""
    normalized = property_type.strip().casefold() if property_type else ""
    if normalized in {"apto", "casa", "cobertura", "sobrado"}:
        return "RESIDENCIAL"
    if normalized in {"sala", "conjunto", "loja", "pavilhão", "sobrado comercial", "box"}:
        return "COMERCIAL"
    return "OUTROS"


def _price_range(prop: Property) -> str:
    price = prop.preco
    if price is None:
        return "SEM_PRECO"
    if price < 0:
        return "PRECO_INVALIDO"
    if price <= 100000:
        return PRICE_RANGES[0]
    if price <= 250000:
        return PRICE_RANGES[1]
    if price <= 500000:
        return PRICE_RANGES[2]
    if price <= 1000000:
        return PRICE_RANGES[3]
    return PRICE_RANGES[4]


def _center_group(prop: Property) -> str | None:
    if prop.bairro is None or not prop.bairro.strip():
        return None
    return "Centro" if prop.bairro == "Centro" else "Demais bairros"


def _statistics(values: Sequence[Decimal]) -> Statistics:
    if not values:
        return {"count": 0, "minimum": None, "maximum": None, "mean": None, "median": None}
    return {
        "count": len(values),
        "minimum": min(values),
        "maximum": max(values),
        "mean": sum(values, Decimal(0)) / len(values),
        "median": statistics.median(values),
    }


def _outliers(properties: Sequence[Property], values: Sequence[Decimal | None]) -> Outliers:
    indexed = [(index, value) for index, value in enumerate(values) if value is not None and value > 0]
    sample = sorted(value for _, value in indexed)
    if len(sample) < 2:
        return {"sample_size": len(sample), "q1": None, "q3": None,
                "lower": None, "upper": None, "records": []}
    q1, _, q3 = statistics.quantiles(sample, n=4, method="inclusive")
    spread = (q3 - q1) * Decimal("1.5")
    lower, upper = q1 - spread, q3 + spread
    return {
        "sample_size": len(sample),
        "q1": q1,
        "q3": q3,
        "lower": lower,
        "upper": upper,
        "records": [
            {"index": index, "id_anuncio": properties[index].id_anuncio, "value": value}
            for index, value in indexed if value < lower or value > upper
        ],
    }


def _title_area_reasons(prop: Property) -> list[str]:
    if not prop.titulo or prop.area_m2 is None:
        return []
    title_areas: list[Decimal] = []
    reasons: list[str] = []
    for match in AREA_IN_TITLE.finditer(prop.titulo):
        try:
            area = number(match.group(1))
        except ValueError:
            reasons.append("area_titulo_nao_interpretavel")
            continue
        if area is not None:
            title_areas.append(area)
    if title_areas and all(abs(area - prop.area_m2) > Decimal("0.5") for area in title_areas):
        reasons.append("area_titulo_divergente")
    return reasons


def _groups(
    properties: Sequence[Property],
    rates: Sequence[Decimal | None],
    key: Callable[[Property], GroupKey],
    *,
    expected_keys: Sequence[GroupKey] = (),
) -> list[Group]:
    groups: dict[GroupKey, list[int]] = defaultdict(list)
    for value in expected_keys:
        groups[value] = []
    for index, prop in enumerate(properties):
        value = key(prop)
        if isinstance(value, str):
            value = value.strip() or None
        groups[value].append(index)
    result: list[Group] = []
    for value, indexes in groups.items():
        prices = [properties[i].preco for i in indexes]
        areas = [properties[i].area_m2 for i in indexes]
        result.append({
            "key": value,
            "count": len(indexes),
            "percentage": Decimal(len(indexes)) * 100 / len(properties) if properties else None,
            "preco": _statistics([item for item in prices if item is not None and item >= 0]),
            "area_m2": _statistics([item for item in areas if item is not None and item >= 0]),
            "preco_por_m2": _statistics([rates[i] for i in indexes if rates[i] is not None]),
        })
    return result


def _location_analysis(
    properties: Sequence[Property], rates: Sequence[Decimal | None], min_observations: int
) -> LocationAnalysis:
    neighborhoods = _groups(properties, rates, lambda prop: prop.bairro)
    identified = [group for group in neighborhoods if group["key"] is not None]
    by_count = sorted(identified, key=lambda group: (-group["count"], str(group["key"]).casefold()))
    eligible = [
        (median, group)
        for group in identified
        if group["preco_por_m2"]["count"] >= max(5, min_observations)
        for median in [group["preco_por_m2"]["median"]]
        if median is not None
    ]
    eligible.sort(key=lambda item: (-item[0], str(item[1]["key"]).casefold()))
    ranking: list[RankedNeighborhood] = []
    for _, group in eligible:
        count = group["preco_por_m2"]["count"]
        sample_size: SampleSize = "pequena" if count < 10 else "média" if count < 20 else "grande"
        ranking.append({**group, "sample_size": sample_size})

    def share(limit: int) -> Decimal | None:
        if not properties:
            return None
        return Decimal(sum(group["count"] for group in by_count[:limit])) * 100 / len(properties)

    return {
        "by_neighborhood": neighborhoods,
        "ranking_by_count": by_count,
        "ranking_by_price_m2": ranking,
        "center_vs_others": _groups(
            properties, rates, _center_group, expected_keys=("Centro", "Demais bairros")
        ),
        "concentration": {
            "count": len(properties),
            "missing_neighborhood": sum(
                group["count"] for group in neighborhoods if group["key"] is None
            ),
            "top_neighborhood": str(by_count[0]["key"]) if by_count else None,
            "top_1_percentage": share(1),
            "top_5_percentage": share(5),
            "top_10_percentage": share(10),
        },
    }


def analyze_properties(
    properties: Sequence[Property], *, min_observations: int = 5
) -> MarketAnalysis:
    """Analisa objetos Property sem modificá-los; índices de registros começam em zero."""
    if isinstance(min_observations, bool) or not isinstance(min_observations, int) or min_observations < 1:
        raise ValueError("min_observations deve ser um inteiro positivo.")
    prices = [prop.preco if prop.preco is not None and prop.preco >= 0 else None for prop in properties]
    areas = [prop.area_m2 if prop.area_m2 is not None and prop.area_m2 >= 0 else None for prop in properties]
    rates = [
        price / area if price is not None and area is not None and area > 1 else None
        for price, area in zip(prices, areas)
    ]
    outliers = {
        "preco": _outliers(properties, prices),
        "area_m2": _outliers(properties, areas),
        "preco_por_m2": _outliers(properties, rates),
    }
    outlier_indexes = {
        field: {record["index"] for record in result["records"]}
        for field, result in outliers.items()
    }
    assessments: list[Assessment] = []
    quality_counts: dict[Quality, int] = {"valid": 0, "attention": 0, "invalid_for_area_analysis": 0}
    for index, prop in enumerate(properties):
        reasons: list[str] = []
        if prop.preco is None:
            reasons.append("preco_ausente")
        elif prop.preco == 0:
            reasons.append("preco_zero")
        for field in ("preco", "area_m2", "quartos", "banheiros", "vagas"):
            value = getattr(prop, field)
            if value is not None and value < 0:
                reasons.append(f"{field}_negativo")
        if prop.area_m2 is None:
            reasons.append("area_ausente")
        elif prop.area_m2 == 0:
            reasons.append("area_zero")
        elif 0 < prop.area_m2 <= 1:
            reasons.append("area_ate_um_m2")
        reasons.extend(_title_area_reasons(prop))
        for field, indexes in outlier_indexes.items():
            if index in indexes:
                reasons.append(f"outlier_{field}")
        quality: Quality
        if prop.area_m2 is None or prop.area_m2 <= 1:
            quality = "invalid_for_area_analysis"
        elif reasons:
            quality = "attention"
        else:
            quality = "valid"
        quality_counts[quality] += 1
        assessments.append({
            "index": index, "id_anuncio": prop.id_anuncio,
            "segmento": classify_segment(prop.tipo_imovel), "quality": quality,
            "reasons": reasons, "preco_por_m2": rates[index],
        })
    market_location = _location_analysis(properties, rates, min_observations)
    neighborhoods = market_location["by_neighborhood"]
    segment_locations: dict[str, LocationAnalysis] = {}
    for segment in ("RESIDENCIAL", "COMERCIAL"):
        indexes = [index for index, assessment in enumerate(assessments)
                   if assessment["segmento"] == segment]
        segment_locations[segment] = _location_analysis(
            [properties[index] for index in indexes], [rates[index] for index in indexes],
            min_observations,
        )
    ranked = [
        (median, group)
        for group in neighborhoods
        if group["key"] is not None and group["preco_por_m2"]["count"] >= min_observations
        for median in [group["preco_por_m2"]["median"]]
        if median is not None
    ]
    ranked.sort(key=lambda item: item[0], reverse=True)
    return {
        "market": {
            "count": len(properties),
            "preco": _statistics([value for value in prices if value is not None]),
            "area_m2": _statistics([value for value in areas if value is not None]),
            "preco_por_m2": _statistics([value for value in rates if value is not None]),
        },
        "locations": {
            "market": market_location,
            "residential": segment_locations["RESIDENCIAL"],
            "commercial": segment_locations["COMERCIAL"],
        },
        "by_segment": _groups(
            properties, rates, lambda prop: classify_segment(prop.tipo_imovel),
            expected_keys=("RESIDENCIAL", "COMERCIAL", "OUTROS"),
        ),
        "by_type": _groups(properties, rates, lambda prop: prop.tipo_imovel),
        "by_neighborhood": neighborhoods,
        "neighborhood_ranking": [group for _, group in ranked],
        "min_observations": min_observations,
        "center_vs_others": market_location["center_vs_others"],
        "by_price_range": _groups(properties, rates, _price_range, expected_keys=PRICE_RANGES),
        "by_city": _groups(properties, rates, lambda prop: prop.cidade),
        "by_bedrooms": _groups(properties, rates, lambda prop: prop.quartos),
        "by_parking": _groups(properties, rates, lambda prop: prop.vagas),
        "outliers": outliers,
        "quality_counts": quality_counts,
        "assessments": assessments,
    }
