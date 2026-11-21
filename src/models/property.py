from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class Property(BaseModel):
    model_config = ConfigDict(extra="forbid")

    titulo: str | None = None
    preco: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    moeda: str | None = None
    tipo_imovel: str | None = None
    finalidade: str | None = None
    endereco: str | None = None
    localizacao: str | None = None
    cidade: str | None = None
    bairro: str | None = None
    estado: str | None = None
    area_m2: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    quartos: int | None = Field(default=None, ge=0, strict=True)
    banheiros: int | None = Field(default=None, ge=0, strict=True)
    vagas: int | None = Field(default=None, ge=0, strict=True)
    url: str | None = None
    id_anuncio: str | None = None
    fonte: str | None = None
