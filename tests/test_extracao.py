"""Testes do módulo de extração (SGS/BCB).

Nenhum teste aqui acessa a internet: toda chamada HTTP é feita através de
`httpx.MockTransport`. Nos testes de retry, `esperar` é sempre um no-op.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from indicadores.extracao import (
    FUSO_BRASILIA,
    MAX_TENTATIVAS,
    SERIES,
    SGS_BASE_URL,
    SGS_TIMEOUT,
    ErroConexao,
    ErroHTTP,
    ErroRespostaInvalida,
    ErroSerieVazia,
    _hoje,
    buscar_serie,
    buscar_series,
    calcular_intervalo,
    criar_client,
)


def _no_espera(segundos: float) -> None:
    """No-op usado no lugar de `time.sleep` nos testes de retry."""


# ---------------------------------------------------------------------------
# calcular_intervalo
# ---------------------------------------------------------------------------


def test_calcular_intervalo_janela_padrao_de_5_anos():
    referencia = date(2025, 6, 15)

    data_inicial, data_final = calcular_intervalo(referencia)

    assert data_final == "15/06/2025"
    assert data_inicial == "15/06/2020"


def test_calcular_intervalo_29_fevereiro_ano_bissexto_ajusta_para_28():
    referencia = date(2024, 2, 29)

    data_inicial, data_final = calcular_intervalo(referencia, janela_anos=5)

    assert data_final == "29/02/2024"
    assert data_inicial == "28/02/2019"


def test_calcular_intervalo_formato_dd_mm_aaaa():
    referencia = date(2025, 1, 5)

    data_inicial, data_final = calcular_intervalo(referencia)

    assert data_final == "05/01/2025"
    assert data_inicial == "05/01/2020"


# ---------------------------------------------------------------------------
# buscar_serie — montagem de request e retorno bruto
# ---------------------------------------------------------------------------


def test_buscar_serie_monta_url_e_parametros_corretos():
    requisicoes: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requisicoes.append(request)
        return httpx.Response(200, json=[{"data": "01/01/2020", "valor": "13,75"}])

    client = httpx.Client(base_url=SGS_BASE_URL, transport=httpx.MockTransport(handler))
    try:
        buscar_serie(432, client=client, data_referencia=date(2025, 6, 15))
    finally:
        client.close()

    assert len(requisicoes) == 1
    request = requisicoes[0]
    assert request.url.path == "/dados/serie/bcdata.sgs.432/dados"
    params = request.url.params
    assert params["formato"] == "json"
    assert params["dataInicial"] == "15/06/2020"
    assert params["dataFinal"] == "15/06/2025"
    assert request.headers["accept"] == "application/json"


def test_buscar_serie_retorna_dados_brutos_sem_alteracao():
    dados_originais = [{"data": "01/01/2020", "valor": "13,75"}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=dados_originais)

    client = httpx.Client(base_url=SGS_BASE_URL, transport=httpx.MockTransport(handler))
    try:
        resultado = buscar_serie(432, client=client, data_referencia=date(2025, 6, 15))
    finally:
        client.close()

    assert resultado == dados_originais
    assert isinstance(resultado[0]["valor"], str)
    assert resultado[0]["valor"] == "13,75"
    assert resultado[0]["data"] == "01/01/2020"


# ---------------------------------------------------------------------------
# buscar_serie — erros e retries
# ---------------------------------------------------------------------------


def test_buscar_serie_erro_http_4xx_nao_tenta_novamente():
    chamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request)
        return httpx.Response(404, text="não encontrado")

    client = httpx.Client(base_url=SGS_BASE_URL, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ErroHTTP) as exc_info:
            buscar_serie(432, client=client, data_referencia=date(2025, 6, 15))
    finally:
        client.close()

    assert len(chamadas) == 1
    assert exc_info.value.codigo == 432
    assert exc_info.value.status_code == 404
    assert "após" not in str(exc_info.value)


def test_buscar_serie_erro_http_3xx_nao_tenta_novamente():
    chamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request)
        return httpx.Response(
            301, headers={"Location": "https://api.bcb.gov.br/dados/serie/novo"}
        )

    client = httpx.Client(base_url=SGS_BASE_URL, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ErroHTTP) as exc_info:
            buscar_serie(432, client=client, data_referencia=date(2025, 6, 15))
    finally:
        client.close()

    assert len(chamadas) == 1
    assert exc_info.value.status_code == 301


def test_buscar_serie_erro_http_5xx_retenta_e_leva_ate_max_tentativas():
    chamadas = []
    esperas: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request)
        return httpx.Response(500, text="erro interno")

    client = httpx.Client(base_url=SGS_BASE_URL, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ErroHTTP) as exc_info:
            buscar_serie(
                432,
                client=client,
                data_referencia=date(2025, 6, 15),
                esperar=esperas.append,
            )
    finally:
        client.close()

    assert len(chamadas) == MAX_TENTATIVAS
    assert exc_info.value.status_code == 500
    assert esperas == [0.5, 1]


def test_buscar_serie_erro_http_5xx_sucesso_apos_retry():
    chamadas = []
    esperas: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request)
        if len(chamadas) == 1:
            return httpx.Response(500, text="erro interno")
        return httpx.Response(200, json=[{"data": "01/01/2020", "valor": "13,75"}])

    client = httpx.Client(base_url=SGS_BASE_URL, transport=httpx.MockTransport(handler))
    try:
        resultado = buscar_serie(
            432,
            client=client,
            data_referencia=date(2025, 6, 15),
            esperar=esperas.append,
        )
    finally:
        client.close()

    assert len(chamadas) == 2
    assert resultado == [{"data": "01/01/2020", "valor": "13,75"}]
    assert esperas == [0.5]


def test_buscar_serie_retry_respeita_max_tentativas_alterado(monkeypatch):
    monkeypatch.setattr("indicadores.extracao.MAX_TENTATIVAS", 4)
    chamadas = []
    esperas: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request)
        return httpx.Response(500, text="erro interno")

    client = httpx.Client(base_url=SGS_BASE_URL, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ErroHTTP):
            buscar_serie(
                432,
                client=client,
                data_referencia=date(2025, 6, 15),
                esperar=esperas.append,
            )
    finally:
        client.close()

    assert len(chamadas) == 4
    assert esperas == [0.5, 1.0, 2.0]


def test_erro_http_5xx_mensagem_informa_tentativas():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="erro interno")

    client = httpx.Client(base_url=SGS_BASE_URL, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ErroHTTP) as exc_info:
            buscar_serie(
                432,
                client=client,
                data_referencia=date(2025, 6, 15),
                esperar=_no_espera,
            )
    finally:
        client.close()

    assert "após 3 tentativas" in str(exc_info.value)


def test_buscar_serie_resposta_nao_json_levanta_erro_resposta_invalida():
    chamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request)
        return httpx.Response(200, text="<html>manutenção</html>")

    client = httpx.Client(base_url=SGS_BASE_URL, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ErroRespostaInvalida):
            buscar_serie(432, client=client, data_referencia=date(2025, 6, 15))
    finally:
        client.close()

    assert len(chamadas) == 1


def test_buscar_serie_json_nao_e_lista_levanta_erro_resposta_invalida():
    chamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request)
        return httpx.Response(200, json={"erro": "parametro invalido"})

    client = httpx.Client(base_url=SGS_BASE_URL, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ErroRespostaInvalida):
            buscar_serie(432, client=client, data_referencia=date(2025, 6, 15))
    finally:
        client.close()

    assert len(chamadas) == 1


def test_buscar_serie_item_sem_chaves_esperadas_levanta_erro_resposta_invalida():
    chamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request)
        return httpx.Response(200, json=[{"campo_errado": "x"}])

    client = httpx.Client(base_url=SGS_BASE_URL, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ErroRespostaInvalida):
            buscar_serie(432, client=client, data_referencia=date(2025, 6, 15))
    finally:
        client.close()

    assert len(chamadas) == 1


def test_buscar_serie_lista_vazia_levanta_erro_serie_vazia():
    chamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request)
        return httpx.Response(200, json=[])

    client = httpx.Client(base_url=SGS_BASE_URL, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ErroSerieVazia):
            buscar_serie(432, client=client, data_referencia=date(2025, 6, 15))
    finally:
        client.close()

    assert len(chamadas) == 1


def test_buscar_serie_timeout_retenta_e_levanta_erro_conexao():
    chamadas = []
    esperas: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request)
        raise httpx.TimeoutException("timeout", request=request)

    client = httpx.Client(base_url=SGS_BASE_URL, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ErroConexao):
            buscar_serie(
                432,
                client=client,
                data_referencia=date(2025, 6, 15),
                esperar=esperas.append,
            )
    finally:
        client.close()

    assert len(chamadas) == MAX_TENTATIVAS
    assert esperas == [0.5, 1]


def test_buscar_serie_erro_conexao_sucesso_apos_retry():
    chamadas = []
    esperas: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request)
        if len(chamadas) == 1:
            raise httpx.ConnectError("erro de conexão", request=request)
        return httpx.Response(200, json=[{"data": "01/01/2020", "valor": "13,75"}])

    client = httpx.Client(base_url=SGS_BASE_URL, transport=httpx.MockTransport(handler))
    try:
        resultado = buscar_serie(
            432,
            client=client,
            data_referencia=date(2025, 6, 15),
            esperar=esperas.append,
        )
    finally:
        client.close()

    assert len(chamadas) == 2
    assert resultado == [{"data": "01/01/2020", "valor": "13,75"}]
    assert esperas == [0.5]


# ---------------------------------------------------------------------------
# buscar_series
# ---------------------------------------------------------------------------


def test_buscar_series_usa_registro_series_por_padrao():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"data": "01/01/2020", "valor": "1,0"}])

    client = httpx.Client(base_url=SGS_BASE_URL, transport=httpx.MockTransport(handler))
    try:
        resultado = buscar_series(client=client, data_referencia=date(2025, 6, 15))
    finally:
        client.close()

    assert list(resultado.keys()) == list(SERIES.keys())


def test_buscar_series_nao_fecha_client_injetado():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"data": "01/01/2020", "valor": "1,0"}])

    client = httpx.Client(base_url=SGS_BASE_URL, transport=httpx.MockTransport(handler))
    try:
        resultado = buscar_series(client=client, data_referencia=date(2025, 6, 15))

        assert resultado[432].sucesso is True
        assert client.is_closed is False
    finally:
        client.close()


def test_buscar_series_isola_falha_de_uma_serie():
    codigos = list(SERIES.keys())
    codigo_com_falha = codigos[1]

    def handler(request: httpx.Request) -> httpx.Response:
        if f"sgs.{codigo_com_falha}/dados" in request.url.path:
            return httpx.Response(500, text="erro interno")
        return httpx.Response(200, json=[{"data": "01/01/2020", "valor": "1,0"}])

    client = httpx.Client(base_url=SGS_BASE_URL, transport=httpx.MockTransport(handler))
    try:
        resultado = buscar_series(
            client=client,
            data_referencia=date(2025, 6, 15),
            esperar=_no_espera,
        )
    finally:
        client.close()

    for codigo in codigos:
        if codigo == codigo_com_falha:
            assert resultado[codigo].sucesso is False
            assert isinstance(resultado[codigo].erro, ErroHTTP)
            assert resultado[codigo].dados is None
        else:
            assert resultado[codigo].sucesso is True
            assert resultado[codigo].erro is None
            assert resultado[codigo].dados is not None


def test_buscar_series_sem_client_usa_criar_client_internamente(monkeypatch):
    clients_criados: list[httpx.Client] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"data": "01/01/2020", "valor": "1,0"}])

    def criar_client_falso() -> httpx.Client:
        client = httpx.Client(base_url=SGS_BASE_URL, transport=httpx.MockTransport(handler))
        clients_criados.append(client)
        return client

    monkeypatch.setattr("indicadores.extracao.criar_client", criar_client_falso)

    resultado = buscar_series(codigos=[432], data_referencia=date(2025, 6, 15))

    assert resultado[432].sucesso is True
    assert len(clients_criados) == 1
    assert clients_criados[0].is_closed


# ---------------------------------------------------------------------------
# criar_client
# ---------------------------------------------------------------------------


def test_criar_client_usa_base_url_timeout_e_header_esperados():
    with criar_client() as client:
        assert client.base_url == httpx.URL(SGS_BASE_URL)
        assert client.timeout == SGS_TIMEOUT
        assert client.headers["accept"] == "application/json"


# ---------------------------------------------------------------------------
# Testes adicionais (fora da lista de 17, ajustes pedidos explicitamente):
# cobertura de `_hoje()` e do default de `data_referencia` em `buscar_serie`.
# ---------------------------------------------------------------------------


def test_buscar_serie_sem_data_referencia_usa_hoje():
    requisicoes: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requisicoes.append(request)
        return httpx.Response(200, json=[{"data": "01/01/2020", "valor": "1,0"}])

    client = httpx.Client(base_url=SGS_BASE_URL, transport=httpx.MockTransport(handler))
    try:
        buscar_serie(432, client=client)
    finally:
        client.close()

    esperado = _hoje().strftime("%d/%m/%Y")
    assert requisicoes[0].url.params["dataFinal"] == esperado


def test_hoje_usa_fuso_utc_menos_3(monkeypatch):
    fuso_recebido = []

    class DatetimeFalso:
        @staticmethod
        def now(fuso=None):
            fuso_recebido.append(fuso)
            return datetime(2025, 6, 15, 10, 0, 0, tzinfo=fuso)

    monkeypatch.setattr("indicadores.extracao.datetime", DatetimeFalso)

    resultado = _hoje()

    assert fuso_recebido == [FUSO_BRASILIA]
    assert FUSO_BRASILIA == timezone(timedelta(hours=-3))
    assert resultado == date(2025, 6, 15)
