"""Análises de negócio sobre os dados persistidos (Selic, IPCA, PTAX).

Três funções puras que recebem um `pandas.DataFrame` (no formato de saída
de `persistencia.ler` ou `limpeza.limpar`) e devolvem outro DataFrame.
Nenhuma faz I/O: não abrem conexão, não conhecem `duckdb`/`httpx`.
"""

from __future__ import annotations

import math

import pandas as pd

CODIGO_SELIC_META: int = 432
CODIGO_IPCA_MENSAL: int = 433
CODIGO_PTAX_VENDA: int = 1

_COLUNAS_MINIMAS: set[str] = {"codigo", "data", "valor"}

_DTYPES_MUDANCAS_SELIC: dict[str, str] = {
    "data": "datetime64[ns]",
    "valor_anterior": "float64",
    "valor_novo": "float64",
    "variacao_pp": "float64",
}
_COLUNAS_MUDANCAS_SELIC: list[str] = list(_DTYPES_MUDANCAS_SELIC)

_DTYPES_IPCA_ACUMULADO: dict[str, str] = {
    "data": "datetime64[ns]",
    "ipca_mensal": "float64",
    "acumulado_12m": "float64",
}
_COLUNAS_IPCA_ACUMULADO: list[str] = list(_DTYPES_IPCA_ACUMULADO)

_DTYPES_PTAX_MENSAL: dict[str, str] = {
    "mes": "datetime64[ns]",
    "media": "float64",
    "fechamento": "float64",
    "minimo": "float64",
    "maximo": "float64",
    "dias_com_dado": "int64",
}
_COLUNAS_PTAX_MENSAL: list[str] = list(_DTYPES_PTAX_MENSAL)

# Tamanho da janela de meses consecutivos exigida pelo acumulado do IPCA.
_JANELA_IPCA_MESES: int = 12


class ErroAnalise(Exception):
    """`dados` não tem as colunas mínimas exigidas (codigo/data/valor)."""


def _validar_colunas(dados: pd.DataFrame) -> None:
    """Levanta `ErroAnalise` se `dados` não tiver codigo/data/valor."""
    faltando = _COLUNAS_MINIMAS - set(dados.columns)
    if faltando:
        raise ErroAnalise(f"colunas obrigatórias faltando: {sorted(faltando)}")


def _filtrar_codigo_ordenado(dados: pd.DataFrame, codigo: int) -> pd.DataFrame:
    """Filtra `dados` por `codigo` e ordena por `data`, sem alterar `dados`."""
    filtrado = dados.loc[dados["codigo"] == codigo, ["data", "valor"]].copy()
    return filtrado.sort_values("data").reset_index(drop=True)


def _aplicar_dtypes(df: pd.DataFrame, dtypes: dict[str, str]) -> pd.DataFrame:
    """Aplica dtypes explícitos coluna a coluna, nunca por inferência."""
    df = df.copy()
    for coluna, dtype in dtypes.items():
        df[coluna] = df[coluna].astype(dtype)
    return df


def _dataframe_vazio(colunas: list[str], dtypes: dict[str, str]) -> pd.DataFrame:
    """DataFrame vazio com as colunas e dtypes explícitos de uma análise."""
    df = pd.DataFrame(columns=colunas)
    return _aplicar_dtypes(df, dtypes).reset_index(drop=True)


def mudancas_selic(dados: pd.DataFrame) -> pd.DataFrame:
    """Datas em que a Selic meta (código 432) mudou de valor.

    Retorna colunas `data` (datetime64[ns]), `valor_anterior` (float64),
    `valor_novo` (float64) e `variacao_pp` (float64, = valor_novo -
    valor_anterior). Só inclui uma linha quando `valor_novo !=
    valor_anterior` (comparação exata). A primeira observação da série
    nunca aparece (não há "anterior" para compará-la).
    """
    _validar_colunas(dados)

    serie = _filtrar_codigo_ordenado(dados, CODIGO_SELIC_META)
    if serie.empty:
        return _dataframe_vazio(_COLUNAS_MUDANCAS_SELIC, _DTYPES_MUDANCAS_SELIC)

    valor_novo = serie["valor"]
    valor_anterior = valor_novo.shift(1)

    mudou = (valor_novo != valor_anterior).to_numpy(copy=True)
    mudou[0] = False  # nunca há "anterior" para a primeira observação

    resultado = pd.DataFrame(
        {
            "data": serie["data"],
            "valor_anterior": valor_anterior,
            "valor_novo": valor_novo,
        }
    )[mudou].copy()
    resultado["variacao_pp"] = resultado["valor_novo"] - resultado["valor_anterior"]
    resultado = resultado[_COLUNAS_MUDANCAS_SELIC]

    resultado = _aplicar_dtypes(resultado, _DTYPES_MUDANCAS_SELIC)
    return resultado.sort_values("data").reset_index(drop=True)


def ipca_acumulado_12m(dados: pd.DataFrame) -> pd.DataFrame:
    """Acumulado móvel de 12 meses do IPCA mensal (código 433).

    Para cada mês com uma janela de 12 meses consecutivos (sem lacuna,
    checado por `Period` mensal, não pelo dia exato da data) terminando
    nele, calcula
    `acumulado_12m = (prod(1 + v/100 para os 12 valores da janela) - 1)
    * 100`, sem arredondamento. Meses sem janela completa/sem lacuna
    simplesmente não aparecem na saída (não entram como `NaN`).
    """
    _validar_colunas(dados)

    serie = _filtrar_codigo_ordenado(dados, CODIGO_IPCA_MENSAL)
    if serie.empty:
        return _dataframe_vazio(_COLUNAS_IPCA_ACUMULADO, _DTYPES_IPCA_ACUMULADO)

    ordinais_mensais = serie["data"].dt.to_period("M").astype("int64")
    passo_consecutivo = (ordinais_mensais.diff() == 1).astype("int64")

    passos_na_janela = _JANELA_IPCA_MESES - 1
    janela_ok = passo_consecutivo.rolling(window=passos_na_janela).sum() == passos_na_janela

    valores = serie["valor"].to_numpy()
    datas = serie["data"].to_numpy()

    linhas = []
    for i in range(len(serie)):
        if not bool(janela_ok.iloc[i]):
            continue
        janela = valores[i - passos_na_janela : i + 1]
        fator = math.prod(1 + valor / 100 for valor in janela)
        linhas.append(
            {
                "data": datas[i],
                "ipca_mensal": valores[i],
                "acumulado_12m": (fator - 1) * 100,
            }
        )

    resultado = pd.DataFrame(linhas, columns=_COLUNAS_IPCA_ACUMULADO)
    resultado = _aplicar_dtypes(resultado, _DTYPES_IPCA_ACUMULADO)
    return resultado.sort_values("data").reset_index(drop=True)


def ptax_mensal(dados: pd.DataFrame) -> pd.DataFrame:
    """Agregação mensal da PTAX venda diária (código 1).

    Agrupa por mês civil (`data.dt.to_period("M")`). Retorna `mes`
    (datetime64[ns], primeiro dia do mês), `media`/`minimo`/`maximo`
    (float64) dos `valor` do mês, `fechamento` (float64, valor do dia
    com maior `data` disponível no mês — não necessariamente o último
    dia civil) e `dias_com_dado` (int64, quantidade de linhas do mês).
    """
    _validar_colunas(dados)

    serie = _filtrar_codigo_ordenado(dados, CODIGO_PTAX_VENDA)
    if serie.empty:
        return _dataframe_vazio(_COLUNAS_PTAX_MENSAL, _DTYPES_PTAX_MENSAL)

    serie["_periodo"] = serie["data"].dt.to_period("M")

    linhas = []
    for periodo, grupo in serie.groupby("_periodo", sort=True):
        # `serie` já vem ordenada por data (via `_filtrar_codigo_ordenado`)
        # e `groupby` preserva essa ordem dentro de cada grupo, então
        # `iloc[-1]` já é o último dia com dado, sem precisar reordenar.
        valores_grupo = grupo["valor"]
        linhas.append(
            {
                "mes": periodo.to_timestamp(),
                "media": valores_grupo.mean(),
                "fechamento": valores_grupo.iloc[-1],
                "minimo": valores_grupo.min(),
                "maximo": valores_grupo.max(),
                "dias_com_dado": len(grupo),
            }
        )

    resultado = pd.DataFrame(linhas, columns=_COLUNAS_PTAX_MENSAL)
    resultado = _aplicar_dtypes(resultado, _DTYPES_PTAX_MENSAL)
    return resultado.sort_values("mes").reset_index(drop=True)
