"""Testes da CLI (`indicadores.__main__.main`).

`--banco` sempre aponta para dentro de `tmp_path`; o client HTTP é sempre
mockado (`httpx.MockTransport`), nunca rede real.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import duckdb
import httpx
import pandas as pd
import pytest

import indicadores.__main__ as cli_module
from indicadores.__main__ import _parse_args, main
from indicadores.extracao import FUSO_BRASILIA, SGS_BASE_URL
from indicadores.persistencia import abrir_conexao, criar_tabela, gravar, ler
from indicadores.relatorio import gerar_html as gerar_html_real


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


@pytest.fixture(autouse=True)
def _isolar_diretorio_de_trabalho(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Roda cada teste com `tmp_path` como diretório de trabalho.

    O relatório HTML tem default relativo (`relatorio.html`); sem isso, um
    teste que esquecesse `--sem-relatorio`/`--relatorio` escreveria na raiz
    do repositório.
    """
    monkeypatch.chdir(tmp_path)


# ---------------------------------------------------------------------------
# 12: sucesso retorna 0 e imprime o resumo
# ---------------------------------------------------------------------------


def test_main_sucesso_retorna_zero_e_imprime_resumo(tmp_path: Path, capsys):
    client = _cliente(_RESPOSTAS_TODAS_OK)
    banco = tmp_path / "indicadores.duckdb"

    codigo = main(
        ["--banco", str(banco), "--data-referencia", "2025-06-15", "--sem-relatorio"], client=client
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
        ["--banco", str(banco), "--data-referencia", "2025-06-15", "--sem-relatorio"], client=client
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
    main(["--banco", str(banco), "--data-referencia", "2025-06-15", "--sem-relatorio"], client=client_sem_v)
    assert logging.getLogger("indicadores").level == logging.WARNING

    client_com_v = _cliente(_RESPOSTAS_TODAS_OK)
    main(
        ["--banco", str(banco), "--data-referencia", "2025-06-15", "--sem-relatorio", "-v"],
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
        ["--banco", str(banco), "--data-referencia", "2025-06-15", "--sem-relatorio", "-vv"],
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

    main(["--banco", str(banco), "--data-referencia", "2025-06-15", "--sem-relatorio"], client=client)

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

    main(["--banco", str(banco), "--data-referencia", "2025-06-15", "--sem-relatorio"], client=client)

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
    main(["--banco", str(banco), "--data-referencia", "2025-06-15", "--sem-relatorio"], client=client1)
    capsys.readouterr()  # descarta a saída da 1ª chamada

    client2 = _cliente(respostas)
    main(["--banco", str(banco), "--data-referencia", "2025-06-15", "--sem-relatorio"], client=client2)
    saida = capsys.readouterr()

    novos_handlers = [h for h in logger.handlers if h not in handlers_antes]
    assert len(novos_handlers) == 1
    assert saida.err.count("ignorada") == 1


# ===========================================================================
# Relatório HTML (specs/relatorio.md, casos 44-55)
# ===========================================================================

_DATA_REF = "2026-08-31"
_ATUALIZADO_EM = datetime(2026, 8, 30, 10, 0)  # noqa: DTZ001 (naive de propósito)

# Respostas da API (mock) com datas dentro da janela de 5 anos.
_RESPOSTAS_RELATORIO: dict[int, object] = {
    432: [
        {"data": "30/08/2026", "valor": "14,75"},
        {"data": "31/08/2026", "valor": "14,75"},
    ],
    433: [
        {"data": "01/07/2026", "valor": "0,30"},
        {"data": "01/08/2026", "valor": "-0,11"},
    ],
    1: [
        {"data": "28/08/2026", "valor": "5,4000"},
        {"data": "31/08/2026", "valor": "5,3784"},
    ],
}

_NOMES = {432: "selic_meta", 433: "ipca_mensal", 1: "dolar_ptax_venda"}


def _popular_banco(banco: Path) -> None:
    """Grava no banco dados "antigos" (que a API mockada não devolve)."""
    linhas = [
        (432, "2026-08-27", 14.50),
        (433, "2026-06-01", 0.40),
        (1, "2026-08-27", 5.5000),
    ]
    dados = pd.DataFrame(
        {
            "codigo": pd.Series([c for c, _, _ in linhas], dtype="int64"),
            "serie": [_NOMES[c] for c, _, _ in linhas],
            "data": pd.to_datetime([d for _, d, _ in linhas]),
            "valor": [float(v) for _, _, v in linhas],
        }
    )
    conexao = abrir_conexao(banco)
    try:
        criar_tabela(conexao)
        gravar(conexao, dados, atualizado_em=_ATUALIZADO_EM)
    finally:
        conexao.close()


def _ler_banco(banco: Path) -> pd.DataFrame:
    conexao = abrir_conexao(banco)
    try:
        return ler(conexao)
    finally:
        conexao.close()


def _args_relatorio(tmp_path: Path, *extras: str) -> list[str]:
    return [
        "--banco",
        str(tmp_path / "indicadores.duckdb"),
        "--relatorio",
        str(tmp_path / "r.html"),
        "--data-referencia",
        _DATA_REF,
        *extras,
    ]


def _falhar_se_chamado(*_args, **_kwargs):
    raise AssertionError("não deveria ser chamado")


def _gerar_html_falho(*_args, **_kwargs):
    raise RuntimeError("falha forçada")


def test_main_grava_relatorio_depois_da_execucao(tmp_path: Path, capsys):
    banco = tmp_path / "indicadores.duckdb"
    _popular_banco(banco)
    rel = tmp_path / "r.html"

    codigo = main(_args_relatorio(tmp_path), client=_cliente(_RESPOSTAS_RELATORIO))

    saida = capsys.readouterr()
    assert codigo == 0
    html = rel.read_text(encoding="utf-8")
    assert "R$ 5,3784" in html  # recém-gravado
    assert "27/08/2026 — R$ 5,5000" in html  # já estava no banco (lido do banco)
    assert "ERROR" not in saida.err

    linha = f"Relatório gravado em: {rel}"
    assert saida.out.rstrip("\n").endswith(linha)
    assert saida.out.index("Séries gravadas:") < saida.out.index(linha)
    assert "\n\n" + linha in saida.out  # separada do resumo por linha em branco


def test_main_sem_relatorio_nao_gera_arquivo(tmp_path: Path, capsys):
    codigo = main(
        _args_relatorio(tmp_path, "--sem-relatorio"),
        client=_cliente(_RESPOSTAS_RELATORIO),
    )

    saida = capsys.readouterr()
    assert codigo == 0
    assert not (tmp_path / "r.html").exists()
    assert not (tmp_path / "relatorio.html").exists()  # o default também não
    assert not list(tmp_path.glob("*.tmp"))
    assert "Relatório gravado" not in saida.out
    assert "Séries gravadas:" in saida.out


def test_main_so_relatorio_sem_rede_e_sem_alterar_o_banco(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    banco = tmp_path / "indicadores.duckdb"
    _popular_banco(banco)
    antes = _ler_banco(banco)
    monkeypatch.setattr("indicadores.__main__.criar_client", _falhar_se_chamado)
    monkeypatch.setattr("indicadores.__main__.executar", _falhar_se_chamado)
    rel = tmp_path / "r.html"

    codigo = main(["--banco", str(banco), "--relatorio", str(rel), "--so-relatorio"])

    saida = capsys.readouterr()
    assert codigo == 0
    html = rel.read_text(encoding="utf-8")
    # Só 1 observação por série no banco: o card mostra o último valor.
    assert "R$ 5,5000" in html and "27/08/2026" in html
    assert f"Relatório gravado em: {rel}" in saida.out
    assert "Séries gravadas" not in saida.out  # não há resumo do pipeline
    assert "ERROR" not in saida.err

    # Conexão fechada: o arquivo pode ser reaberto para escrita em seguida.
    conexao = duckdb.connect(str(banco))
    try:
        conexao.execute("SELECT 1").fetchone()
    finally:
        conexao.close()
    # Banco idêntico, inclusive atualizado_em.
    depois = _ler_banco(banco)
    pd.testing.assert_frame_equal(depois, antes)
    assert (depois["atualizado_em"] == pd.Timestamp(_ATUALIZADO_EM)).all()


def test_main_so_relatorio_com_banco_inexistente(tmp_path: Path, capsys):
    banco = tmp_path / "pasta_nova" / "nao_existe.duckdb"
    rel = tmp_path / "r.html"

    codigo = main(["--banco", str(banco), "--relatorio", str(rel), "--so-relatorio"])

    saida = capsys.readouterr()
    assert codigo == 4
    assert not banco.exists()
    assert not banco.parent.exists()
    assert not rel.exists()
    assert "ERROR" in saida.err
    assert "banco não encontrado" in saida.err
    assert "Relatório gravado" not in saida.out


@pytest.mark.parametrize(
    "extras",
    [
        ["--so-relatorio", "--sem-relatorio"],
        ["--so-relatorio", "--series", "432"],
        ["--so-relatorio", "--data-referencia", "2026-01-01"],
    ],
    ids=["sem_relatorio", "series", "data_referencia"],
)
def test_main_flags_incompativeis_retornam_dois(tmp_path: Path, extras: list[str]):
    argv = [
        "--banco",
        str(tmp_path / "sub" / "indicadores.duckdb"),
        "--relatorio",
        str(tmp_path / "r.html"),
        *extras,
    ]

    with pytest.raises(SystemExit) as excinfo:
        main(argv)

    assert excinfo.value.code == 2
    assert list(tmp_path.iterdir()) == []  # nada criado (banco, diretório, relatório)


def test_main_falha_ao_gerar_relatorio_retorna_quatro_e_preserva_dados(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    rel = tmp_path / "r.html"
    rel.write_text("RELATORIO ANTERIOR", encoding="utf-8")
    monkeypatch.setattr("indicadores.__main__.gerar_html", _gerar_html_falho)

    codigo = main(_args_relatorio(tmp_path), client=_cliente(_RESPOSTAS_RELATORIO))

    saida = capsys.readouterr()
    assert codigo == 4
    dados = _ler_banco(tmp_path / "indicadores.duckdb")
    assert set(dados["codigo"]) == {432, 433, 1}
    assert len(dados) == 6
    assert "Séries gravadas:" in saida.out
    assert "Relatório gravado" not in saida.out
    assert "ERROR" in saida.err
    assert "falha ao gerar o relatório" in saida.err  # mesmo sem -v
    assert rel.read_text(encoding="utf-8") == "RELATORIO ANTERIOR"


def test_main_falha_do_relatorio_com_serie_ignorada_mantem_um(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    monkeypatch.setattr("indicadores.__main__.gerar_html", _gerar_html_falho)
    respostas = {**_RESPOSTAS_RELATORIO, 433: 404}

    codigo = main(_args_relatorio(tmp_path), client=_cliente(respostas))

    saida = capsys.readouterr()
    assert codigo == 1  # prevalece sobre o 4
    assert "ERROR" in saida.err
    assert "falha ao gerar o relatório" in saida.err
    assert not (tmp_path / "r.html").exists()


def test_main_serie_ignorada_ainda_gera_relatorio(tmp_path: Path, capsys):
    banco = tmp_path / "indicadores.duckdb"
    _popular_banco(banco)
    respostas = {**_RESPOSTAS_RELATORIO, 433: 404}

    codigo = main(_args_relatorio(tmp_path), client=_cliente(respostas))

    saida = capsys.readouterr()
    assert codigo == 1
    html = (tmp_path / "r.html").read_text(encoding="utf-8")
    assert "27/08/2026 — R$ 5,5000" in html  # dado antigo do banco
    assert "R$ 5,3784" in html  # série que deu certo nesta execução
    assert "jun/2026 — 0,40%" in html  # IPCA antigo, série ignorada agora
    assert "Relatório gravado em:" in saida.out


def test_main_erro_inesperado_nao_tenta_relatorio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    rel = tmp_path / "r.html"
    rel.write_text("RELATORIO ANTERIOR", encoding="utf-8")
    chamadas: list[object] = []

    def _executar_falso(**_kwargs):
        raise RuntimeError("falha forçada")

    def _gerar_html_espiao(*args, **kwargs):
        chamadas.append((args, kwargs))
        return "<html></html>"

    monkeypatch.setattr("indicadores.__main__.executar", _executar_falso)
    monkeypatch.setattr("indicadores.__main__.gerar_html", _gerar_html_espiao)

    codigo = main(_args_relatorio(tmp_path), client=_cliente(_RESPOSTAS_RELATORIO))

    capsys.readouterr()
    assert codigo == 3
    assert chamadas == []
    assert rel.read_text(encoding="utf-8") == "RELATORIO ANTERIOR"


def test_main_falha_de_escrita_do_relatorio_retorna_quatro(tmp_path: Path, capsys):
    destino = tmp_path / "pasta_existente"
    destino.mkdir()  # --relatorio aponta para um diretório: os.replace falha

    codigo = main(
        [
            "--banco",
            str(tmp_path / "indicadores.duckdb"),
            "--relatorio",
            str(destino),
            "--data-referencia",
            _DATA_REF,
        ],
        client=_cliente(_RESPOSTAS_RELATORIO),
    )

    saida = capsys.readouterr()
    assert codigo == 4
    assert "Traceback" not in saida.out
    assert "Relatório gravado" not in saida.out
    assert "ERROR" in saida.err
    assert not list(tmp_path.glob("*.tmp"))
    assert destino.is_dir()
    assert len(_ler_banco(tmp_path / "indicadores.duckdb")) == 6  # dados preservados


@pytest.mark.parametrize("modo", ["normal", "so_relatorio"])
def test_main_passa_horario_de_brasilia_naive_ao_relatorio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, modo: str
):
    capturado: dict[str, object] = {}
    conexoes: list[duckdb.DuckDBPyConnection] = []

    def _gerar_html_espiao(dados, gerado_em):
        capturado["gerado_em"] = gerado_em
        return gerar_html_real(dados, gerado_em)

    modos: list[bool] = []

    def _abrir_conexao_espiao(caminho, *, somente_leitura=False):
        modos.append(somente_leitura)
        conexao = abrir_conexao(caminho, somente_leitura=somente_leitura)
        conexoes.append(conexao)
        return conexao

    monkeypatch.setattr("indicadores.__main__.gerar_html", _gerar_html_espiao)
    monkeypatch.setattr("indicadores.__main__.abrir_conexao", _abrir_conexao_espiao)

    if modo == "normal":
        codigo = main(_args_relatorio(tmp_path), client=_cliente(_RESPOSTAS_RELATORIO))
    else:
        _popular_banco(tmp_path / "indicadores.duckdb")
        codigo = main(
            [
                "--banco",
                str(tmp_path / "indicadores.duckdb"),
                "--relatorio",
                str(tmp_path / "r.html"),
                "--so-relatorio",
            ]
        )

    assert codigo == 0
    gerado_em = capturado["gerado_em"]
    assert isinstance(gerado_em, datetime)
    assert gerado_em.tzinfo is None
    agora = datetime.now(FUSO_BRASILIA).replace(tzinfo=None)
    assert abs((agora - gerado_em).total_seconds()) < 30
    assert len(conexoes) == 1
    assert modos == [modo == "so_relatorio"]
    with pytest.raises(duckdb.Error):
        conexoes[0].execute("SELECT 1")  # conexão fechada ao fim


def test_main_defaults_e_ajuda_das_flags_novas(capsys):
    args = _parse_args([])
    assert args.relatorio == Path("relatorio.html")
    assert args.sem_relatorio is False
    assert args.so_relatorio is False

    # --relatorio junto com --sem-relatorio é aceito (e ignorado).
    args = _parse_args(["--sem-relatorio", "--relatorio", "x.html"])
    assert args.sem_relatorio is True

    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    ajuda = capsys.readouterr().out
    assert "--relatorio CAMINHO" in ajuda
    assert "--sem-relatorio" in ajuda
    assert "--so-relatorio" in ajuda


# ---------------------------------------------------------------------------
# Revisão de código (M3): caminhos de erro do --so-relatorio, todos exit 4
# ---------------------------------------------------------------------------


def _mensagens_error(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]


def _args_so_relatorio(tmp_path: Path, banco: Path | str | None = None) -> list[str]:
    return [
        "--banco",
        str(banco if banco is not None else tmp_path / "indicadores.duckdb"),
        "--relatorio",
        str(tmp_path / "r.html"),
        "--so-relatorio",
    ]


def test_so_relatorio_banco_existente_sem_tabela_retorna_quatro(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, capsys
):
    banco = tmp_path / "indicadores.duckdb"
    conexao = abrir_conexao(banco)  # cria o arquivo, mas não a tabela `indicadores`
    conexao.close()
    antes = banco.read_bytes()

    with caplog.at_level(logging.ERROR, logger="indicadores.pipeline"):
        codigo = main(_args_so_relatorio(tmp_path))

    saida = capsys.readouterr()
    assert codigo == 4
    erros = _mensagens_error(caplog)
    assert erros == ["falha ao gerar o relatório"]  # sem a frase sobre dados gravados
    assert caplog.records[0].exc_info is not None  # traceback vai para o log
    assert "falha ao gerar o relatório" in saida.err
    assert "dados foram gravados" not in saida.err
    assert not (tmp_path / "r.html").exists()
    assert "Relatório gravado" not in saida.out
    assert banco.exists()
    # Não criou a tabela (não chama `criar_tabela`) nem alterou o conteúdo.
    conexao = duckdb.connect(str(banco), read_only=True)
    try:
        assert conexao.execute("SHOW TABLES").fetchall() == []
    finally:
        conexao.close()
    assert banco.read_bytes() == antes


def test_so_relatorio_arquivo_de_banco_invalido_retorna_quatro(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, capsys
):
    banco = tmp_path / "invalido.duckdb"
    banco.write_bytes(b"isto nao e um banco duckdb" * 100)

    with caplog.at_level(logging.ERROR, logger="indicadores.pipeline"):
        codigo = main(_args_so_relatorio(tmp_path, banco))

    saida = capsys.readouterr()
    assert codigo == 4
    erros = _mensagens_error(caplog)
    assert len(erros) == 1 and "banco" in erros[0]
    assert "dados foram gravados" not in erros[0]
    assert "ERROR" in saida.err
    assert not (tmp_path / "r.html").exists()
    assert "Relatório gravado" not in saida.out


def test_so_relatorio_falha_ao_abrir_conexao_retorna_quatro(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, capsys
):
    banco = tmp_path / "indicadores.duckdb"
    _popular_banco(banco)  # o arquivo existe: a checagem prévia passa
    chamadas: list[Path] = []
    modos: list[bool] = []

    def _abrir_bloqueado(caminho, *, somente_leitura=False):
        chamadas.append(caminho)
        modos.append(somente_leitura)
        raise duckdb.IOException("Could not set lock on file (banco bloqueado)")

    monkeypatch.setattr("indicadores.__main__.abrir_conexao", _abrir_bloqueado)
    monkeypatch.setattr("indicadores.__main__.criar_client", _falhar_se_chamado)
    monkeypatch.setattr("indicadores.__main__.executar", _falhar_se_chamado)

    with caplog.at_level(logging.ERROR, logger="indicadores.pipeline"):
        codigo = main(_args_so_relatorio(tmp_path))

    saida = capsys.readouterr()
    assert codigo == 4
    assert chamadas == [banco]
    assert modos == [True]
    erros = _mensagens_error(caplog)
    assert len(erros) == 1 and "abrir o banco" in erros[0]
    assert "dados foram gravados" not in erros[0]
    assert "ERROR" in saida.err and "banco bloqueado" in saida.err
    assert not (tmp_path / "r.html").exists()
    assert "Relatório gravado" not in saida.out


def test_so_relatorio_banco_realmente_bloqueado_retorna_quatro(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """Outro processo segurando o arquivo: simulado por um subprocesso real."""
    import subprocess
    import sys

    banco = tmp_path / "indicadores.duckdb"
    _popular_banco(banco)
    codigo_filho = (
        "import duckdb, sys, time\n"
        f"c = duckdb.connect({str(banco)!r})\n"
        "print('pronto', flush=True)\n"
        "sys.stdin.readline()\n"
    )
    filho = subprocess.Popen(
        [sys.executable, "-I", "-c", codigo_filho],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert filho.stdout.readline().strip() == "pronto"
        with caplog.at_level(logging.ERROR, logger="indicadores.pipeline"):
            codigo = main(_args_so_relatorio(tmp_path))
    finally:
        filho.communicate("\n", timeout=30)

    assert codigo == 4
    assert len(_mensagens_error(caplog)) == 1
    assert not (tmp_path / "r.html").exists()


def test_so_relatorio_gerar_html_falhando_retorna_quatro_sem_dizer_que_gravou(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, capsys
):
    banco = tmp_path / "indicadores.duckdb"
    _popular_banco(banco)
    antes = _ler_banco(banco)
    rel = tmp_path / "r.html"
    rel.write_text("RELATORIO ANTERIOR", encoding="utf-8")
    monkeypatch.setattr("indicadores.__main__.gerar_html", _gerar_html_falho)

    with caplog.at_level(logging.ERROR, logger="indicadores.pipeline"):
        codigo = main(_args_so_relatorio(tmp_path))

    saida = capsys.readouterr()
    assert codigo == 4
    assert _mensagens_error(caplog) == ["falha ao gerar o relatório"]
    assert "dados foram gravados" not in saida.err
    assert "gravados normalmente" not in saida.err
    assert "falha forçada" in saida.err  # o traceback mostra a causa
    assert "Relatório gravado" not in saida.out
    assert rel.read_text(encoding="utf-8") == "RELATORIO ANTERIOR"
    pd.testing.assert_frame_equal(_ler_banco(banco), antes)


def test_so_relatorio_gravar_relatorio_falhando_retorna_quatro_sem_dizer_que_gravou(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, capsys
):
    banco = tmp_path / "indicadores.duckdb"
    _popular_banco(banco)
    rel = tmp_path / "r.html"
    rel.write_text("RELATORIO ANTERIOR", encoding="utf-8")

    def _gravar_falho(html, caminho):
        raise OSError("disco cheio")

    monkeypatch.setattr("indicadores.__main__.gravar_relatorio", _gravar_falho)

    with caplog.at_level(logging.ERROR, logger="indicadores.pipeline"):
        codigo = main(_args_so_relatorio(tmp_path))

    saida = capsys.readouterr()
    assert codigo == 4
    assert _mensagens_error(caplog) == ["falha ao gerar o relatório"]
    assert "dados foram gravados" not in saida.err
    assert "disco cheio" in saida.err
    assert "Relatório gravado" not in saida.out
    assert rel.read_text(encoding="utf-8") == "RELATORIO ANTERIOR"


def test_so_relatorio_falha_fecha_a_conexao(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    banco = tmp_path / "indicadores.duckdb"
    _popular_banco(banco)
    conexoes: list[duckdb.DuckDBPyConnection] = []

    modos: list[bool] = []

    def _abrir_espiao(caminho, *, somente_leitura=False):
        modos.append(somente_leitura)
        conexao = abrir_conexao(caminho, somente_leitura=somente_leitura)
        conexoes.append(conexao)
        return conexao

    monkeypatch.setattr("indicadores.__main__.abrir_conexao", _abrir_espiao)
    monkeypatch.setattr("indicadores.__main__.gerar_html", _gerar_html_falho)

    assert main(_args_so_relatorio(tmp_path)) == 4

    assert len(conexoes) == 1
    assert modos == [True]
    with pytest.raises(duckdb.Error):
        conexoes[0].execute("SELECT 1")


def test_so_relatorio_banco_em_memoria_retorna_quatro(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, capsys
):
    # Spec (passo a passo, item 2): `--banco :memory:` cai na checagem de
    # arquivo inexistente ("nada a ler") -> exit 4, sem abrir conexão.
    monkeypatch.setattr("indicadores.__main__.abrir_conexao", _falhar_se_chamado)
    monkeypatch.setattr("indicadores.__main__.criar_client", _falhar_se_chamado)

    with caplog.at_level(logging.ERROR, logger="indicadores.pipeline"):
        codigo = main(_args_so_relatorio(tmp_path, ":memory:"))

    saida = capsys.readouterr()
    assert codigo == 4
    assert _mensagens_error(caplog) == ["banco não encontrado: :memory:"]
    assert "banco não encontrado" in saida.err
    assert not (tmp_path / ":memory:").exists()
    assert not (tmp_path / "r.html").exists()
    assert "Relatório gravado" not in saida.out


# ===========================================================================
# Revisão de código (relatorio.md, casos 59-65)
# ===========================================================================


def test_main_so_relatorio_abre_banco_somente_leitura(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    banco = tmp_path / "indicadores.duckdb"
    _popular_banco(banco)
    chamadas: list[bool] = []

    def _espiao(caminho, *, somente_leitura=False):
        chamadas.append(somente_leitura)
        conexao = abrir_conexao(caminho, somente_leitura=somente_leitura)
        if somente_leitura:
            with pytest.raises(duckdb.Error):
                conexao.execute("DELETE FROM series")
        return conexao

    monkeypatch.setattr("indicadores.__main__.abrir_conexao", _espiao)

    assert main(_args_so_relatorio(tmp_path)) == 0
    assert chamadas == [True]

    # Fluxo normal: leitura e escrita.
    chamadas.clear()
    codigo = main(
        [
            "--banco",
            str(tmp_path / "outro.duckdb"),
            "--data-referencia",
            _DATA_REF,
            "--sem-relatorio",
        ],
        client=_cliente(_RESPOSTAS_RELATORIO),
    )
    assert codigo == 0
    assert chamadas == [False]


def test_main_so_relatorio_nao_altera_bytes_nem_mtime_do_banco(tmp_path: Path):
    import hashlib
    import os

    banco = tmp_path / "indicadores.duckdb"
    _popular_banco(banco)
    antes_hash = hashlib.sha256(banco.read_bytes()).hexdigest()
    antes_mtime = os.stat(banco).st_mtime_ns

    assert main(_args_so_relatorio(tmp_path)) == 0

    assert hashlib.sha256(banco.read_bytes()).hexdigest() == antes_hash
    assert os.stat(banco).st_mtime_ns == antes_mtime
    assert not list(tmp_path.glob("*.wal"))
    assert (tmp_path / "r.html").is_file()


def _print_que_falha(excecao):
    import builtins

    def _print(*args, **kwargs):
        if not args or str(args[0]).startswith("Relatório gravado em:"):
            raise excecao
        return builtins.print(*args, **kwargs)

    return _print


_EXCECOES_PRINT = [
    pytest.param(lambda: UnicodeEncodeError("ascii", "ç", 0, 1, "fora do codepage"), id="unicode"),
    pytest.param(lambda: OSError("stdout fechado"), id="oserror"),
]


@pytest.mark.parametrize(
    ("respostas", "esperado"),
    [
        pytest.param(_RESPOSTAS_RELATORIO, 0, id="todas_ok"),
        pytest.param({**_RESPOSTAS_RELATORIO, 433: 404}, 1, id="serie_404"),
    ],
)
@pytest.mark.parametrize("fabrica", _EXCECOES_PRINT)
def test_main_falha_ao_imprimir_sucesso_nao_escapa_e_mantem_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, fabrica, respostas, esperado
):
    rel = tmp_path / "r.html"
    conexoes: list[duckdb.DuckDBPyConnection] = []
    client = _cliente(respostas)

    def _espiao(caminho, **kw):
        conexao = abrir_conexao(caminho, **kw)
        conexoes.append(conexao)
        return conexao

    monkeypatch.setattr("indicadores.__main__.abrir_conexao", _espiao)
    monkeypatch.setattr("indicadores.__main__.criar_client", lambda: client)
    monkeypatch.setattr(cli_module, "print", _print_que_falha(fabrica()), raising=False)

    codigo = main(
        [
            "--banco",
            str(tmp_path / "indicadores.duckdb"),
            "--relatorio",
            str(rel),
            "--data-referencia",
            _DATA_REF,
        ]
    )

    saida = capsys.readouterr()
    assert codigo == esperado
    assert "Traceback" not in saida.err
    assert "R$ 5,3784" in rel.read_text(encoding="utf-8")
    linhas_aviso = [
        linha for linha in saida.err.splitlines() if "WARNING indicadores.pipeline:" in linha
    ]
    assert len(linhas_aviso) == 1
    assert str(rel) in linhas_aviso[0]
    assert "ERROR" not in saida.err
    assert "falha ao gerar o relatório" not in saida.err
    assert not _ler_banco(tmp_path / "indicadores.duckdb").empty
    assert client.is_closed
    assert len(conexoes) == 1
    with pytest.raises(duckdb.Error):
        conexoes[0].execute("SELECT 1")


@pytest.mark.parametrize("fabrica", _EXCECOES_PRINT)
def test_main_so_relatorio_falha_ao_imprimir_sucesso_mantem_exit_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, fabrica
):
    banco = tmp_path / "indicadores.duckdb"
    _popular_banco(banco)
    conexoes: list[duckdb.DuckDBPyConnection] = []

    def _espiao(caminho, **kw):
        conexao = abrir_conexao(caminho, **kw)
        conexoes.append(conexao)
        return conexao

    monkeypatch.setattr("indicadores.__main__.abrir_conexao", _espiao)
    monkeypatch.setattr(cli_module, "print", _print_que_falha(fabrica()), raising=False)

    codigo = main(_args_so_relatorio(tmp_path))

    saida = capsys.readouterr()
    assert codigo == 0
    assert (tmp_path / "r.html").is_file()
    assert "WARNING indicadores.pipeline:" in saida.err
    assert "ERROR" not in saida.err
    assert len(conexoes) == 1
    with pytest.raises(duckdb.Error):
        conexoes[0].execute("SELECT 1")
