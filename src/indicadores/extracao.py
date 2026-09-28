"""Extração de dados brutos do SGS/Banco Central.

Este módulo busca as séries configuradas em `SERIES` na API do SGS e
devolve os dados exatamente como a API respondeu, sem nenhuma
transformação (datas continuam `dd/mm/aaaa`, valores continuam `str`
com vírgula decimal). Limpeza, tipagem e persistência ficam a cargo de
módulos futuros.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import httpx

SGS_BASE_URL: str = "https://api.bcb.gov.br"
SGS_TIMEOUT: httpx.Timeout = httpx.Timeout(10.0)
JANELA_ANOS: int = 5
MAX_TENTATIVAS: int = 3
FUSO_BRASILIA: timezone = timezone(timedelta(hours=-3))

SERIES: dict[int, str] = {
    432: "selic_meta",
    433: "ipca_mensal",
    1: "dolar_ptax_venda",
}


class ErroExtracaoBCB(Exception):
    """Base para erros de extração. Sempre carrega `codigo` da série."""

    def __init__(self, codigo: int, mensagem: str) -> None:
        self.codigo = codigo
        self.mensagem = mensagem
        super().__init__(mensagem)


class ErroHTTP(ErroExtracaoBCB):
    """Status HTTP fora de 2xx, após esgotar retries quando aplicável."""

    def __init__(
        self, codigo: int, status_code: int, corpo: str, tentativas: int = 1
    ) -> None:
        self.status_code = status_code
        self.corpo = corpo
        mensagem = f"série {codigo}: status HTTP {status_code}"
        if tentativas > 1:
            mensagem += f" após {tentativas} tentativas"
        super().__init__(codigo, mensagem)


class ErroRespostaInvalida(ErroExtracaoBCB):
    """Corpo da resposta não tem a forma esperada.

    Cobre: não é JSON (ex.: HTML de erro/manutenção), é JSON mas não é
    uma lista (ex.: objeto de erro `{"erro": "..."}`), ou é uma lista
    cujos itens não são dicts com as chaves "data" e "valor".
    """


class ErroSerieVazia(ErroExtracaoBCB):
    """A API respondeu 2xx com JSON válido (lista bem formada), mas vazia."""


class ErroConexao(ErroExtracaoBCB):
    """Timeout ou erro de rede, após esgotar retries."""


@dataclass(frozen=True)
class ResultadoSerie:
    """Resultado da busca de uma série, com sucesso ou erro."""

    codigo: int
    dados: list[dict[str, str]] | None
    erro: ErroExtracaoBCB | None

    @property
    def sucesso(self) -> bool:
        """Indica se a busca terminou sem erro."""
        return self.erro is None


def calcular_intervalo(
    data_referencia: date,
    janela_anos: int = JANELA_ANOS,
) -> tuple[str, str]:
    """Retorna (data_inicial, data_final) no formato dd/mm/aaaa.

    `data_final` é `data_referencia`. `data_inicial` é `data_referencia`
    com o ano subtraído de `janela_anos`. Se `data_referencia` for 29/02
    e o ano de destino não for bissexto, `data_inicial` cai para 28/02
    daquele ano.
    """
    ano_inicial = data_referencia.year - janela_anos
    try:
        data_inicial = data_referencia.replace(year=ano_inicial)
    except ValueError:
        # 29/02 em ano de destino não bissexto.
        data_inicial = data_referencia.replace(year=ano_inicial, day=28)

    return (
        data_inicial.strftime("%d/%m/%Y"),
        data_referencia.strftime("%d/%m/%Y"),
    )


def _hoje() -> date:
    """Data de hoje no fuso de Brasília (UTC-3 fixo, sem horário de verão)."""
    return datetime.now(FUSO_BRASILIA).date()


def criar_client() -> httpx.Client:
    """Monta o client HTTP usado em produção (fora de testes)."""
    return httpx.Client(
        base_url=SGS_BASE_URL,
        timeout=SGS_TIMEOUT,
        headers={"Accept": "application/json"},
    )


def _validar_forma(codigo: int, corpo_json: object) -> list[dict[str, str]]:
    """Valida que `corpo_json` é uma lista de dicts com "data" e "valor"."""
    if not isinstance(corpo_json, list):
        raise ErroRespostaInvalida(
            codigo,
            f"série {codigo}: resposta não é uma lista JSON",
        )

    for item in corpo_json:
        if not isinstance(item, dict) or "data" not in item or "valor" not in item:
            raise ErroRespostaInvalida(
                codigo,
                f"série {codigo}: item da lista sem as chaves 'data'/'valor'",
            )

    if len(corpo_json) == 0:
        raise ErroSerieVazia(codigo, f"série {codigo}: lista vazia")

    return corpo_json


def buscar_serie(
    codigo: int,
    *,
    client: httpx.Client,
    data_referencia: date | None = None,
    esperar: Callable[[float], None] = time.sleep,
) -> list[dict[str, str]]:
    """Busca os dados brutos de uma série no SGS.

    Retorna a lista de dicts exatamente como veio no JSON (chaves
    "data" e "valor", ambos `str`, sem nenhuma conversão). Levanta
    `ErroHTTP`, `ErroRespostaInvalida`, `ErroSerieVazia` ou
    `ErroConexao` conforme o caso.
    """
    referencia = data_referencia if data_referencia is not None else _hoje()
    data_inicial, data_final = calcular_intervalo(referencia)

    url = f"/dados/serie/bcdata.sgs.{codigo}/dados"
    params = {
        "formato": "json",
        "dataInicial": data_inicial,
        "dataFinal": data_final,
    }
    headers = {"Accept": "application/json"}

    backoffs = [0.5 * 2**i for i in range(MAX_TENTATIVAS - 1)]
    tentativa = 0

    while True:
        ultima_tentativa = tentativa == MAX_TENTATIVAS - 1

        try:
            resposta = client.get(url, params=params, headers=headers)
        except httpx.TransportError as erro:
            if not ultima_tentativa:
                esperar(backoffs[tentativa])
                tentativa += 1
                continue
            raise ErroConexao(
                codigo,
                f"série {codigo}: erro de conexão após {MAX_TENTATIVAS} tentativas",
            ) from erro

        if 500 <= resposta.status_code < 600:
            if not ultima_tentativa:
                esperar(backoffs[tentativa])
                tentativa += 1
                continue
            raise ErroHTTP(
                codigo, resposta.status_code, resposta.text, tentativas=MAX_TENTATIVAS
            )

        if resposta.status_code < 200 or resposta.status_code >= 300:
            raise ErroHTTP(codigo, resposta.status_code, resposta.text)

        try:
            corpo_json = resposta.json()
        except ValueError as erro:
            raise ErroRespostaInvalida(
                codigo,
                f"série {codigo}: corpo da resposta não é JSON",
            ) from erro

        return _validar_forma(codigo, corpo_json)


def buscar_series(
    codigos: Iterable[int] | None = None,
    *,
    client: httpx.Client | None = None,
    data_referencia: date | None = None,
    esperar: Callable[[float], None] = time.sleep,
) -> dict[int, ResultadoSerie]:
    """Busca várias séries, uma por vez, isolando falhas por série.

    Se `client` for `None`, cria internamente `criar_client()` como
    context manager e fecha ao final da função.
    """
    codigos_lista = list(codigos) if codigos is not None else list(SERIES.keys())

    if client is not None:
        return _buscar_series_com_client(
            codigos_lista,
            client=client,
            data_referencia=data_referencia,
            esperar=esperar,
        )

    with criar_client() as client_interno:
        return _buscar_series_com_client(
            codigos_lista,
            client=client_interno,
            data_referencia=data_referencia,
            esperar=esperar,
        )


def _buscar_series_com_client(
    codigos: list[int],
    *,
    client: httpx.Client,
    data_referencia: date | None,
    esperar: Callable[[float], None],
) -> dict[int, ResultadoSerie]:
    """Executa `buscar_serie` para cada código, isolando falhas."""
    resultados: dict[int, ResultadoSerie] = {}
    for codigo in codigos:
        try:
            dados = buscar_serie(
                codigo,
                client=client,
                data_referencia=data_referencia,
                esperar=esperar,
            )
            resultados[codigo] = ResultadoSerie(codigo=codigo, dados=dados, erro=None)
        except ErroExtracaoBCB as erro:
            resultados[codigo] = ResultadoSerie(codigo=codigo, dados=None, erro=erro)
    return resultados
