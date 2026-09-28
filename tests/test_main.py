"""Testes da CLI (`indicadores.__main__.main`).

`--banco` sempre aponta para dentro de `tmp_path`; o client HTTP é sempre
mockado (`httpx.MockTransport`), nunca rede real.
"""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb
import httpx
import pytest

import indicadores.__main__ as cli_module
from indicadores.__main__ import main
from indicadores.extracao import SGS_BASE_URL


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


_RESPOSTAS_TODAS_OK: dict[int, object] = {
    432: [{"data": "01/01/2020", "valor": "13,75"}],
    433: [{"data": "01/01/2020", "valor": "0,25"}],
    1: [{"data": "01/01/2020", "valor": "5,25"}],
}


@pytest.fixture(autouse=True)
def _resetar_logging_indicadores():
    """Evita vazar handlers/nível do logger "indicadores" entre testes.

    `_configurar_logging` altera o estado global desse logger; sem esse
    teardown, um teste que chama `main` deixaria handlers e nível
    configurados para os testes seguintes (deste ou de outros módulos).
    """
    logger = logging.getLogger("indicadores")
    nivel_original = logger.level
    handlers_originais = list(logger.handlers)

    yield

    logger.setLevel(nivel_original)
    for handler in list(logger.handlers):
        if handler not in handlers_originais:
            logger.removeHandler(handler)
    cli_module._handler_atual = None


# ---------------------------------------------------------------------------
# 12: sucesso retorna 0 e imprime o resumo
# ---------------------------------------------------------------------------


def test_main_sucesso_retorna_zero_e_imprime_resumo(tmp_path: Path, capsys):
    client = _cliente(_RESPOSTAS_TODAS_OK)
    banco = tmp_path / "indicadores.duckdb"

    codigo = main(
        ["--banco", str(banco), "--data-referencia", "2025-06-15"], client=client
    )

    saida = capsys.readouterr()
    assert codigo == 0
    assert "Séries gravadas:" in saida.out
    assert "Séries ignoradas:" in saida.out
    assert "  (nenhuma)" in saida.out


# ---------------------------------------------------------------------------
# 13: série ignorada (404, sem retry) retorna 1
# ---------------------------------------------------------------------------


def test_main_serie_ignorada_retorna_um(tmp_path: Path, capsys):
    respostas = {**_RESPOSTAS_TODAS_OK, 433: 404}
    client = _cliente(respostas)
    banco = tmp_path / "indicadores.duckdb"

    codigo = main(
        ["--banco", str(banco), "--data-referencia", "2025-06-15"], client=client
    )

    assert codigo == 1


# ---------------------------------------------------------------------------
# 14: entrada de CLI inválida (--series) retorna 2, nada é executado
# ---------------------------------------------------------------------------


def test_main_entrada_invalida_retorna_dois(tmp_path: Path):
    banco = tmp_path / "indicadores.duckdb"
    client = _cliente(_RESPOSTAS_TODAS_OK)

    try:
        with pytest.raises(SystemExit) as excinfo:
            main(["--banco", str(banco), "--series", "abc"], client=client)
    finally:
        client.close()

    assert excinfo.value.code == 2
    assert not banco.exists()


# ---------------------------------------------------------------------------
# 15: --data-referencia inválida retorna 2
# ---------------------------------------------------------------------------


def test_main_data_referencia_invalida_retorna_dois(tmp_path: Path):
    banco = tmp_path / "indicadores.duckdb"
    client = _cliente(_RESPOSTAS_TODAS_OK)

    try:
        with pytest.raises(SystemExit) as excinfo:
            main(
                ["--banco", str(banco), "--data-referencia", "2024-13-40"],
                client=client,
            )
    finally:
        client.close()

    assert excinfo.value.code == 2
    assert not banco.exists()


# ---------------------------------------------------------------------------
# 16: erro ao abrir a conexão retorna 3 e loga o erro
# ---------------------------------------------------------------------------


def test_main_erro_de_banco_retorna_tres(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    arquivo_regular = tmp_path / "nao_e_diretorio"
    arquivo_regular.write_text("x", encoding="utf-8")
    banco = arquivo_regular / "indicadores.duckdb"

    client = _cliente(_RESPOSTAS_TODAS_OK)

    with caplog.at_level(logging.ERROR, logger="indicadores.pipeline"):
        codigo = main(["--banco", str(banco)], client=client)

    assert codigo == 3
    assert any(
        registro.levelno == logging.ERROR for registro in caplog.records
    )


# ---------------------------------------------------------------------------
# 17: fecha client e conexão mesmo quando `executar` levanta exceção
# ---------------------------------------------------------------------------


def test_main_fecha_client_e_conexao_mesmo_em_erro(tmp_path: Path, monkeypatch):
    def _executar_falso(**_kwargs):
        raise RuntimeError("falha forçada")

    monkeypatch.setattr("indicadores.__main__.executar", _executar_falso)

    conexao_real = duckdb.connect(":memory:")
    monkeypatch.setattr(
        "indicadores.__main__.abrir_conexao", lambda caminho: conexao_real
    )

    fechado = {"client": False}

    class ClienteEspiao:
        def close(self) -> None:
            fechado["client"] = True

    banco = tmp_path / "indicadores.duckdb"
    codigo = main(["--banco", str(banco)], client=ClienteEspiao())

    assert codigo == 3
    assert fechado["client"] is True
    with pytest.raises(duckdb.Error):
        conexao_real.execute("SELECT 1")


# ---------------------------------------------------------------------------
# Acréscimo (revisão de código): falha ao criar o client retorna 3, sem
# nenhuma conexão aberta.
# ---------------------------------------------------------------------------


def test_main_falha_ao_criar_client_retorna_tres(
    tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
):
    chamadas_abrir_conexao: list[Path] = []

    def _criar_client_falso() -> httpx.Client:
        raise RuntimeError("falha ao criar client")

    def _abrir_conexao_espiao(caminho: Path):
        chamadas_abrir_conexao.append(caminho)
        raise AssertionError("abrir_conexao não deveria ser chamado")

    monkeypatch.setattr("indicadores.__main__.criar_client", _criar_client_falso)
    monkeypatch.setattr("indicadores.__main__.abrir_conexao", _abrir_conexao_espiao)

    banco = tmp_path / "indicadores.duckdb"

    with caplog.at_level(logging.ERROR, logger="indicadores.pipeline"):
        codigo = main(["--banco", str(banco)])

    assert codigo == 3
    assert any(registro.levelno == logging.ERROR for registro in caplog.records)
    assert chamadas_abrir_conexao == []
    assert not banco.exists()


# ---------------------------------------------------------------------------
# 18: -v ajusta o nível de log
# ---------------------------------------------------------------------------


def test_main_verbose_ajusta_nivel_de_log(tmp_path: Path):
    banco = tmp_path / "indicadores.duckdb"

    client_sem_v = _cliente(_RESPOSTAS_TODAS_OK)
    main(["--banco", str(banco), "--data-referencia", "2025-06-15"], client=client_sem_v)
    assert logging.getLogger("indicadores").level == logging.WARNING

    client_com_v = _cliente(_RESPOSTAS_TODAS_OK)
    main(
        ["--banco", str(banco), "--data-referencia", "2025-06-15", "-v"],
        client=client_com_v,
    )
    assert logging.getLogger("indicadores").level == logging.INFO


# ---------------------------------------------------------------------------
# Extra (fora da lista de 20 da spec): -vv ajusta para DEBUG.
# ---------------------------------------------------------------------------


def test_main_verbose_duplo_v_ajusta_para_debug(tmp_path: Path):
    banco = tmp_path / "indicadores.duckdb"
    client = _cliente(_RESPOSTAS_TODAS_OK)

    main(
        ["--banco", str(banco), "--data-referencia", "2025-06-15", "-vv"],
        client=client,
    )

    assert logging.getLogger("indicadores").level == logging.DEBUG


# ---------------------------------------------------------------------------
# 19: logs vão para stderr, nunca para stdout
# ---------------------------------------------------------------------------


def test_main_logs_vao_para_stderr_nao_stdout(tmp_path: Path, capsys):
    respostas = {**_RESPOSTAS_TODAS_OK, 433: 404}
    client = _cliente(respostas)
    banco = tmp_path / "indicadores.duckdb"

    main(["--banco", str(banco), "--data-referencia", "2025-06-15"], client=client)

    saida = capsys.readouterr()
    # "433 ignorada:" é específico da mensagem de log (`limpeza.limpar`);
    # não usar só "ignorada", que também aparece no cabeçalho
    # "Séries ignoradas:" do resumo impresso em stdout.
    assert "433 ignorada:" in saida.err
    assert "433 ignorada:" not in saida.out


# ---------------------------------------------------------------------------
# Acréscimo (revisão de código): a linha de log inclui nível e nome do
# logger de origem (formato "%(levelname)s %(name)s: %(message)s").
# ---------------------------------------------------------------------------


def test_main_log_inclui_nivel_e_nome_do_logger(tmp_path: Path, capsys):
    respostas = {**_RESPOSTAS_TODAS_OK, 433: 404}
    client = _cliente(respostas)
    banco = tmp_path / "indicadores.duckdb"

    main(["--banco", str(banco), "--data-referencia", "2025-06-15"], client=client)

    saida = capsys.readouterr()
    assert "WARNING indicadores.limpeza:" in saida.err


# ---------------------------------------------------------------------------
# 20: chamar main duas vezes não duplica handlers nem mensagens de log
# ---------------------------------------------------------------------------


def test_main_chamado_duas_vezes_nao_duplica_handlers(tmp_path: Path, capsys):
    logger = logging.getLogger("indicadores")
    handlers_antes = list(logger.handlers)

    respostas = {**_RESPOSTAS_TODAS_OK, 433: 404}
    banco = tmp_path / "indicadores.duckdb"

    client1 = _cliente(respostas)
    main(["--banco", str(banco), "--data-referencia", "2025-06-15"], client=client1)
    capsys.readouterr()  # descarta a saída da 1ª chamada

    client2 = _cliente(respostas)
    main(["--banco", str(banco), "--data-referencia", "2025-06-15"], client=client2)
    saida = capsys.readouterr()

    novos_handlers = [h for h in logger.handlers if h not in handlers_antes]
    assert len(novos_handlers) == 1
    assert saida.err.count("ignorada") == 1
