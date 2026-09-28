"""Limpeza e tipagem dos dados brutos extraídos do SGS/Banco Central.

Este módulo é uma função pura: recebe o resultado de
`extracao.buscar_series` (`dict[int, ResultadoSerie]`) e devolve um único
`pandas.DataFrame` em formato longo, com datas e valores convertidos e
tipados. Não faz I/O, não acessa rede, não usa DuckDB.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from indicadores.extracao import SERIES, ResultadoSerie

logger = logging.getLogger("indicadores.limpeza")

COLUNAS: list[str] = ["codigo", "serie", "data", "valor"]

PADRAO_MILHAR = re.compile(r"-?\d{1,3}(\.\d{3})+(,\d+)?", re.ASCII)
PADRAO_VIRGULA_DECIMAL = re.compile(r"-?\d+,\d+", re.ASCII)
PADRAO_INTEIRO = re.compile(r"-?\d+", re.ASCII)
PADRAO_PONTO_DECIMAL = re.compile(r"-?\d+\.\d+", re.ASCII)


@dataclass(frozen=True)
class Descarte:
    """Uma linha bruta que não pôde ser aproveitada."""

    codigo: int
    data_bruta: str
    valor_bruto: str
    motivo: str  # "data_invalida" | "valor_invalido" | "data_duplicada"


@dataclass(frozen=True)
class SerieIgnorada:
    """Uma série inteira ignorada por ter falhado na extração."""

    codigo: int
    motivo: str  # str(resultado.erro) — mensagem da exceção original


@dataclass(frozen=True)
class ResultadoLimpeza:
    """Resultado da limpeza de todas as séries."""

    dados: pd.DataFrame
    descartes: list[Descarte]
    series_ignoradas: list[SerieIgnorada]


def _parse_data(bruto: str) -> datetime | None:
    """Converte `dd/mm/aaaa` em `datetime`; `None` se inválida."""
    texto = bruto.strip()
    try:
        # Datas de calendário puras (sem hora), conforme a spec: devem
        # continuar "naive" para virar `datetime64[ns]` (não
        # `datetime64[ns, tz]`) no DataFrame final.
        return datetime.strptime(texto, "%d/%m/%Y")  # noqa: DTZ007
    except ValueError:
        return None


def _parse_valor(bruto: str) -> float | None:
    """Converte string numérica BR (vírgula decimal) em `float`; `None` se inválida."""
    texto = bruto.strip()

    if PADRAO_MILHAR.fullmatch(texto):
        # ponto(s) = separador de milhar; vírgula (se houver) = decimal.
        return float(texto.replace(".", "").replace(",", "."))
    if PADRAO_VIRGULA_DECIMAL.fullmatch(texto):
        return float(texto.replace(",", "."))
    if PADRAO_INTEIRO.fullmatch(texto):
        return float(texto)
    if PADRAO_PONTO_DECIMAL.fullmatch(texto):
        # só chega aqui se NÃO casou com o padrão de milhar acima —
        # ou seja, o(s) grupo(s) após o ponto não têm exatamente 3
        # dígitos, então o ponto é decimal, não milhar.
        return float(texto)

    return None


def _dataframe_vazio() -> pd.DataFrame:
    """DataFrame vazio com as 4 colunas e os dtypes explícitos exigidos."""
    df = pd.DataFrame(columns=COLUNAS)
    return _aplicar_dtypes(df)


def _aplicar_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """Aplica os dtypes explícitos exigidos pela spec, nunca por inferência."""
    df = df.copy()
    df["codigo"] = df["codigo"].astype("int64")
    df["serie"] = df["serie"].astype(object)
    # A coluna já contém `datetime` (parse feito em `_parse_data`); só
    # falta fixar a resolução explícita `ns`, sem reparsear string.
    df["data"] = df["data"].astype("datetime64[ns]")
    df["valor"] = df["valor"].astype("float64")
    return df


def _registrar_descarte(
    descartes: list[Descarte],
    codigo: int,
    data_bruta: str,
    valor_bruto: str,
    motivo: str,
) -> None:
    """Registra um `Descarte` na lista e loga o motivo em `warning`."""
    descartes.append(
        Descarte(
            codigo=codigo,
            data_bruta=data_bruta,
            valor_bruto=valor_bruto,
            motivo=motivo,
        )
    )
    logger.warning(
        "descarte série %s: %s (data=%r, valor=%r)",
        codigo,
        motivo,
        data_bruta,
        valor_bruto,
    )


def limpar(resultados: dict[int, ResultadoSerie]) -> ResultadoLimpeza:
    """Limpa e tipa os dados brutos de todas as séries.

    Para cada `ResultadoSerie`: séries com `sucesso is False` são
    ignoradas (registradas em `series_ignoradas`); nas demais, cada item
    é validado e convertido, itens inválidos viram `descartes` e
    duplicatas de data entre linhas totalmente válidas também viram
    descarte. Nunca levanta exceção por causa de dado malformado.
    """
    descartes: list[Descarte] = []
    series_ignoradas: list[SerieIgnorada] = []
    linhas: list[dict[str, object]] = []

    for codigo, resultado in resultados.items():
        if not resultado.sucesso:
            motivo = str(resultado.erro)
            series_ignoradas.append(SerieIgnorada(codigo=codigo, motivo=motivo))
            logger.warning(
                "série %s ignorada: %s",
                codigo,
                motivo,
            )
            continue

        nome_serie = SERIES.get(codigo)
        if nome_serie is None:
            nome_serie = f"serie_{codigo}"
            logger.warning(
                "código %s fora do registro SERIES; usando nome de fallback '%s'",
                codigo,
                nome_serie,
            )

        datas_validas_vistas: set[datetime] = set()

        for item in resultado.dados or []:
            data_bruta = item["data"]
            valor_bruto = item["valor"]

            data = _parse_data(data_bruta)
            if data is None:
                _registrar_descarte(
                    descartes, codigo, data_bruta, valor_bruto, "data_invalida"
                )
                continue

            valor = _parse_valor(valor_bruto)
            if valor is None:
                _registrar_descarte(
                    descartes, codigo, data_bruta, valor_bruto, "valor_invalido"
                )
                continue

            if data in datas_validas_vistas:
                _registrar_descarte(
                    descartes, codigo, data_bruta, valor_bruto, "data_duplicada"
                )
                continue

            datas_validas_vistas.add(data)
            linhas.append(
                {
                    "codigo": codigo,
                    "serie": nome_serie,
                    "data": data,
                    "valor": valor,
                }
            )

    if not linhas:
        dados = _dataframe_vazio()
    else:
        dados = pd.DataFrame(linhas, columns=COLUNAS)
        dados = _aplicar_dtypes(dados)

    dados = dados.sort_values(["codigo", "data"]).reset_index(drop=True)

    return ResultadoLimpeza(
        dados=dados,
        descartes=descartes,
        series_ignoradas=series_ignoradas,
    )
