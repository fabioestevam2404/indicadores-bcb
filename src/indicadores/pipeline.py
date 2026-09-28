"""Orquestração do pipeline de ponta a ponta: extração → limpeza → persistência.

Este módulo não implementa nenhuma regra nova de extração, limpeza ou
persistência — só encadeia, na ordem certa, o que já existe em
`extracao.py`, `limpeza.py` e `persistencia.py`. Conexão e client HTTP
são sempre injetados por quem chama.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime

import duckdb
import httpx

from indicadores.extracao import FUSO_BRASILIA, SERIES, buscar_series
from indicadores.limpeza import Descarte, SerieIgnorada, limpar
from indicadores.persistencia import ResultadoGravacao, criar_tabela, gravar

# Ordem fixa em que os três motivos de descarte conhecidos aparecem no
# resumo, mesmo com contagem 0 (ver `formatar_resumo`).
_MOTIVOS_DESCARTE: tuple[str, ...] = (
    "data_invalida",
    "valor_invalido",
    "data_duplicada",
)


@dataclass(frozen=True)
class ResumoPipeline:
    """Resultado consolidado de uma execução de `executar`."""

    gravacoes: dict[int, ResultadoGravacao]
    nomes: dict[int, str]
    descartes: list[Descarte]
    series_ignoradas: list[SerieIgnorada]


def executar(
    *,
    conexao: duckdb.DuckDBPyConnection,
    client: httpx.Client | None = None,
    codigos: Iterable[int] | None = None,
    data_referencia: date | None = None,
    esperar: Callable[[float], None] = time.sleep,
    atualizado_em: datetime | None = None,
) -> ResumoPipeline:
    """Executa extração → limpeza → persistência para `codigos`.

    Chama `criar_tabela(conexao)` (idempotente) antes de qualquer
    gravação, busca e limpa os dados, e então grava uma vez por código
    (não em uma única transação para todo o DataFrame), para poder
    reportar inseridos/atualizados por série. Não abre nem fecha
    `conexao` nem `client` — ambos são sempre injetados por quem chama.
    """
    criar_tabela(conexao)

    resultados = buscar_series(
        codigos,
        client=client,
        data_referencia=data_referencia,
        esperar=esperar,
    )
    resultado_limpeza = limpar(resultados)

    valor_atualizado_em = (
        atualizado_em
        if atualizado_em is not None
        else datetime.now(FUSO_BRASILIA).replace(tzinfo=None)
    )

    gravacoes: dict[int, ResultadoGravacao] = {}
    nomes: dict[int, str] = {}

    dados = resultado_limpeza.dados
    for codigo_np in sorted(dados["codigo"].unique()):
        codigo = int(codigo_np)
        subconjunto = dados[dados["codigo"] == codigo]
        gravacoes[codigo] = gravar(
            conexao, subconjunto, atualizado_em=valor_atualizado_em
        )
        nomes[codigo] = subconjunto["serie"].iloc[0]

    return ResumoPipeline(
        gravacoes=gravacoes,
        nomes=nomes,
        descartes=resultado_limpeza.descartes,
        series_ignoradas=resultado_limpeza.series_ignoradas,
    )


def _nome_serie_ignorada(codigo: int) -> str:
    """Nome de exibição para série ignorada.

    Usa o mesmo fallback de `SERIES[codigo]`/`f"serie_{codigo}"` já
    usado em `limpeza.limpar` (bloco `nome_serie = SERIES.get(codigo)
    ...` em `src/indicadores/limpeza.py`). Séries ignoradas nunca
    chegam a `limpeza.limpar`, então o fallback é recalculado aqui —
    duplicado de propósito, em vez de tornar público um detalhe
    interno de `limpeza.py` só para reaproveitar essa linha.
    """
    return SERIES.get(codigo, f"serie_{codigo}")


def formatar_resumo(resumo: ResumoPipeline) -> str:
    """Formata `resumo` como texto simples em português, para stdout.

    Função pura (sem I/O), testável por comparação de string.
    """
    linhas: list[str] = ["Séries gravadas:"]
    if resumo.gravacoes:
        for codigo in sorted(resumo.gravacoes):
            gravacao = resumo.gravacoes[codigo]
            nome = resumo.nomes[codigo]
            linhas.append(
                f"  {codigo} ({nome}): {gravacao.inseridos} inseridos, "
                f"{gravacao.atualizados} atualizados"
            )
    else:
        linhas.append("  (nenhuma)")

    linhas.append("")
    linhas.append("Descartes por motivo:")
    contagens = Counter(descarte.motivo for descarte in resumo.descartes)
    for motivo in _MOTIVOS_DESCARTE:
        linhas.append(f"  {motivo}: {contagens.get(motivo, 0)}")

    linhas.append("")
    linhas.append("Séries ignoradas:")
    if resumo.series_ignoradas:
        for ignorada in sorted(resumo.series_ignoradas, key=lambda si: si.codigo):
            nome = _nome_serie_ignorada(ignorada.codigo)
            linhas.append(f"  {ignorada.codigo} ({nome}): {ignorada.motivo}")
    else:
        linhas.append("  (nenhuma)")

    return "\n".join(linhas)
