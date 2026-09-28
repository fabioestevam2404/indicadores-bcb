"""Testes do módulo de limpeza (Silver, sem I/O).

Nenhum teste aqui acessa rede: as entradas são `ResultadoSerie`
construídos à mão, importados de `indicadores.extracao`.
"""

from __future__ import annotations

import logging

import pandas as pd
import pytest

from indicadores.extracao import SERIES, ErroHTTP, ResultadoSerie
from indicadores.limpeza import COLUNAS, _parse_data, _parse_valor, limpar

DTYPES_ESPERADOS = {
    "codigo": "int64",
    "serie": "object",
    "data": "datetime64[ns]",
    "valor": "float64",
}


def _checar_dtypes(dados: pd.DataFrame) -> None:
    for coluna, dtype_esperado in DTYPES_ESPERADOS.items():
        assert str(dados[coluna].dtype) == dtype_esperado


# ---------------------------------------------------------------------------
# 1-2: conversão básica de data/valor via `limpar`
# ---------------------------------------------------------------------------


def test_limpar_converte_data_e_valor_basicos():
    resultados = {
        432: ResultadoSerie(
            codigo=432,
            dados=[{"data": "01/01/2020", "valor": "13,75"}],
            erro=None,
        ),
    }

    resultado = limpar(resultados)

    assert len(resultado.dados) == 1
    linha = resultado.dados.iloc[0]
    assert linha["data"] == pd.Timestamp("2020-01-01")
    assert linha["valor"] == 13.75
    assert str(resultado.dados["valor"].dtype) == "float64"


def test_limpar_usa_formato_explicito_nao_infere_dia_mes():
    resultados = {
        432: ResultadoSerie(
            codigo=432,
            dados=[{"data": "13/02/2020", "valor": "1,0"}],
            erro=None,
        ),
    }

    resultado = limpar(resultados)

    assert len(resultado.dados) == 1
    assert resultado.dados.iloc[0]["data"] == pd.Timestamp("2020-02-13")


# ---------------------------------------------------------------------------
# 3-8 e extra: `_parse_valor` — casos válidos, agrupados via parametrize.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        pytest.param("13,75", 13.75, id="test_parse_valor_virgula_simples"),
        pytest.param(
            "1.234,56", 1234.56, id="test_parse_valor_milhar_com_ponto_e_virgula"
        ),
        pytest.param(
            "1.234.567,89", 1234567.89, id="test_parse_valor_milhar_multiplo"
        ),
        pytest.param("1.234", 1234.0, id="test_parse_valor_milhar_sem_virgula"),
        pytest.param(
            "5.25", 5.25, id="test_parse_valor_ponto_decimal_isolado_nao_e_milhar"
        ),
        pytest.param("-1,5", -1.5, id="test_parse_valor_negativo"),
        pytest.param("10", 10.0, id="test_parse_valor_inteiro_simples"),
    ],
)
def test_parse_valor_casos_validos(bruto: str, esperado: float):
    assert _parse_valor(bruto) == esperado


# ---------------------------------------------------------------------------
# 9-14: `_parse_valor` — casos inválidos, agrupados via parametrize.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bruto",
    [
        pytest.param("", id="test_parse_valor_vazio_e_traco_invalidos_vazio"),
        pytest.param("-", id="test_parse_valor_vazio_e_traco_invalidos_traco"),
        pytest.param("N/D", id="test_parse_valor_texto_nao_numerico_invalido_nd"),
        pytest.param("abc", id="test_parse_valor_texto_nao_numerico_invalido_abc"),
        pytest.param("nan", id="test_parse_valor_nao_finito_invalido_nan"),
        pytest.param("inf", id="test_parse_valor_nao_finito_invalido_inf"),
        pytest.param("-inf", id="test_parse_valor_nao_finito_invalido_neg_inf"),
        pytest.param(
            "Infinity", id="test_parse_valor_nao_finito_invalido_infinity"
        ),
        pytest.param(
            "1,234.56", id="test_parse_valor_formato_americano_invalido"
        ),
        pytest.param(
            "1.23.4", id="test_parse_valor_pontuacao_malformada_invalida"
        ),
        pytest.param(
            "13, 75", id="test_parse_valor_espaco_apos_virgula_invalido"
        ),
        # `float()` nativo aceita dígitos arábico-indianos (ex.:
        # `float("١٢")  == 12.0`); os padrões usam `re.ASCII`
        # justamente para rejeitar esse caso antes de chegar ao `float()`.
        pytest.param(
            "١٢,٣",
            id="test_parse_valor_digitos_nao_ascii_invalido_re_ascii",
        ),
    ],
)
def test_parse_valor_casos_invalidos(bruto: str):
    assert _parse_valor(bruto) is None


# ---------------------------------------------------------------------------
# 15: `_parse_data` — casos inválidos
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bruto", ["", "31/02/2020", "00/01/2020"])
def test_parse_data_invalida_ou_impossivel(bruto: str):
    assert _parse_data(bruto) is None


# ---------------------------------------------------------------------------
# 16: espaços em branco removidos antes da validação
# ---------------------------------------------------------------------------


def test_limpar_espacos_em_branco_sao_removidos():
    resultados = {
        432: ResultadoSerie(
            codigo=432,
            dados=[{"data": " 01/01/2020 ", "valor": " 13,75 "}],
            erro=None,
        ),
    }

    resultado = limpar(resultados)

    assert resultado.descartes == []
    assert len(resultado.dados) == 1
    assert resultado.dados.iloc[0]["data"] == pd.Timestamp("2020-01-01")
    assert resultado.dados.iloc[0]["valor"] == 13.75


# ---------------------------------------------------------------------------
# 17: prioridade de motivo quando data e valor são inválidos ao mesmo tempo
# ---------------------------------------------------------------------------


def test_limpar_prioriza_motivo_data_invalida_quando_ambos_invalidos():
    resultados = {
        432: ResultadoSerie(
            codigo=432,
            dados=[{"data": "31/02/2020", "valor": "abc"}],
            erro=None,
        ),
    }

    resultado = limpar(resultados)

    assert resultado.dados.empty
    assert len(resultado.descartes) == 1
    assert resultado.descartes[0].motivo == "data_invalida"


# ---------------------------------------------------------------------------
# 18: linha inválida não descarta a série inteira
# ---------------------------------------------------------------------------


def test_limpar_linha_invalida_nao_descarta_serie_inteira():
    resultados = {
        432: ResultadoSerie(
            codigo=432,
            dados=[
                {"data": "01/01/2020", "valor": "13,75"},
                {"data": "02/01/2020", "valor": "abc"},
                {"data": "03/01/2020", "valor": "14,00"},
            ],
            erro=None,
        ),
    }

    resultado = limpar(resultados)

    assert len(resultado.dados) == 2
    assert len(resultado.descartes) == 1
    assert resultado.descartes[0].motivo == "valor_invalido"


# ---------------------------------------------------------------------------
# 19-20: deduplicação de data entre linhas válidas
# ---------------------------------------------------------------------------


def test_limpar_data_duplicada_entre_linhas_validas_mantem_primeira():
    resultados = {
        432: ResultadoSerie(
            codigo=432,
            dados=[
                {"data": "01/01/2020", "valor": "13,75"},
                {"data": "01/01/2020", "valor": "14,00"},
            ],
            erro=None,
        ),
    }

    resultado = limpar(resultados)

    assert len(resultado.dados) == 1
    assert resultado.dados.iloc[0]["valor"] == 13.75
    assert len(resultado.descartes) == 1
    assert resultado.descartes[0].motivo == "data_duplicada"
    assert resultado.descartes[0].valor_bruto == "14,00"


def test_limpar_data_duplicada_quando_primeira_invalida_mantem_segunda_valida():
    resultados = {
        432: ResultadoSerie(
            codigo=432,
            dados=[
                {"data": "01/01/2020", "valor": "abc"},
                {"data": "01/01/2020", "valor": "14,00"},
            ],
            erro=None,
        ),
    }

    resultado = limpar(resultados)

    assert len(resultado.dados) == 1
    assert resultado.dados.iloc[0]["valor"] == 14.00
    assert len(resultado.descartes) == 1
    assert resultado.descartes[0].motivo == "valor_invalido"
    assert resultado.descartes[0].valor_bruto == "abc"


# ---------------------------------------------------------------------------
# 21: série com falha é ignorada sem interromper as demais
# ---------------------------------------------------------------------------


def test_limpar_serie_com_falha_e_ignorada_sem_interromper_outras():
    erro = ErroHTTP(433, 500, "erro interno")
    resultados = {
        432: ResultadoSerie(
            codigo=432,
            dados=[{"data": "01/01/2020", "valor": "13,75"}],
            erro=None,
        ),
        433: ResultadoSerie(codigo=433, dados=None, erro=erro),
    }

    resultado = limpar(resultados)

    assert len(resultado.dados) == 1
    assert (resultado.dados["codigo"] == 432).all()
    assert len(resultado.series_ignoradas) == 1
    assert resultado.series_ignoradas[0].codigo == 433
    assert resultado.series_ignoradas[0].motivo == str(erro)


# ---------------------------------------------------------------------------
# 22-23: entrada vazia / todas as séries com falha
# ---------------------------------------------------------------------------


def test_limpar_entrada_vazia_retorna_dataframe_vazio_com_dtypes():
    resultado = limpar({})

    assert resultado.dados.empty
    assert list(resultado.dados.columns) == COLUNAS
    _checar_dtypes(resultado.dados)
    assert resultado.descartes == []
    assert resultado.series_ignoradas == []


def test_limpar_todas_series_falharam_retorna_dataframe_vazio():
    resultados = {
        432: ResultadoSerie(codigo=432, dados=None, erro=ErroHTTP(432, 500, "x")),
        433: ResultadoSerie(codigo=433, dados=None, erro=ErroHTTP(433, 500, "y")),
    }

    resultado = limpar(resultados)

    assert resultado.dados.empty
    _checar_dtypes(resultado.dados)
    codigos_ignorados = {item.codigo for item in resultado.series_ignoradas}
    assert codigos_ignorados == {432, 433}


# ---------------------------------------------------------------------------
# 24: código fora do registro SERIES usa nome de fallback
# ---------------------------------------------------------------------------


def test_limpar_codigo_fora_do_registro_usa_nome_fallback():
    assert 999 not in SERIES
    resultados = {
        999: ResultadoSerie(
            codigo=999,
            dados=[{"data": "01/01/2020", "valor": "1,0"}],
            erro=None,
        ),
    }

    resultado = limpar(resultados)

    assert resultado.dados.iloc[0]["serie"] == "serie_999"


# ---------------------------------------------------------------------------
# 25: ordenação final por codigo e data, índice resetado
# ---------------------------------------------------------------------------


def test_limpar_ordena_por_codigo_e_data_com_indice_resetado():
    resultados = {
        433: ResultadoSerie(
            codigo=433,
            dados=[
                {"data": "01/02/2020", "valor": "1,0"},
                {"data": "01/01/2020", "valor": "2,0"},
            ],
            erro=None,
        ),
        432: ResultadoSerie(
            codigo=432,
            dados=[
                {"data": "01/03/2020", "valor": "3,0"},
                {"data": "01/01/2020", "valor": "4,0"},
            ],
            erro=None,
        ),
    }

    resultado = limpar(resultados)
    dados = resultado.dados

    assert list(dados.columns) == COLUNAS
    assert list(dados["codigo"]) == [432, 432, 433, 433]
    assert list(dados["data"]) == [
        pd.Timestamp("2020-01-01"),
        pd.Timestamp("2020-03-01"),
        pd.Timestamp("2020-01-01"),
        pd.Timestamp("2020-02-01"),
    ]
    assert list(dados.index) == list(range(len(dados)))


# ---------------------------------------------------------------------------
# 26-27: logging via caplog
# ---------------------------------------------------------------------------


def test_limpar_loga_descarte_com_caplog(caplog: pytest.LogCaptureFixture):
    resultados = {
        432: ResultadoSerie(
            codigo=432,
            dados=[{"data": "01/01/2020", "valor": "abc"}],
            erro=None,
        ),
    }

    with caplog.at_level(logging.WARNING, logger="indicadores.limpeza"):
        limpar(resultados)

    mensagens = [registro.getMessage() for registro in caplog.records]
    assert any("432" in mensagem and "valor_invalido" in mensagem for mensagem in mensagens)


def test_limpar_loga_serie_ignorada_com_caplog(caplog: pytest.LogCaptureFixture):
    resultados = {
        433: ResultadoSerie(codigo=433, dados=None, erro=ErroHTTP(433, 500, "x")),
    }

    with caplog.at_level(logging.WARNING, logger="indicadores.limpeza"):
        limpar(resultados)

    mensagens = [registro.getMessage() for registro in caplog.records]
    assert any("433" in mensagem for mensagem in mensagens)


# ---------------------------------------------------------------------------
# 28: dtypes da saída com dados reais
# ---------------------------------------------------------------------------


def test_limpar_dtypes_da_saida():
    resultados = {
        432: ResultadoSerie(
            codigo=432,
            dados=[{"data": "01/01/2020", "valor": "13,75"}],
            erro=None,
        ),
    }

    resultado = limpar(resultados)

    _checar_dtypes(resultado.dados)
