"""Testes da orquestração pura (`indicadores.pipeline`).

`executar` é testada com `httpx.MockTransport` e
`duckdb.connect(":memory:")`, sem rede real. `formatar_resumo` é testada
com `ResumoPipeline` construído à mão, sem rede/banco.
"""

from __future__ import annotations

from datetime import date, datetime

import duckdb
import httpx

from indicadores.extracao import SGS_BASE_URL
from indicadores.limpeza import SerieIgnorada
from indicadores.persistencia import ResultadoGravacao, ler
from indicadores.pipeline import ResumoPipeline, executar, formatar_resumo


def _construir_handler(respostas: dict[int, object]):
    """Handler de `MockTransport` que escolhe a resposta pelo código na URL.

    `respostas[codigo]` é uma lista de itens `{"data": ..., "valor": ...}`
    (sucesso, HTTP 200) ou um `int` (status HTTP, ex. 404/500).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        for codigo, resposta in respostas.items():
            if f"sgs.{codigo}/dados" in request.url.path:
                if isinstance(resposta, int):
                    return httpx.Response(resposta, text="erro")
                return httpx.Response(200, json=resposta)
        raise AssertionError(f"código inesperado na URL: {request.url.path}")

    return handler


def _cliente(respostas: dict[int, object]) -> httpx.Client:
    return httpx.Client(
        base_url=SGS_BASE_URL, transport=httpx.MockTransport(_construir_handler(respostas))
    )


# ---------------------------------------------------------------------------
# 1: executar grava séries bem-sucedidas
# ---------------------------------------------------------------------------


def test_executar_grava_series_bem_sucedidas():
    respostas = {
        432: [
            {"data": "01/01/2020", "valor": "13,75"},
            {"data": "02/01/2020", "valor": "14,00"},
        ],
        433: [{"data": "01/01/2020", "valor": "0,25"}],
    }
    client = _cliente(respostas)
    con = duckdb.connect(":memory:")
    try:
        resumo = executar(
            conexao=con,
            client=client,
            codigos=[432, 433],
            data_referencia=date(2025, 6, 15),
            esperar=lambda s: None,
        )
    finally:
        client.close()
        con.close()

    assert resumo.gravacoes[432] == ResultadoGravacao(inseridos=2, atualizados=0, total=2)
    assert resumo.gravacoes[433] == ResultadoGravacao(inseridos=1, atualizados=0, total=1)
    assert resumo.nomes == {432: "selic_meta", 433: "ipca_mensal"}


# ---------------------------------------------------------------------------
# 2: executar cria a tabela automaticamente
# ---------------------------------------------------------------------------


def test_executar_cria_tabela_automaticamente():
    respostas = {432: [{"data": "01/01/2020", "valor": "13,75"}]}
    client = _cliente(respostas)
    con = duckdb.connect(":memory:")  # sem `criar_tabela` chamada antes.
    try:
        resumo = executar(
            conexao=con,
            client=client,
            codigos=[432],
            data_referencia=date(2025, 6, 15),
            esperar=lambda s: None,
        )
    finally:
        client.close()
        con.close()

    assert resumo.gravacoes[432].total == 1


# ---------------------------------------------------------------------------
# 3: executar isola série com falha de extração
# ---------------------------------------------------------------------------


def test_executar_isola_serie_com_falha_de_extracao():
    respostas = {
        432: [{"data": "01/01/2020", "valor": "13,75"}],
        433: 500,
    }
    client = _cliente(respostas)
    con = duckdb.connect(":memory:")
    try:
        resumo = executar(
            conexao=con,
            client=client,
            codigos=[432, 433],
            data_referencia=date(2025, 6, 15),
            esperar=lambda s: None,
        )
    finally:
        client.close()
        con.close()

    assert 432 in resumo.gravacoes
    assert len(resumo.series_ignoradas) == 1
    ignorada = resumo.series_ignoradas[0]
    assert ignorada.codigo == 433
    assert "status HTTP 500" in ignorada.motivo


# ---------------------------------------------------------------------------
# 4: executar propaga descartes da limpeza
# ---------------------------------------------------------------------------


def test_executar_propaga_descartes_da_limpeza():
    respostas = {
        432: [
            {"data": "01/01/2020", "valor": "13,75"},
            {"data": "02/01/2020", "valor": "abc"},
        ],
    }
    client = _cliente(respostas)
    con = duckdb.connect(":memory:")
    try:
        resumo = executar(
            conexao=con,
            client=client,
            codigos=[432],
            data_referencia=date(2025, 6, 15),
            esperar=lambda s: None,
        )
    finally:
        client.close()
        con.close()

    assert len(resumo.descartes) == 1
    assert resumo.descartes[0].motivo == "valor_invalido"


# ---------------------------------------------------------------------------
# 5: reexecução atualiza em vez de duplicar
# ---------------------------------------------------------------------------


def test_executar_reexecucao_atualiza_em_vez_de_duplicar():
    respostas = {432: [{"data": "01/01/2020", "valor": "13,75"}]}
    con = duckdb.connect(":memory:")
    client = _cliente(respostas)
    try:
        executar(
            conexao=con,
            client=client,
            codigos=[432],
            data_referencia=date(2025, 6, 15),
            esperar=lambda s: None,
        )
        resumo2 = executar(
            conexao=con,
            client=client,
            codigos=[432],
            data_referencia=date(2025, 6, 15),
            esperar=lambda s: None,
        )
    finally:
        client.close()
        con.close()

    gravacao = resumo2.gravacoes[432]
    assert gravacao.inseridos == 0
    assert gravacao.atualizados == gravacao.total == 1


# ---------------------------------------------------------------------------
# 6: um único atualizado_em por chamada, mesmo com várias séries
# ---------------------------------------------------------------------------


def test_executar_usa_atualizado_em_unico_por_chamada(monkeypatch):
    chamadas: list[datetime] = []

    class DatetimeFalso:
        @staticmethod
        def now(fuso=None):
            valor = datetime(2025, 6, 15, 9, 0, len(chamadas), tzinfo=fuso)
            chamadas.append(valor)
            return valor

    monkeypatch.setattr("indicadores.pipeline.datetime", DatetimeFalso)

    respostas = {
        432: [{"data": "01/01/2020", "valor": "13,75"}],
        433: [{"data": "01/01/2020", "valor": "0,25"}],
    }
    client = _cliente(respostas)
    con = duckdb.connect(":memory:")
    try:
        executar(
            conexao=con,
            client=client,
            codigos=[432, 433],
            data_referencia=date(2025, 6, 15),
            esperar=lambda s: None,
        )
        lido = ler(con)
    finally:
        client.close()
        con.close()

    # `datetime.now` só deve ter sido chamado uma vez para a chamada
    # inteira (não uma vez por série).
    assert len(chamadas) == 1
    valores_atualizado_em = set(lido["atualizado_em"])
    assert len(valores_atualizado_em) == 1


# ---------------------------------------------------------------------------
# 7: série sem nenhuma linha válida não aparece em gravacoes
# ---------------------------------------------------------------------------


def test_executar_serie_sem_linha_valida_nao_aparece_em_gravacoes():
    respostas = {432: [{"data": "01/01/2020", "valor": "abc"}]}
    client = _cliente(respostas)
    con = duckdb.connect(":memory:")
    try:
        resumo = executar(
            conexao=con,
            client=client,
            codigos=[432],
            data_referencia=date(2025, 6, 15),
            esperar=lambda s: None,
        )
    finally:
        client.close()
        con.close()

    assert 432 not in resumo.gravacoes
    assert 432 not in resumo.nomes
    assert not any(ignorada.codigo == 432 for ignorada in resumo.series_ignoradas)
    assert len(resumo.descartes) == 1
    assert resumo.descartes[0].motivo == "valor_invalido"


# ---------------------------------------------------------------------------
# 8: formatar_resumo lista séries gravadas e nomes
# ---------------------------------------------------------------------------


def test_formatar_resumo_lista_series_gravadas_e_nomes():
    resumo = ResumoPipeline(
        gravacoes={
            432: ResultadoGravacao(inseridos=12, atualizados=1808, total=1820),
            433: ResultadoGravacao(inseridos=3, atualizados=57, total=60),
        },
        nomes={432: "selic_meta", 433: "ipca_mensal"},
        descartes=[],
        series_ignoradas=[],
    )

    texto = formatar_resumo(resumo)

    assert "432 (selic_meta): 12 inseridos, 1808 atualizados" in texto
    assert "433 (ipca_mensal): 3 inseridos, 57 atualizados" in texto


# ---------------------------------------------------------------------------
# 9: os três motivos de descarte sempre aparecem, mesmo com contagem zero
# ---------------------------------------------------------------------------


def test_formatar_resumo_lista_tres_motivos_de_descarte_mesmo_com_zero():
    resumo = ResumoPipeline(gravacoes={}, nomes={}, descartes=[], series_ignoradas=[])

    texto = formatar_resumo(resumo)

    assert "  data_invalida: 0" in texto
    assert "  valor_invalido: 0" in texto
    assert "  data_duplicada: 0" in texto


# ---------------------------------------------------------------------------
# 10: séries ignoradas usam nome via fallback quando fora de SERIES
# ---------------------------------------------------------------------------


def test_formatar_resumo_lista_series_ignoradas_com_nome_via_fallback():
    resumo = ResumoPipeline(
        gravacoes={},
        nomes={},
        descartes=[],
        series_ignoradas=[SerieIgnorada(codigo=999, motivo="erro de exemplo")],
    )

    texto = formatar_resumo(resumo)

    assert "999 (serie_999): erro de exemplo" in texto


# ---------------------------------------------------------------------------
# 11: resumo vazio mostra "(nenhuma)" nas duas seções
# ---------------------------------------------------------------------------


def test_formatar_resumo_vazio_mostra_nenhuma():
    resumo = ResumoPipeline(gravacoes={}, nomes={}, descartes=[], series_ignoradas=[])

    texto = formatar_resumo(resumo)

    assert texto.count("  (nenhuma)") == 2
