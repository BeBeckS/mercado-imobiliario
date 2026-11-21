from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class Listing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id_anuncio: str | None = None
    titulo: str | None = None
    preco: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    area_m2: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    quartos: int | None = Field(default=None, ge=0, strict=True)
    banheiros: int | None = Field(default=None, ge=0, strict=True)
    vagas: int | None = Field(default=None, ge=0, strict=True)
    tipo_imovel: str | None = None
    bairro: str | None = None
    cidade: str | None = None
    endereco: str | None = None
    url: HttpUrl | None = None
    fonte: str = Field(min_length=1)
    data_coleta: datetime
    hash_registro: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("data_coleta")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("data_coleta deve incluir fuso horário.")
        return value
