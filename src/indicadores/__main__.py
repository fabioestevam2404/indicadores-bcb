"""CLI fina de `python -m indicadores`: extração → limpeza → persistência.

Responsável só por argparse, logging e I/O (stdin/stdout/stderr); toda a
orquestração de fato está em `pipeline.executar`.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import httpx

from indicadores.extracao import criar_client
from indicadores.persistencia import CAMINHO_PADRAO, abrir_conexao
from indicadores.pipeline import executar, formatar_resumo

logger = logging.getLogger("indicadores.pipeline")

# Guarda o handler de stderr adicionado pela última chamada de
# `_configurar_logging`, para removê-lo antes de adicionar um novo e
# nunca acumular handlers em chamadas repetidas (ex.: em testes).
_handler_atual: logging.Handler | None = None


def _tipo_data_referencia(texto: str) -> date:
    """Converte `AAAA-MM-DD` em `date`; erro de formato vira `ArgumentTypeError`."""
    try:
        return date.fromisoformat(texto)
    except ValueError as erro:
        raise argparse.ArgumentTypeError(str(erro)) from erro


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Interpreta os argumentos da CLI. Pode levantar `SystemExit(2)`."""
    parser = argparse.ArgumentParser(
        prog="python -m indicadores",
        description=(
            "Busca as séries do SGS/Banco Central, limpa e grava em DuckDB."
        ),
    )
    parser.add_argument(
        "--banco",
        type=Path,
        default=CAMINHO_PADRAO,
        metavar="CAMINHO",
        help=f"caminho do arquivo DuckDB (default: {CAMINHO_PADRAO})",
    )
    parser.add_argument(
        "--series",
        type=int,
        nargs="+",
        default=None,
        metavar="COD",
        help="códigos SGS a buscar (default: todas as séries de SERIES)",
    )
    parser.add_argument(
        "--data-referencia",
        type=_tipo_data_referencia,
        default=None,
        metavar="AAAA-MM-DD",
        help="data de referência da janela de 5 anos (default: hoje em Brasília)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="-v para INFO, -vv para DEBUG (default: WARNING)",
    )
    return parser.parse_args(argv)


def _configurar_logging(verbose: int) -> None:
    """Configura o logger `indicadores` para emitir em stderr.

    Nível: 0 → WARNING, 1 → INFO, 2 ou mais → DEBUG. Remove o handler
    adicionado por uma chamada anterior antes de adicionar um novo, para
    não acumular handlers em chamadas repetidas na mesma execução do
    processo (ex.: em testes que chamam `main` várias vezes).

    Limitação registrada: `propagate` é mantido `True` (necessário para
    `caplog` capturar essas mensagens nos testes). Se `main` for chamado
    dentro de uma aplicação host que já configurou handlers no logger
    raiz (root logger) do processo, as mensagens de `indicadores.*` podem
    sair duplicadas (uma vez pelo handler adicionado aqui, outra pela
    propagação até o handler do host). No uso normal via linha de
    comando isso não ocorre, pois não há handler prévio no root logger.
    """
    global _handler_atual

    if verbose >= 2:
        nivel = logging.DEBUG
    elif verbose == 1:
        nivel = logging.INFO
    else:
        nivel = logging.WARNING

    logger_raiz = logging.getLogger("indicadores")
    logger_raiz.setLevel(nivel)
    logger_raiz.propagate = True

    if _handler_atual is not None:
        logger_raiz.removeHandler(_handler_atual)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger_raiz.addHandler(handler)
    _handler_atual = handler


def main(
    argv: list[str] | None = None,
    *,
    client: httpx.Client | None = None,
) -> int:
    """Ponto de entrada de `python -m indicadores`. Retorna o exit code.

    `client` é keyword-only e existe só para testes injetarem um
    `httpx.Client` mockado, sem monkeypatch em `extracao.criar_client`.
    `main` sempre fecha esse client (o que criou ou o que recebeu) e a
    conexão que abriu, inclusive quando `executar` levanta uma exceção
    (incluindo uma falha na própria criação do client). Uma falha em
    `criar_client()` também cai no `except` e resulta em exit code 3.

    Limitação registrada: duas execuções simultâneas apontando para o
    mesmo arquivo `--banco` provavelmente fazem a segunda falhar ao
    abrir o arquivo (bloqueado pela primeira conexão), terminando com
    exit code 3 — não há tratamento especial de concorrência aqui.
    """
    args = _parse_args(argv)
    _configurar_logging(args.verbose)

    meu_client: httpx.Client | None = None
    conexao = None
    codigo_saida = 0

    try:
        meu_client = client if client is not None else criar_client()
        conexao = abrir_conexao(args.banco)
        resumo = executar(
            conexao=conexao,
            client=meu_client,
            codigos=args.series,
            data_referencia=args.data_referencia,
        )
    except Exception:
        logger.exception("erro inesperado ao executar o pipeline")
        codigo_saida = 3
    else:
        print(formatar_resumo(resumo))
        codigo_saida = 1 if resumo.series_ignoradas else 0
    finally:
        if meu_client is not None:
            meu_client.close()
        if conexao is not None:
            conexao.close()

    return codigo_saida


if __name__ == "__main__":
    sys.exit(main())
