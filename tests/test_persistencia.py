"""Testes do módulo de persistência (DuckDB).

Todos os testes usam `duckdb.connect(":memory:")`, exceto o teste de
`abrir_conexao`, que grava um arquivo real dentro de `tmp_path`. Nenhum
teste cria arquivo `.duckdb` fora de `tmp_path`.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

import duckdb
import httpx
import pandas as pd
import pytest

from indicadores.extracao import SGS_BASE_URL, buscar_series
from indicadores.limpeza import limpar
from indicadores.persistencia import (
    ErroPersistencia,
    ResultadoGravacao,
    abrir_conexao,
    criar_tabela,
    gravar,
    ler,
)

COLUNAS_ENTRADA = ["codigo", "serie", "data", "valor"]
COLUNAS_SAIDA = ["codigo", "serie", "data", "valor", "atualizado_em"]


def _construir_dados(linhas: list[dict[str, object]]) -> pd.DataFrame:
    """Monta um DataFrame no formato de saída de `limpeza.limpar`.

    Colunas `["codigo", "serie", "data", "valor"]`, dtypes `int64`,
    `object`, `datetime64[ns]`, `float64`.
    """
    df = pd.DataFrame(linhas, columns=COLUNAS_ENTRADA)
    df["codigo"] = df["codigo"].astype("int64")
    df["serie"] = df["serie"].astype(object)
    df["data"] = pd.to_datetime(df["data"], dayfirst=True).astype("datetime64[ns]")
    df["valor"] = df["valor"].astype("float64")
    return df


def _dt(*args: int) -> datetime:
    """`datetime` naive fixo para os testes (sem tzinfo, de propósito).

    A coluna `atualizado_em` é `TIMESTAMP` sem fuso — ver `_agora_brasilia`
    em `indicadores.persistencia`. O `# noqa` documenta que a ausência de
    tzinfo aqui é intencional, não um esquecimento.
    """
    return datetime(*args)  # noqa: DTZ001


@pytest.fixture
def conexao():
    con = duckdb.connect(":memory:")
    criar_tabela(con)
    try:
        yield con
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 1: criar_tabela é idempotente
# ---------------------------------------------------------------------------


def test_criar_tabela_e_idempotente(conexao):
    # `conexao` já chamou `criar_tabela` uma vez na fixture.
    criar_tabela(conexao)

    dados = _construir_dados(
        [{"codigo": 432, "serie": "selic_meta", "data": "01/01/2020", "valor": 13.75}]
    )
    gravar(conexao, dados, atualizado_em=_dt(2025, 6, 15, 10, 0, 0))

    criar_tabela(conexao)

    assert len(ler(conexao)) == 1


# ---------------------------------------------------------------------------
# 2: gravar insere linhas novas
# ---------------------------------------------------------------------------


def test_gravar_insere_linhas_novas(conexao):
    atualizado_em = _dt(2025, 6, 15, 10, 0, 0)
    dados = _construir_dados(
        [
            {"codigo": 432, "serie": "selic_meta", "data": "01/01/2020", "valor": 13.75},
            {"codigo": 433, "serie": "ipca_mensal", "data": "01/02/2020", "valor": 0.25},
        ]
    )

    resultado = gravar(conexao, dados, atualizado_em=atualizado_em)

    assert resultado == ResultadoGravacao(inseridos=2, atualizados=0, total=2)
    lido = ler(conexao)
    assert len(lido) == 2

    linha_432 = lido.iloc[0]
    assert linha_432["codigo"] == 432
    assert linha_432["serie"] == "selic_meta"
    assert linha_432["data"] == pd.Timestamp("2020-01-01")
    assert linha_432["valor"] == 13.75
    assert linha_432["atualizado_em"] == pd.Timestamp(atualizado_em)

    linha_433 = lido.iloc[1]
    assert linha_433["codigo"] == 433
    assert linha_433["serie"] == "ipca_mensal"
    assert linha_433["data"] == pd.Timestamp("2020-02-01")
    assert linha_433["valor"] == 0.25
    assert linha_433["atualizado_em"] == pd.Timestamp(atualizado_em)


# ---------------------------------------------------------------------------
# 3: reexecução não duplica
# ---------------------------------------------------------------------------


def test_gravar_reexecucao_nao_duplica(conexao):
    dados = _construir_dados(
        [{"codigo": 432, "serie": "selic_meta", "data": "01/01/2020", "valor": 13.75}]
    )

    gravar(conexao, dados, atualizado_em=_dt(2025, 6, 15, 10, 0, 0))
    total_apos_primeira = len(ler(conexao))

    resultado = gravar(conexao, dados, atualizado_em=_dt(2025, 6, 15, 11, 0, 0))
    total_apos_segunda = len(ler(conexao))

    assert total_apos_primeira == total_apos_segunda == 1
    assert resultado.inseridos == 0
    assert resultado.atualizados == resultado.total == 1


# ---------------------------------------------------------------------------
# 4: revisão atualiza valor e atualizado_em
# ---------------------------------------------------------------------------


def test_gravar_revisao_atualiza_valor_e_atualizado_em(conexao):
    t1 = _dt(2025, 6, 15, 10, 0, 0)
    t2 = _dt(2025, 6, 16, 8, 0, 0)

    dados_v1 = _construir_dados(
        [{"codigo": 432, "serie": "selic_meta", "data": "01/01/2020", "valor": 13.75}]
    )
    gravar(conexao, dados_v1, atualizado_em=t1)

    dados_v2 = _construir_dados(
        [{"codigo": 432, "serie": "selic_meta", "data": "01/01/2020", "valor": 14.00}]
    )
    gravar(conexao, dados_v2, atualizado_em=t2)

    lido = ler(conexao)
    assert len(lido) == 1
    linha = lido.iloc[0]
    assert linha["valor"] == 14.00
    assert linha["atualizado_em"] == pd.Timestamp(t2)


# ---------------------------------------------------------------------------
# 5: DataFrame vazio não faz nada
# ---------------------------------------------------------------------------


def test_gravar_dataframe_vazio_nao_faz_nada(conexao):
    dados_iniciais = _construir_dados(
        [{"codigo": 432, "serie": "selic_meta", "data": "01/01/2020", "valor": 13.75}]
    )
    gravar(conexao, dados_iniciais, atualizado_em=_dt(2025, 6, 15, 10, 0, 0))
    total_antes = len(ler(conexao))

    dados_vazio = _construir_dados([])
    resultado = gravar(conexao, dados_vazio, atualizado_em=_dt(2025, 6, 15, 10, 0, 0))

    assert resultado == ResultadoGravacao(0, 0, 0)
    assert len(ler(conexao)) == total_antes


# ---------------------------------------------------------------------------
# 6-7: validação de colunas
# ---------------------------------------------------------------------------


def test_gravar_colunas_faltando_levanta_erro_persistencia(conexao):
    dados = pd.DataFrame(
        {
            "codigo": pd.array([432], dtype="int64"),
            "serie": pd.array(["selic_meta"], dtype=object),
            "data": pd.to_datetime(["2020-01-01"]).astype("datetime64[ns]"),
        }
    )

    with pytest.raises(ErroPersistencia):
        gravar(conexao, dados, atualizado_em=_dt(2025, 6, 15, 10, 0, 0))

    assert len(ler(conexao)) == 0


def test_gravar_colunas_inesperadas_levanta_erro_persistencia(conexao):
    dados = _construir_dados(
        [{"codigo": 432, "serie": "selic_meta", "data": "01/01/2020", "valor": 13.75}]
    )
    dados["coluna_extra"] = "x"

    with pytest.raises(ErroPersistencia):
        gravar(conexao, dados, atualizado_em=_dt(2025, 6, 15, 10, 0, 0))

    assert len(ler(conexao)) == 0


# ---------------------------------------------------------------------------
# 8: duplicata interna levanta ErroPersistencia
# ---------------------------------------------------------------------------


def test_gravar_duplicata_interna_levanta_erro_persistencia(conexao):
    dados = _construir_dados(
        [
            {"codigo": 432, "serie": "selic_meta", "data": "01/01/2020", "valor": 13.75},
            {"codigo": 432, "serie": "selic_meta", "data": "01/01/2020", "valor": 14.00},
        ]
    )

    with pytest.raises(ErroPersistencia):
        gravar(conexao, dados, atualizado_em=_dt(2025, 6, 15, 10, 0, 0))

    assert len(ler(conexao)) == 0


# ---------------------------------------------------------------------------
# 9: rollback em erro do banco (serie=None)
# ---------------------------------------------------------------------------


def test_gravar_rollback_em_erro_do_banco(conexao):
    dados = _construir_dados(
        [
            {"codigo": 432, "serie": "selic_meta", "data": "01/01/2020", "valor": 13.75},
            {"codigo": 432, "serie": None, "data": "02/01/2020", "valor": 14.00},
            {"codigo": 433, "serie": "ipca_mensal", "data": "01/01/2020", "valor": 0.25},
        ]
    )

    with pytest.raises(duckdb.ConstraintException):
        gravar(conexao, dados, atualizado_em=_dt(2025, 6, 15, 10, 0, 0))

    assert len(ler(conexao)) == 0

    with pytest.raises(duckdb.CatalogException):
        conexao.execute("SELECT * FROM _indicadores_entrada").fetchall()


# ---------------------------------------------------------------------------
# 10: tipos gravados são DATE e DOUBLE
# ---------------------------------------------------------------------------


def test_gravar_tipos_gravados_sao_date_e_double(conexao):
    dados = _construir_dados(
        [{"codigo": 432, "serie": "selic_meta", "data": "01/01/2020", "valor": 13.75}]
    )
    gravar(conexao, dados, atualizado_em=_dt(2025, 6, 15, 10, 0, 0))

    tipos = conexao.execute(
        "SELECT typeof(data), typeof(valor) FROM indicadores LIMIT 1"
    ).fetchone()

    assert tipos == ("DATE", "DOUBLE")


# ---------------------------------------------------------------------------
# 11: abrir_conexao cria diretório pai
# ---------------------------------------------------------------------------


def test_abrir_conexao_cria_diretorio_pai(tmp_path: Path):
    caminho = tmp_path / "sub" / "indicadores.duckdb"
    assert not caminho.parent.exists()

    con = abrir_conexao(caminho)
    try:
        assert caminho.parent.exists()
        criar_tabela(con)
        dados = _construir_dados(
            [{"codigo": 432, "serie": "selic_meta", "data": "01/01/2020", "valor": 13.75}]
        )
        resultado = gravar(con, dados, atualizado_em=_dt(2025, 6, 15, 10, 0, 0))
        assert resultado.total == 1
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 12: ler filtra por codigos
# ---------------------------------------------------------------------------


def test_ler_filtra_por_codigos(conexao):
    dados = _construir_dados(
        [
            {"codigo": 432, "serie": "selic_meta", "data": "01/01/2020", "valor": 13.75},
            {"codigo": 433, "serie": "ipca_mensal", "data": "01/01/2020", "valor": 0.25},
        ]
    )
    gravar(conexao, dados, atualizado_em=_dt(2025, 6, 15, 10, 0, 0))

    lido = ler(conexao, codigos=[432])

    assert len(lido) == 1
    assert lido.iloc[0]["codigo"] == 432


# ---------------------------------------------------------------------------
# 13: ler retorna dtypes explícitos
# ---------------------------------------------------------------------------


def test_ler_retorna_dtypes_explicitos(conexao):
    dados = _construir_dados(
        [{"codigo": 432, "serie": "selic_meta", "data": "01/01/2020", "valor": 13.75}]
    )
    gravar(conexao, dados, atualizado_em=_dt(2025, 6, 15, 10, 0, 0))

    lido = ler(conexao)

    assert list(lido.columns) == COLUNAS_SAIDA
    assert str(lido["codigo"].dtype) == "int64"
    assert str(lido["serie"].dtype) == "object"
    assert str(lido["data"].dtype) == "datetime64[ns]"
    assert str(lido["valor"].dtype) == "float64"
    assert str(lido["atualizado_em"].dtype) == "datetime64[ns]"


# ---------------------------------------------------------------------------
# 14: ler ordenado por codigo e data
# ---------------------------------------------------------------------------


def test_ler_ordenado_por_codigo_e_data(conexao):
    dados = _construir_dados(
        [
            {"codigo": 433, "serie": "ipca_mensal", "data": "01/02/2020", "valor": 0.25},
            {"codigo": 432, "serie": "selic_meta", "data": "01/03/2020", "valor": 13.75},
            {"codigo": 432, "serie": "selic_meta", "data": "01/01/2020", "valor": 13.00},
        ]
    )
    gravar(conexao, dados, atualizado_em=_dt(2025, 6, 15, 10, 0, 0))

    lido = ler(conexao)

    assert list(lido["codigo"]) == [432, 432, 433]
    assert list(lido["data"]) == [
        pd.Timestamp("2020-01-01"),
        pd.Timestamp("2020-03-01"),
        pd.Timestamp("2020-02-01"),
    ]
    assert list(lido.index) == list(range(len(lido)))


# ---------------------------------------------------------------------------
# 15: atualizado_em default usa hora de Brasília, naive
# ---------------------------------------------------------------------------


def test_gravar_atualizado_em_default_usa_hora_de_brasilia(conexao, monkeypatch):
    instante_esperado = _dt(2025, 6, 15, 9, 30, 0)

    class DatetimeFalso:
        @staticmethod
        def now(fuso=None):
            return datetime(2025, 6, 15, 9, 30, 0, tzinfo=fuso)

    monkeypatch.setattr("indicadores.persistencia.datetime", DatetimeFalso)

    dados = _construir_dados(
        [{"codigo": 432, "serie": "selic_meta", "data": "01/01/2020", "valor": 13.75}]
    )
    gravar(conexao, dados)

    lido = ler(conexao)
    atualizado_em = lido.iloc[0]["atualizado_em"]
    assert atualizado_em == pd.Timestamp(instante_esperado)
    assert atualizado_em.tzinfo is None


# ---------------------------------------------------------------------------
# 16: relação temporária removida no sucesso e no erro
# ---------------------------------------------------------------------------


def test_gravar_remove_relacao_temporaria_no_sucesso_e_no_erro(conexao):
    dados_ok = _construir_dados(
        [{"codigo": 432, "serie": "selic_meta", "data": "01/01/2020", "valor": 13.75}]
    )
    gravar(conexao, dados_ok, atualizado_em=_dt(2025, 6, 15, 10, 0, 0))

    with pytest.raises(duckdb.CatalogException):
        conexao.execute("SELECT * FROM _indicadores_entrada").fetchall()

    dados_com_erro = _construir_dados(
        [{"codigo": 433, "serie": None, "data": "01/01/2020", "valor": 1.0}]
    )
    with pytest.raises(duckdb.ConstraintException):
        gravar(conexao, dados_com_erro, atualizado_em=_dt(2025, 6, 15, 10, 0, 0))

    with pytest.raises(duckdb.CatalogException):
        conexao.execute("SELECT * FROM _indicadores_entrada").fetchall()

    # A relação não ficou "presa": um novo `gravar` reaproveitando o
    # mesmo nome funciona normalmente, sem erro de "já registrada".
    resultado = gravar(conexao, dados_ok, atualizado_em=_dt(2025, 6, 15, 11, 0, 0))
    assert resultado.total == 1


# ---------------------------------------------------------------------------
# Casos adicionais pedidos explicitamente pelo coordenador.
# ---------------------------------------------------------------------------


def test_ler_lista_codigos_vazia_retorna_dataframe_vazio(conexao):
    dados = _construir_dados(
        [{"codigo": 432, "serie": "selic_meta", "data": "01/01/2020", "valor": 13.75}]
    )
    gravar(conexao, dados, atualizado_em=_dt(2025, 6, 15, 10, 0, 0))

    lido = ler(conexao, codigos=[])

    assert lido.empty
    assert list(lido.columns) == COLUNAS_SAIDA
    assert str(lido["codigo"].dtype) == "int64"
    assert str(lido["serie"].dtype) == "object"
    assert str(lido["data"].dtype) == "datetime64[ns]"
    assert str(lido["valor"].dtype) == "float64"
    assert str(lido["atualizado_em"].dtype) == "datetime64[ns]"


def test_pipeline_extracao_limpeza_persistencia_encadeados(conexao):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"data": "01/01/2020", "valor": "13,75"}])

    client = httpx.Client(base_url=SGS_BASE_URL, transport=httpx.MockTransport(handler))
    try:
        resultados = buscar_series(
            codigos=[432], client=client, data_referencia=date(2025, 6, 15)
        )
    finally:
        client.close()

    resultado_limpeza = limpar(resultados)
    gravar(
        conexao,
        resultado_limpeza.dados,
        atualizado_em=_dt(2025, 6, 15, 10, 0, 0),
    )

    lido = ler(conexao)

    assert len(lido) == 1
    linha = lido.iloc[0]
    assert linha["codigo"] == 432
    assert linha["valor"] == 13.75
    assert linha["data"] == pd.Timestamp("2020-01-01")


# ---------------------------------------------------------------------------
# 17: ler em tabela vazia (sem nenhum gravar) devolve DataFrame vazio
# ---------------------------------------------------------------------------


def test_ler_tabela_vazia_retorna_dataframe_vazio_com_dtypes(conexao):
    lido = ler(conexao)

    assert lido.empty
    assert list(lido.columns) == COLUNAS_SAIDA
    assert str(lido["codigo"].dtype) == "int64"
    assert str(lido["serie"].dtype) == "object"
    assert str(lido["data"].dtype) == "datetime64[ns]"
    assert str(lido["valor"].dtype) == "float64"
    assert str(lido["atualizado_em"].dtype) == "datetime64[ns]"


# ---------------------------------------------------------------------------
# Conexões fake/dublê usadas nos testes 18 e 19: envolvem uma conexão real
# `:memory:` e delegam `execute`/`register`/`unregister`, permitindo
# provocar falhas específicas que não seriam possíveis via monkeypatch
# direto em `duckdb.DuckDBPyConnection` (extensão C).
# ---------------------------------------------------------------------------


class _ConexaoFalhaNoRollback:
    """Delega tudo à conexão real, exceto `execute("ROLLBACK")`, que falha."""

    def __init__(self, conexao_real: duckdb.DuckDBPyConnection) -> None:
        self._conexao_real = conexao_real

    def execute(self, sql, *args, **kwargs):
        if isinstance(sql, str) and sql.strip().upper() == "ROLLBACK":
            raise RuntimeError("rollback fake falhou")
        return self._conexao_real.execute(sql, *args, **kwargs)

    def register(self, *args, **kwargs):
        return self._conexao_real.register(*args, **kwargs)

    def unregister(self, *args, **kwargs):
        return self._conexao_real.unregister(*args, **kwargs)


class _ConexaoFalhaNoRegister:
    """Delega tudo à conexão real, exceto `register(...)`, que falha.

    Conta as chamadas de `unregister` recebidas, em `chamadas_unregister`.
    """

    def __init__(self, conexao_real: duckdb.DuckDBPyConnection) -> None:
        self._conexao_real = conexao_real
        self.chamadas_unregister: list[tuple] = []

    def execute(self, sql, *args, **kwargs):
        return self._conexao_real.execute(sql, *args, **kwargs)

    def register(self, *args, **kwargs):
        raise RuntimeError("register fake falhou")

    def unregister(self, *args, **kwargs):
        self.chamadas_unregister.append(args)
        return self._conexao_real.unregister(*args, **kwargs)


# ---------------------------------------------------------------------------
# 18: falha no ROLLBACK preserva a exceção original, loga a falha
# ---------------------------------------------------------------------------


def test_gravar_falha_no_rollback_preserva_erro_original(caplog: pytest.LogCaptureFixture):
    conexao_real = duckdb.connect(":memory:")
    criar_tabela(conexao_real)
    fake = _ConexaoFalhaNoRollback(conexao_real)

    dados = _construir_dados(
        [{"codigo": 432, "serie": None, "data": "01/01/2020", "valor": 1.0}]
    )

    try:
        with (
            caplog.at_level(logging.ERROR, logger="indicadores.persistencia"),
            pytest.raises(duckdb.ConstraintException),
        ):
            gravar(fake, dados, atualizado_em=_dt(2025, 6, 15, 10, 0, 0))
    finally:
        conexao_real.close()

    mensagens = [registro.getMessage() for registro in caplog.records]
    assert any("falha ao executar ROLLBACK" in mensagem for mensagem in mensagens)
    assert any(registro.levelno == logging.ERROR for registro in caplog.records)


# ---------------------------------------------------------------------------
# 19: falha no register não chama unregister
# ---------------------------------------------------------------------------


def test_gravar_falha_no_register_nao_chama_unregister():
    conexao_real = duckdb.connect(":memory:")
    criar_tabela(conexao_real)
    fake = _ConexaoFalhaNoRegister(conexao_real)

    dados = _construir_dados(
        [{"codigo": 432, "serie": "selic_meta", "data": "01/01/2020", "valor": 13.75}]
    )

    try:
        with pytest.raises(RuntimeError, match="register fake falhou"):
            gravar(fake, dados, atualizado_em=_dt(2025, 6, 15, 10, 0, 0))

        assert len(fake.chamadas_unregister) == 0
    finally:
        conexao_real.close()


# ---------------------------------------------------------------------------
# 20: gravar com transação já aberta levanta erro sem alterar a tabela
# ---------------------------------------------------------------------------


def test_gravar_com_transacao_ja_aberta_levanta_erro_sem_alterar_tabela(conexao):
    dados_iniciais = _construir_dados(
        [{"codigo": 432, "serie": "selic_meta", "data": "01/01/2020", "valor": 13.75}]
    )
    gravar(conexao, dados_iniciais, atualizado_em=_dt(2025, 6, 15, 10, 0, 0))

    conexao.execute("BEGIN TRANSACTION")

    outro_df = _construir_dados(
        [{"codigo": 433, "serie": "ipca_mensal", "data": "01/01/2020", "valor": 0.25}]
    )

    try:
        with pytest.raises(duckdb.TransactionException):
            gravar(conexao, outro_df, atualizado_em=_dt(2025, 6, 15, 11, 0, 0))
    finally:
        conexao.execute("ROLLBACK")

    lido = ler(conexao)
    assert len(lido) == 1
    assert lido.iloc[0]["codigo"] == 432


# ---------------------------------------------------------------------------
# Decisão 12 (relatorio.md, caso 58): abrir_conexao somente leitura
# ---------------------------------------------------------------------------


def test_abrir_conexao_somente_leitura(tmp_path: Path):
    caminho = tmp_path / "banco" / "indicadores.duckdb"
    con = abrir_conexao(caminho)
    try:
        criar_tabela(con)
        gravar(
            con,
            _construir_dados(
                [{"codigo": 432, "serie": "selic_meta", "data": "01/01/2020", "valor": 13.75}]
            ),
            atualizado_em=_dt(2025, 6, 15, 10, 0, 0),
        )
    finally:
        con.close()

    # (a) leitura funciona; escrita levanta o erro nativo do duckdb.
    ro = abrir_conexao(caminho, somente_leitura=True)
    try:
        assert len(ler(ro)) == 1
        with pytest.raises(duckdb.Error):
            ro.execute("CREATE TABLE intruso (x INTEGER)")
        with pytest.raises(duckdb.Error):
            ro.execute("INSERT INTO intruso VALUES (1)")
    finally:
        ro.close()

    # (c) default preserva o comportamento anterior (cria o diretório pai).
    novo = tmp_path / "outro" / "dir" / "b.duckdb"
    con2 = abrir_conexao(novo)
    try:
        assert novo.parent.is_dir()
        con2.execute("CREATE TABLE t (x INTEGER)")
        con2.execute("INSERT INTO t VALUES (1)")
    finally:
        con2.close()

    # (b) inexistente: erro e nada criado (nem arquivo, nem diretório pai).
    ausente = tmp_path / "nao_existe_dir" / "x.duckdb"
    with pytest.raises(duckdb.Error):
        abrir_conexao(ausente, somente_leitura=True)
    assert not ausente.exists()
    assert not ausente.parent.exists()
