"""Persistência dos dados limpos em DuckDB.

Recebe o `pandas.DataFrame` produzido por `limpeza.limpar` (colunas
`codigo`, `serie`, `data`, `valor`) e grava em uma tabela única
`indicadores`, com upsert por `(codigo, data)`. Não faz limpeza (isso já
aconteceu em `limpeza.py`) e não orquestra o pipeline de ponta a ponta.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

from indicadores.extracao import FUSO_BRASILIA

logger = logging.getLogger("indicadores.persistencia")

CAMINHO_PADRAO: Path = Path("dados/indicadores.duckdb")

_COLUNAS_ENTRADA: set[str] = {"codigo", "serie", "data", "valor"}
_COLUNAS_SAIDA: list[str] = ["codigo", "serie", "data", "valor", "atualizado_em"]
_RELACAO_TEMPORARIA = "_indicadores_entrada"

_CRIAR_TABELA_SQL = """
CREATE TABLE IF NOT EXISTS indicadores (
    codigo         INTEGER   NOT NULL,
    serie          VARCHAR   NOT NULL,
    data           DATE      NOT NULL,
    valor          DOUBLE    NOT NULL,
    atualizado_em  TIMESTAMP NOT NULL,
    PRIMARY KEY (codigo, data)
)
"""

_UPSERT_SQL = f"""
INSERT INTO indicadores
SELECT codigo, serie, CAST(data AS DATE), valor, atualizado_em
FROM {_RELACAO_TEMPORARIA}
ON CONFLICT (codigo, data) DO UPDATE SET
    serie = excluded.serie,
    valor = excluded.valor,
    atualizado_em = excluded.atualizado_em
"""

_CONTAR_EXISTENTES_SQL = f"""
SELECT count(*)
FROM {_RELACAO_TEMPORARIA} AS e
JOIN indicadores AS i
    ON i.codigo = e.codigo AND i.data = CAST(e.data AS DATE)
"""


class ErroPersistencia(Exception):
    """Erro de uso deste módulo, detectável antes de tocar o banco."""


@dataclass(frozen=True)
class ResultadoGravacao:
    """Contagem de linhas inseridas/atualizadas por uma chamada de `gravar`."""

    inseridos: int
    atualizados: int
    total: int


def _agora_brasilia() -> datetime:
    """Horário atual de Brasília, naive (sem tzinfo)."""
    return datetime.now(FUSO_BRASILIA).replace(tzinfo=None)


def abrir_conexao(caminho: str | Path = CAMINHO_PADRAO) -> duckdb.DuckDBPyConnection:
    """Abre (ou cria) o arquivo DuckDB em `caminho`.

    Cria o diretório pai de `caminho` se não existir, exceto quando
    `caminho == ":memory:"`. Não chama `criar_tabela` automaticamente.
    """
    if str(caminho) != ":memory:":
        Path(caminho).parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(caminho))


def criar_tabela(conexao: duckdb.DuckDBPyConnection) -> None:
    """Executa `CREATE TABLE IF NOT EXISTS indicadores (...)`.

    Idempotente: chamar múltiplas vezes na mesma conexão não recria nem
    apaga dados existentes.
    """
    conexao.execute(_CRIAR_TABELA_SQL)


def _validar_dados(dados: pd.DataFrame) -> None:
    """Valida colunas e duplicidade interna antes de abrir a transação."""
    colunas = set(dados.columns)
    faltando = _COLUNAS_ENTRADA - colunas
    sobrando = colunas - _COLUNAS_ENTRADA
    if faltando or sobrando:
        detalhes = []
        if faltando:
            detalhes.append(f"faltando: {sorted(faltando)}")
        if sobrando:
            detalhes.append(f"inesperadas: {sorted(sobrando)}")
        raise ErroPersistencia(
            "colunas de 'dados' inválidas (" + "; ".join(detalhes) + ")"
        )

    if dados.duplicated(subset=["codigo", "data"]).any():
        raise ErroPersistencia(
            "'dados' contém (codigo, data) duplicado internamente"
        )


def gravar(
    conexao: duckdb.DuckDBPyConnection,
    dados: pd.DataFrame,
    *,
    atualizado_em: datetime | None = None,
) -> ResultadoGravacao:
    """Grava `dados` na tabela `indicadores`, upsert por (codigo, data).

    Valida colunas e duplicidade interna antes de tocar o banco. Se
    `dados` estiver vazio, retorna `ResultadoGravacao(0, 0, 0)` sem abrir
    transação. Caso contrário, `gravar` abre a própria transação
    (`BEGIN`/`COMMIT`, com `ROLLBACK` em caso de exceção) — chamá-la com
    uma transação já aberta na mesma conexão resulta em erro nativo do
    DuckDB já no `BEGIN`, antes do `try`, então não há `ROLLBACK` nem
    `unregister` indevidos nesse caso. Não chama `criar_tabela`
    implicitamente.
    """
    _validar_dados(dados)

    total = len(dados)
    if total == 0:
        return ResultadoGravacao(inseridos=0, atualizados=0, total=0)

    valor_atualizado_em = (
        atualizado_em if atualizado_em is not None else _agora_brasilia()
    )

    entrada = dados.copy()
    entrada["atualizado_em"] = valor_atualizado_em

    conexao.execute("BEGIN TRANSACTION")
    registrado = False
    try:
        conexao.register(_RELACAO_TEMPORARIA, entrada)
        registrado = True
        linha = conexao.execute(_CONTAR_EXISTENTES_SQL).fetchone()
        atualizados = linha[0] if linha is not None else 0

        conexao.execute(_UPSERT_SQL)
        conexao.execute("COMMIT")
    except Exception:
        try:
            conexao.execute("ROLLBACK")
        except Exception:
            logger.exception("falha ao executar ROLLBACK")
        raise
    finally:
        if registrado:
            conexao.unregister(_RELACAO_TEMPORARIA)

    return ResultadoGravacao(
        inseridos=total - atualizados,
        atualizados=atualizados,
        total=total,
    )


def _aplicar_dtypes_saida(df: pd.DataFrame) -> pd.DataFrame:
    """Aplica os dtypes explícitos de saída de `ler`, nunca por inferência."""
    df = df.copy()
    df["codigo"] = df["codigo"].astype("int64")
    df["serie"] = df["serie"].astype(object)
    df["data"] = df["data"].astype("datetime64[ns]")
    df["valor"] = df["valor"].astype("float64")
    df["atualizado_em"] = df["atualizado_em"].astype("datetime64[ns]")
    return df


def ler(
    conexao: duckdb.DuckDBPyConnection,
    codigos: Iterable[int] | None = None,
) -> pd.DataFrame:
    """Lê a tabela `indicadores`, opcionalmente filtrando por `codigos`.

    Retorna DataFrame com colunas
    `["codigo", "serie", "data", "valor", "atualizado_em"]`, ordenado por
    `codigo` e `data`, com dtypes explícitos aplicados via `.astype(...)`.
    Se `codigos` for uma lista vazia, devolve um DataFrame vazio com as
    mesmas colunas/dtypes, sem consultar o banco.
    """
    if codigos is not None:
        codigos_lista = list(codigos)
        if not codigos_lista:
            return _aplicar_dtypes_saida(pd.DataFrame(columns=_COLUNAS_SAIDA))
    else:
        codigos_lista = None

    query = f"SELECT {', '.join(_COLUNAS_SAIDA)} FROM indicadores"
    parametros: list[int] = []
    if codigos_lista is not None:
        placeholders = ", ".join("?" for _ in codigos_lista)
        query += f" WHERE codigo IN ({placeholders})"
        parametros = codigos_lista
    query += " ORDER BY codigo, data"

    df = conexao.execute(query, parametros).df()

    return _aplicar_dtypes_saida(df).reset_index(drop=True)
