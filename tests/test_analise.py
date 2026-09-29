"""Testes das análises de negócio (`indicadores.analise`).

Todos os DataFrames de entrada são montados à mão (sem rede, sem
arquivo), exceto o teste de integração leve, que usa
`duckdb.connect(":memory:")`.
"""

from __future__ import annotations

import math
from datetime import datetime

import duckdb
import pandas as pd
import pytest

from indicadores.analise import (
    CODIGO_IPCA_MENSAL,
    CODIGO_PTAX_VENDA,
    CODIGO_SELIC_META,
    ErroAnalise,
    ipca_acumulado_12m,
    mudancas_selic,
    ptax_mensal,
)
from indicadores.persistencia import criar_tabela, gravar, ler

_ATUALIZADO_EM_PADRAO = datetime(2025, 6, 15, 10, 0, 0)  # noqa: DTZ001 (naive de propósito)

_DTYPES_MUDANCAS_SELIC = {
    "data": "datetime64[ns]",
    "valor_anterior": "float64",
    "valor_novo": "float64",
    "variacao_pp": "float64",
}
_DTYPES_IPCA_ACUMULADO = {
    "data": "datetime64[ns]",
    "ipca_mensal": "float64",
    "acumulado_12m": "float64",
}
_DTYPES_PTAX_MENSAL = {
    "mes": "datetime64[ns]",
    "media": "float64",
    "fechamento": "float64",
    "minimo": "float64",
    "maximo": "float64",
    "dias_com_dado": "int64",
}


def _construir_dados(
    linhas: list[dict[str, object]], *, formato: str = "ler"
) -> pd.DataFrame:
    """Monta um DataFrame no formato de `persistencia.ler` (`formato="ler"`,
    5 colunas), de `persistencia.gravar`/`limpeza.limpar` (`formato="gravar"`,
    4 colunas, sem `atualizado_em`) ou mínimo (`formato="minimo"`, só as 3
    colunas que `analise.py` realmente usa: `codigo`, `data`, `valor`).
    """
    linhas_completas = []
    for linha in linhas:
        nova: dict[str, object] = {
            "codigo": linha["codigo"],
            "data": linha["data"],
            "valor": linha["valor"],
        }
        if formato in ("ler", "gravar"):
            nova["serie"] = linha.get("serie", f"serie_{linha['codigo']}")
        if formato == "ler":
            nova["atualizado_em"] = linha.get("atualizado_em", _ATUALIZADO_EM_PADRAO)
        linhas_completas.append(nova)

    if formato == "ler":
        colunas = ["codigo", "serie", "data", "valor", "atualizado_em"]
    elif formato == "gravar":
        colunas = ["codigo", "serie", "data", "valor"]
    else:
        colunas = ["codigo", "data", "valor"]

    df = pd.DataFrame(linhas_completas, columns=colunas)
    df["codigo"] = df["codigo"].astype("int64")
    if "serie" in colunas:
        df["serie"] = df["serie"].astype(object)
    df["data"] = pd.to_datetime(df["data"], dayfirst=True).astype("datetime64[ns]")
    df["valor"] = df["valor"].astype("float64")
    if "atualizado_em" in colunas:
        df["atualizado_em"] = pd.to_datetime(df["atualizado_em"]).astype("datetime64[ns]")
    return df


def _serie(
    codigo: int, datas: list, valores: list[float], *, formato: str = "ler"
) -> pd.DataFrame:
    linhas = [
        {"codigo": codigo, "data": data, "valor": valor}
        for data, valor in zip(datas, valores, strict=True)
    ]
    return _construir_dados(linhas, formato=formato)


def _meses(inicio: str, quantidade: int):
    return list(pd.date_range(inicio, periods=quantidade, freq="MS"))


# ---------------------------------------------------------------------------
# mudancas_selic
# ---------------------------------------------------------------------------


def test_mudancas_selic_detecta_alta():
    datas = ["01/01/2020", "01/02/2020", "01/03/2020"]
    valores = [13.75, 13.75, 14.25]
    dados = _serie(CODIGO_SELIC_META, datas, valores, formato="minimo")

    resultado = mudancas_selic(dados)

    assert len(resultado) == 1
    linha = resultado.iloc[0]
    assert linha["valor_anterior"] == 13.75
    assert linha["valor_novo"] == 14.25
    assert linha["variacao_pp"] == pytest.approx(0.5)


def test_mudancas_selic_detecta_baixa():
    datas = ["01/01/2020", "01/02/2020", "01/03/2020"]
    valores = [14.25, 14.25, 13.75]
    dados = _serie(CODIGO_SELIC_META, datas, valores)

    resultado = mudancas_selic(dados)

    assert len(resultado) == 1
    linha = resultado.iloc[0]
    assert linha["valor_anterior"] == 14.25
    assert linha["valor_novo"] == 13.75
    assert linha["variacao_pp"] == pytest.approx(-0.5)


def test_mudancas_selic_valores_repetidos_nao_geram_linha():
    datas = ["01/01/2020", "01/02/2020", "01/03/2020", "01/04/2020"]
    valores = [13.75, 13.75, 13.75, 13.75]
    dados = _serie(CODIGO_SELIC_META, datas, valores)

    resultado = mudancas_selic(dados)

    assert resultado.empty


def test_mudancas_selic_primeira_observacao_nunca_aparece():
    dados_uma_linha = _serie(CODIGO_SELIC_META, ["01/01/2020"], [13.75])
    resultado_uma_linha = mudancas_selic(dados_uma_linha)
    assert resultado_uma_linha.empty

    dados_com_mudanca = _serie(
        CODIGO_SELIC_META, ["01/01/2020", "01/02/2020"], [13.75, 14.25]
    )
    resultado = mudancas_selic(dados_com_mudanca)
    assert len(resultado) == 1
    assert resultado.iloc[0]["data"] == pd.Timestamp("2020-02-01")
    assert pd.Timestamp("2020-01-01") not in set(resultado["data"])


# ---------------------------------------------------------------------------
# ipca_acumulado_12m
# ---------------------------------------------------------------------------


def test_ipca_acumulado_12m_valor_calculado_a_mao():
    datas = _meses("2020-01-01", 12)
    valores = [1.0] * 12
    dados = _serie(CODIGO_IPCA_MENSAL, datas, valores)

    resultado = ipca_acumulado_12m(dados)

    assert len(resultado) == 1
    assert resultado.iloc[0]["acumulado_12m"] == pytest.approx((1.01**12 - 1) * 100)


def test_ipca_acumulado_12m_janela_incompleta():
    datas = _meses("2020-01-01", 11)
    valores = [1.0] * 11
    dados = _serie(CODIGO_IPCA_MENSAL, datas, valores)

    resultado = ipca_acumulado_12m(dados)

    assert resultado.empty


def test_ipca_acumulado_12m_mes_faltando_no_meio():
    # 6 meses (Jan-Jun/2020), lacuna em Jul/2020, depois 13 meses
    # consecutivos (Ago/2020-Ago/2021) - suficiente para uma janela de
    # 12 consecutivos se formar só bem depois da lacuna.
    datas_antes = _meses("2020-01-01", 6)
    datas_depois = _meses("2020-08-01", 13)
    datas = datas_antes + datas_depois
    valores = [(-1) ** i * (0.1 + 0.05 * i) for i in range(len(datas))]
    dados = _serie(CODIGO_IPCA_MENSAL, datas, valores)

    resultado = ipca_acumulado_12m(dados)

    # Só os 2 últimos meses (cuja janela de 12 consecutivos cai
    # inteiramente depois da lacuna) devem aparecer.
    assert list(resultado["data"]) == [
        pd.Timestamp("2021-07-01"),
        pd.Timestamp("2021-08-01"),
    ]

    # Nenhum mês cuja janela toque a lacuna (Jul/2020) aparece - ex. o
    # primeiro mês possível depois da lacuna já filtrado (Jan/2021 tem
    # janela Fev/2020-Jan/2021, que ainda inclui a lacuna).
    assert pd.Timestamp("2021-01-01") not in set(resultado["data"])

    # Confere o valor do último mês (Ago/2021) pela fórmula, usando os
    # 12 valores dos meses Set/2020-Ago/2021 (índices 7..18 da série
    # completa, já que a lacuna nunca chega a virar uma linha).
    esperado_ago2021 = (math.prod(1 + v / 100 for v in valores[7:19]) - 1) * 100
    linha_ago2021 = resultado[resultado["data"] == pd.Timestamp("2021-08-01")].iloc[0]
    assert linha_ago2021["acumulado_12m"] == pytest.approx(esperado_ago2021)


def test_ipca_acumulado_12m_valores_variados():
    datas = _meses("2020-01-01", 12)
    valores = [0.5, 0.3, -0.1, 0.8, 0.2, 0.4, 0.6, -0.2, 0.1, 0.9, 0.3, 0.7]
    dados = _serie(CODIGO_IPCA_MENSAL, datas, valores)

    resultado = ipca_acumulado_12m(dados)

    esperado = (math.prod(1 + v / 100 for v in valores) - 1) * 100
    assert len(resultado) == 1
    assert resultado.iloc[0]["ipca_mensal"] == valores[-1]
    assert resultado.iloc[0]["acumulado_12m"] == pytest.approx(esperado)


def test_ipca_acumulado_12m_janela_desliza():
    datas = _meses("2020-01-01", 13)
    valores = [0.5, 0.3, -0.1, 0.8, 0.2, 0.4, 0.6, -0.2, 0.1, 0.9, 0.3, 0.7, 1.1]
    dados = _serie(CODIGO_IPCA_MENSAL, datas, valores)

    resultado = ipca_acumulado_12m(dados)

    assert len(resultado) == 2

    esperado_mes_12 = (math.prod(1 + v / 100 for v in valores[0:12]) - 1) * 100
    esperado_mes_13 = (math.prod(1 + v / 100 for v in valores[1:13]) - 1) * 100

    linha_mes_12 = resultado.iloc[0]
    linha_mes_13 = resultado.iloc[1]
    assert linha_mes_12["data"] == datas[11]
    assert linha_mes_13["data"] == datas[12]
    assert linha_mes_12["acumulado_12m"] == pytest.approx(esperado_mes_12)
    assert linha_mes_13["acumulado_12m"] == pytest.approx(esperado_mes_13)
    assert esperado_mes_12 != pytest.approx(esperado_mes_13)


# ---------------------------------------------------------------------------
# ptax_mensal
# ---------------------------------------------------------------------------


def test_ptax_mensal_mes_completo():
    datas = list(pd.date_range("2020-01-01", periods=20, freq="D"))
    valores = [5.0 + 0.01 * i for i in range(20)]
    dados = _serie(CODIGO_PTAX_VENDA, datas, valores)

    resultado = ptax_mensal(dados)

    assert len(resultado) == 1
    linha = resultado.iloc[0]
    assert linha["mes"] == pd.Timestamp("2020-01-01")
    assert linha["dias_com_dado"] == 20
    assert linha["media"] == pytest.approx(sum(valores) / len(valores))
    assert linha["minimo"] == pytest.approx(min(valores))
    assert linha["maximo"] == pytest.approx(max(valores))
    assert linha["fechamento"] == pytest.approx(valores[-1])


def test_ptax_mensal_mes_parcial():
    datas = list(pd.date_range("2020-02-01", periods=5, freq="D"))
    valores = [5.1, 5.2, 5.15, 5.05, 5.3]
    dados = _serie(CODIGO_PTAX_VENDA, datas, valores)

    resultado = ptax_mensal(dados)

    assert len(resultado) == 1
    assert resultado.iloc[0]["dias_com_dado"] == 5
    assert list(resultado.columns) == list(_DTYPES_PTAX_MENSAL)


def test_ptax_mensal_fechamento_ultimo_dia_util():
    # Mês de 31 dias (Março/2020); só os dias 29 (útil) e 15 têm dado,
    # o dia 31 (fim de semana simulado) não tem linha. Ordem de entrada
    # deliberadamente invertida (dia 29 antes do dia 15), para provar
    # que `fechamento` vem da maior *data*, não da última linha da
    # lista de entrada.
    dados = _serie(
        CODIGO_PTAX_VENDA,
        [pd.Timestamp("2020-03-29"), pd.Timestamp("2020-03-15")],
        [5.20, 5.10],
    )

    resultado = ptax_mensal(dados)

    assert len(resultado) == 1
    assert resultado.iloc[0]["fechamento"] == pytest.approx(5.20)


# ---------------------------------------------------------------------------
# Série ausente/vazia -> DataFrame vazio com dtypes documentados
# ---------------------------------------------------------------------------


def test_mudancas_selic_serie_ausente_retorna_vazio_com_dtypes():
    dados = _serie(CODIGO_IPCA_MENSAL, ["01/01/2020"], [0.5])  # outro código

    resultado = mudancas_selic(dados)

    assert resultado.empty
    assert list(resultado.columns) == list(_DTYPES_MUDANCAS_SELIC)
    for coluna, dtype in _DTYPES_MUDANCAS_SELIC.items():
        assert str(resultado[coluna].dtype) == dtype


def test_ipca_acumulado_12m_serie_ausente_retorna_vazio_com_dtypes():
    dados = _serie(CODIGO_SELIC_META, ["01/01/2020"], [13.75])  # outro código

    resultado = ipca_acumulado_12m(dados)

    assert resultado.empty
    assert list(resultado.columns) == list(_DTYPES_IPCA_ACUMULADO)
    for coluna, dtype in _DTYPES_IPCA_ACUMULADO.items():
        assert str(resultado[coluna].dtype) == dtype


def test_ptax_mensal_serie_ausente_retorna_vazio_com_dtypes():
    dados = _serie(CODIGO_SELIC_META, ["01/01/2020"], [13.75])  # outro código

    resultado = ptax_mensal(dados)

    assert resultado.empty
    assert list(resultado.columns) == list(_DTYPES_PTAX_MENSAL)
    for coluna, dtype in _DTYPES_PTAX_MENSAL.items():
        assert str(resultado[coluna].dtype) == dtype


# ---------------------------------------------------------------------------
# Colunas obrigatórias faltando -> ErroAnalise
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("coluna_faltando", ["codigo", "data", "valor"])
@pytest.mark.parametrize(
    "funcao", [mudancas_selic, ipca_acumulado_12m, ptax_mensal]
)
def test_colunas_faltando_levanta_erro_analise(funcao, coluna_faltando):
    dados = _serie(CODIGO_SELIC_META, ["01/01/2020"], [13.75], formato="minimo")
    dados_incompletos = dados.drop(columns=[coluna_faltando])

    with pytest.raises(ErroAnalise):
        funcao(dados_incompletos)


# ---------------------------------------------------------------------------
# Entrada fora de ordem produz o mesmo resultado
# ---------------------------------------------------------------------------


def _casos_entrada_fora_de_ordem():
    # Selic: 4 pontos, com duas mudanças (alta e depois baixa).
    dados_selic = _serie(
        CODIGO_SELIC_META,
        ["01/01/2020", "01/02/2020", "01/03/2020", "01/04/2020"],
        [13.75, 13.75, 14.25, 13.75],
    )

    # IPCA: 13 meses consecutivos com valores variados (janela desliza).
    datas_ipca = _meses("2020-01-01", 13)
    valores_ipca = [0.5, 0.3, -0.1, 0.8, 0.2, 0.4, 0.6, -0.2, 0.1, 0.9, 0.3, 0.7, 1.1]
    dados_ipca = _serie(CODIGO_IPCA_MENSAL, datas_ipca, valores_ipca)

    # PTAX: 2 meses, com vários dias cada.
    datas_ptax = list(pd.date_range("2020-01-01", periods=5, freq="D")) + list(
        pd.date_range("2020-02-01", periods=5, freq="D")
    )
    valores_ptax = [5.0, 5.1, 5.2, 5.15, 5.05, 5.3, 5.25, 5.4, 5.35, 5.45]
    dados_ptax = _serie(CODIGO_PTAX_VENDA, datas_ptax, valores_ptax)

    return [
        (mudancas_selic, dados_selic),
        (ipca_acumulado_12m, dados_ipca),
        (ptax_mensal, dados_ptax),
    ]


@pytest.mark.parametrize(
    ("funcao", "dados_ordenados"),
    _casos_entrada_fora_de_ordem(),
    ids=["mudancas_selic", "ipca_acumulado_12m", "ptax_mensal"],
)
def test_entrada_fora_de_ordem_produz_mesmo_resultado(funcao, dados_ordenados):
    dados_embaralhados = dados_ordenados.sample(frac=1, random_state=42).reset_index(
        drop=True
    )
    # Garante que a ordem realmente mudou (senão o teste não provaria nada).
    assert list(dados_embaralhados["data"]) != list(dados_ordenados["data"])

    resultado_ordenado = funcao(dados_ordenados)
    resultado_embaralhado = funcao(dados_embaralhados)

    pd.testing.assert_frame_equal(resultado_ordenado, resultado_embaralhado)


# ---------------------------------------------------------------------------
# Dtypes exatos da saída das três funções, com dados reais
# ---------------------------------------------------------------------------


def test_dtypes_da_saida_das_tres_funcoes():
    dados_selic = _serie(
        CODIGO_SELIC_META, ["01/01/2020", "01/02/2020"], [13.75, 14.25]
    )
    dados_ipca = _serie(CODIGO_IPCA_MENSAL, _meses("2020-01-01", 12), [1.0] * 12)
    dados_ptax = _serie(
        CODIGO_PTAX_VENDA,
        list(pd.date_range("2020-01-01", periods=5, freq="D")),
        [5.0, 5.1, 5.2, 5.15, 5.05],
    )

    resultado_selic = mudancas_selic(dados_selic)
    resultado_ipca = ipca_acumulado_12m(dados_ipca)
    resultado_ptax = ptax_mensal(dados_ptax)

    assert not resultado_selic.empty
    assert not resultado_ipca.empty
    assert not resultado_ptax.empty

    for coluna, dtype in _DTYPES_MUDANCAS_SELIC.items():
        assert str(resultado_selic[coluna].dtype) == dtype
    for coluna, dtype in _DTYPES_IPCA_ACUMULADO.items():
        assert str(resultado_ipca[coluna].dtype) == dtype
    for coluna, dtype in _DTYPES_PTAX_MENSAL.items():
        assert str(resultado_ptax[coluna].dtype) == dtype


# ---------------------------------------------------------------------------
# Nenhuma das três funções altera o DataFrame de entrada.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("funcao", "codigo"),
    [
        (mudancas_selic, CODIGO_SELIC_META),
        (ipca_acumulado_12m, CODIGO_IPCA_MENSAL),
        (ptax_mensal, CODIGO_PTAX_VENDA),
    ],
)
def test_funcoes_nao_alteram_dataframe_de_entrada(funcao, codigo):
    datas = _meses("2020-01-01", 13)
    valores = [0.5, 0.3, -0.1, 0.8, 0.2, 0.4, 0.6, -0.2, 0.1, 0.9, 0.3, 0.7, 1.1]
    dados = _serie(codigo, datas, valores)
    copia_antes = dados.copy()

    funcao(dados)

    pd.testing.assert_frame_equal(dados, copia_antes)


# ---------------------------------------------------------------------------
# Integração leve: gravar -> ler -> as três análises
# ---------------------------------------------------------------------------


def test_integracao_gravar_ler_e_analisar():
    conexao = duckdb.connect(":memory:")
    try:
        criar_tabela(conexao)

        datas_selic = ["01/01/2020", "01/02/2020", "01/03/2020"]
        valores_selic = [13.75, 13.75, 14.25]
        dados_selic = _serie(
            CODIGO_SELIC_META, datas_selic, valores_selic, formato="gravar"
        )

        datas_ipca = _meses("2020-01-01", 13)
        valores_ipca = [0.5, 0.3, -0.1, 0.8, 0.2, 0.4, 0.6, -0.2, 0.1, 0.9, 0.3, 0.7, 1.1]
        dados_ipca = _serie(
            CODIGO_IPCA_MENSAL, datas_ipca, valores_ipca, formato="gravar"
        )

        datas_ptax = list(pd.date_range("2020-01-01", periods=5, freq="D"))
        valores_ptax = [5.0, 5.1, 5.2, 5.15, 5.05]
        dados_ptax = _serie(
            CODIGO_PTAX_VENDA, datas_ptax, valores_ptax, formato="gravar"
        )

        dados_completos = pd.concat(
            [dados_selic, dados_ipca, dados_ptax], ignore_index=True
        )

        gravar(conexao, dados_completos, atualizado_em=_ATUALIZADO_EM_PADRAO)

        lido = ler(conexao)

        resultado_selic = mudancas_selic(lido)
        resultado_ipca = ipca_acumulado_12m(lido)
        resultado_ptax = ptax_mensal(lido)
    finally:
        conexao.close()

    assert not resultado_selic.empty
    assert len(resultado_selic) == 1
    assert resultado_selic.iloc[0]["variacao_pp"] == pytest.approx(0.5)

    assert not resultado_ipca.empty
    assert len(resultado_ipca) == 2
    for coluna, dtype in _DTYPES_IPCA_ACUMULADO.items():
        assert str(resultado_ipca[coluna].dtype) == dtype

    assert not resultado_ptax.empty
    assert resultado_ptax.iloc[0]["dias_com_dado"] == 5
    for coluna, dtype in _DTYPES_PTAX_MENSAL.items():
        assert str(resultado_ptax[coluna].dtype) == dtype
