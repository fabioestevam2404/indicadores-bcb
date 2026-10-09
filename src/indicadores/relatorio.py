"""Relatório HTML autocontido com os indicadores gravados (Selic, IPCA, PTAX).

Núcleo puro: `gerar_html` recebe um `pandas.DataFrame` (colunas `codigo`,
`data`, `valor`, como em `analise.py`) e a data/hora de geração e devolve a
string HTML. Nenhum I/O ali: não acessa banco nem rede e não lê o relógio.
`gravar_relatorio` é a única função com I/O (escrita atômica do arquivo).

O HTML não usa recurso externo: CSS inline, gráficos em SVG gerado aqui e
um único `<script>` mínimo, inline, só para o tooltip. A formatação pt-BR
(vírgula decimal, dd/mm/aaaa) é só de exibição e não depende de `locale`.
Os cálculos de negócio vêm de `analise.py`; aqui só há apresentação.
"""

from __future__ import annotations

import html
import json
import math
import os
from datetime import datetime
from pathlib import Path

import pandas as pd

from indicadores.analise import (
    CODIGO_IPCA_MENSAL,
    CODIGO_PTAX_VENDA,
    CODIGO_SELIC_META,
    ipca_acumulado_12m,
    mudancas_selic,
    ptax_mensal,
)

RELATORIO_PADRAO: Path = Path("relatorio.html")
JANELA_ANOS: int = 5
ESPACO_ENTRE_BARRAS: int = 2
LARGURA_MAXIMA_BARRA: int = 24

_MESES: tuple[str, ...] = (
    "jan", "fev", "mar", "abr", "mai", "jun",
    "jul", "ago", "set", "out", "nov", "dez",
)  # fmt: skip

_NOMES_PADRAO: dict[int, str] = {
    CODIGO_SELIC_META: "selic_meta",
    CODIGO_IPCA_MENSAL: "ipca_mensal",
    CODIGO_PTAX_VENDA: "dolar_ptax_venda",
}

_TITULO = "Indicadores Econômicos — Banco Central do Brasil"
_SEM_DADOS = "Sem dados suficientes"
_VARIACAO_INDISPONIVEL = "variação indisponível"
_TRACO = "—"
_MENOS = "−"  # U+2212

# Geometria dos gráficos (unidades do viewBox).
_LARGURA = 720
_ALTURA = 320
_MARGEM_ESQ = 64
_MARGEM_DIR = 84
_MARGEM_TOPO = 16
_MARGEM_BASE = 34
_X0 = float(_MARGEM_ESQ)
_X1 = float(_LARGURA - _MARGEM_DIR)
_Y0 = float(_MARGEM_TOPO)
_Y1 = float(_ALTURA - _MARGEM_BASE)
_RAIO_BARRAS = 4.0
_MAX_MARCAS_X = 6


# --------------------------------------------------------------------------
# Formatação de exibição (pt-BR), sem locale
# --------------------------------------------------------------------------


def _finito(valor: object) -> bool:
    """True se `valor` é um número real finito."""
    try:
        return math.isfinite(float(valor))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def formatar_numero(valor: float, casas: int) -> str:
    """Formata `valor` em pt-BR: milhar com ponto, decimal com vírgula.

    Negativo usa U+2212; resultado que arredonda para zero fica sem sinal.
    Valor não finito vira `—`.
    """
    if not _finito(valor):
        return _TRACO
    numero = float(valor)
    texto = f"{abs(numero):,.{casas}f}"
    texto = texto.translate(str.maketrans({",": ".", ".": ","}))
    if numero < 0 and any(c in "123456789" for c in texto):
        return _MENOS + texto
    return texto


def formatar_percentual(valor: float, casas: int = 2) -> str:
    """Formata `valor` como percentual pt-BR (`14,25%`)."""
    return formatar_numero(valor, casas) + "%"


def formatar_moeda(valor: float, casas: int = 4) -> str:
    """Formata `valor` em reais (`R$ 5,3784`)."""
    return "R$ " + formatar_numero(valor, casas)


def formatar_data(data) -> str:
    """Formata uma data como `dd/mm/aaaa`."""
    d = pd.Timestamp(data)
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def formatar_mes(data) -> str:
    """Formata uma data como mês abreviado e ano (`ago/2026`)."""
    d = pd.Timestamp(data)
    return f"{_MESES[d.month - 1]}/{d.year}"


def formatar_variacao(valor: float, unidade: str) -> str:
    """Formata uma variação com seta, sinal e texto (nunca só cor).

    A classificação (alta/queda/estável) usa o valor já arredondado para as
    2 casas exibidas. `unidade` é `"p.p."` ou `"%"`.
    """
    if not _finito(valor):
        return _VARIACAO_INDISPONIVEL
    sufixo = f" {unidade}" if unidade == "p.p." else unidade
    texto = formatar_numero(valor, 2)
    if texto == "0,00":
        return f"▶ estável (0,00{sufixo})"
    if float(valor) > 0:
        return f"▲ alta de +{texto}{sufixo}"
    return f"▼ queda de {texto}{sufixo}"


# --------------------------------------------------------------------------
# Eixo
# --------------------------------------------------------------------------


def calcular_marcas_eixo(minimo: float, maximo: float) -> list[float]:
    """Marcas "redondas" (4 a 6) para o eixo Y, cobrindo `[minimo, maximo]`.

    Passos candidatos `{1, 2, 2.5, 5} x 10^k`; escolhe o menor passo que
    dá no máximo 6 marcas. Intervalo degenerado é expandido antes.
    """
    minimo = float(minimo)
    maximo = float(maximo)
    if minimo > maximo:
        minimo, maximo = maximo, minimo
    if minimo == maximo:
        folga = max(1.0, abs(minimo) * 0.05)
        minimo, maximo = minimo - folga, maximo + folga

    candidatos = sorted(
        base * 10.0**k for k in range(-6, 9) for base in (1, 2, 2.5, 5)
    )
    passo = candidatos[-1]
    primeiro = ultimo = 0
    for candidato in candidatos:
        primeiro = math.floor(minimo / candidato + 1e-9)
        ultimo = math.ceil(maximo / candidato - 1e-9)
        if ultimo - primeiro + 1 <= 6:
            passo = candidato
            break
    # Garantias contra ruído de ponto flutuante nas bordas.
    while primeiro * passo > minimo:
        primeiro -= 1
    while ultimo * passo < maximo:
        ultimo += 1
    return [round(k * passo, 10) for k in range(primeiro, ultimo + 1)]


def _casas_do_passo(marcas: list[float]) -> int:
    """Casas decimais (0 a 4) necessárias para escrever as marcas."""
    if len(marcas) < 2:
        return 0
    passo = marcas[1] - marcas[0]
    for casas in range(4):
        escalado = passo * 10**casas
        if abs(escalado - round(escalado)) < 1e-6:
            return casas
    return 4


# --------------------------------------------------------------------------
# Utilidades de HTML e de dados
# --------------------------------------------------------------------------


def _e(texto: object) -> str:
    """Escapa texto para interpolar no HTML."""
    return html.escape(str(texto), quote=True)


def _c(valor: float) -> str:
    """Coordenada SVG: ponto decimal e 1 casa (único lugar sem vírgula)."""
    return f"{valor:.1f}"


def _serie(dados: pd.DataFrame, codigo: int) -> pd.DataFrame:
    """Linhas `data`/`valor` de um código, ordenadas; não altera `dados`."""
    recorte = dados.loc[dados["codigo"] == codigo, ["data", "valor"]].copy()
    return recorte.sort_values("data").reset_index(drop=True)


def _corte_janela(ultima_data) -> pd.Timestamp:
    """Primeira data da janela de `JANELA_ANOS` anos que termina em `ultima_data`."""
    return pd.Timestamp(ultima_data) - pd.DateOffset(years=JANELA_ANOS)


def _janela(serie: pd.DataFrame) -> pd.DataFrame:
    """Aplica a janela de 5 anos contada da última data da própria série."""
    if serie.empty:
        return serie
    corte = _corte_janela(serie["data"].max())
    return serie[serie["data"] >= corte].reset_index(drop=True)


def _variacao_percentual(valor: float, referencia: float) -> float | None:
    """`(valor / referencia - 1) * 100`; None se referência 0 ou não finita."""
    if not _finito(valor) or not _finito(referencia) or float(referencia) == 0.0:
        return None
    return (float(valor) / float(referencia) - 1.0) * 100.0


def _indice_mes(data) -> int:
    """Índice linear de mês (ano * 12 + mês - 1)."""
    d = pd.Timestamp(data)
    return d.year * 12 + d.month - 1


def _vazio(texto: str = _SEM_DADOS) -> str:
    return f'<p class="vazio">{_e(texto)}</p>'


def _html_variacao(valor: float | None, unidade: str) -> str:
    """Variação em `<span class="variacao">` ou aviso de indisponível."""
    if valor is None or not _finito(valor):
        return f'<span class="sem-variacao">{_VARIACAO_INDISPONIVEL}</span>'
    return f'<span class="variacao">{_e(formatar_variacao(valor, unidade))}</span>'


# --------------------------------------------------------------------------
# Cards
# --------------------------------------------------------------------------


def _card(id_card: str, titulo: str, corpo: str) -> str:
    return (
        f'<article class="card" id="{id_card}">'
        f'<p class="card-titulo">{_e(titulo)}</p>{corpo}</article>'
    )


def _card_selic(selic: pd.DataFrame, mudancas: pd.DataFrame) -> str:
    if selic.empty:
        return _card("card-selic", "Selic meta", _vazio())
    atual = formatar_percentual(selic["valor"].iloc[-1])
    corpo = (
        f'<p class="card-valor">{_e(atual)} <span class="unidade">a.a.</span></p>'
    )
    if mudancas.empty:
        corpo += '<p class="contexto">Sem mudanças no período</p>'
    else:
        ultima = mudancas.iloc[-1]
        corpo += (
            f'<p class="contexto">última mudança em {_e(formatar_data(ultima["data"]))}: '
            f'{_html_variacao(ultima["variacao_pp"], "p.p.")} '
            f'(de {_e(formatar_percentual(ultima["valor_anterior"]))} '
            f'para {_e(formatar_percentual(ultima["valor_novo"]))})</p>'
        )
    return _card("card-selic", "Selic meta", corpo)


def _card_ipca_12m(ipca12: pd.DataFrame) -> str:
    titulo = "IPCA acumulado 12m"
    if ipca12.empty:
        return _card("card-ipca12m", titulo, _vazio())
    ultima = ipca12.iloc[-1]
    corpo = (
        f'<p class="card-valor">{_e(formatar_percentual(ultima["acumulado_12m"]))}</p>'
        f'<p class="contexto">mês de referência: {_e(formatar_mes(ultima["data"]))}</p>'
    )
    variacao: float | None = None
    if len(ipca12) >= 2:
        anterior = ipca12.iloc[-2]
        if _indice_mes(anterior["data"]) + 1 == _indice_mes(ultima["data"]):
            variacao = float(ultima["acumulado_12m"]) - float(anterior["acumulado_12m"])
    corpo += f'<p class="contexto">{_html_variacao(variacao, "p.p.")}</p>'
    return _card("card-ipca12m", titulo, corpo)


def _card_ipca_mensal(ipca: pd.DataFrame) -> str:
    titulo = "IPCA mensal"
    if ipca.empty:
        return _card("card-ipcamensal", titulo, _vazio())
    corpo = (
        f'<p class="card-valor">{_e(formatar_percentual(ipca["valor"].iloc[-1]))}</p>'
        f'<p class="contexto">mês de referência: {_e(formatar_mes(ipca["data"].iloc[-1]))}</p>'
    )
    return _card("card-ipcamensal", titulo, corpo)


def _card_ptax(ptax: pd.DataFrame, ptax_m: pd.DataFrame) -> str:
    titulo = "PTAX venda"
    if ptax.empty:
        return _card("card-ptax", titulo, _vazio())
    valor = float(ptax["valor"].iloc[-1])
    data = pd.Timestamp(ptax["data"].iloc[-1])
    corpo = (
        f'<p class="card-valor">{_e(formatar_moeda(valor))}</p>'
        f'<p class="contexto">{_e(formatar_data(data))}</p>'
    )

    var_dia = (
        _variacao_percentual(valor, float(ptax["valor"].iloc[-2]))
        if len(ptax) >= 2
        else None
    )
    corpo += (
        '<p class="contexto">vs dia anterior com dado: '
        f'{_html_variacao(var_dia, "%")}</p>'
    )

    mes_anterior = pd.Timestamp(year=data.year, month=data.month, day=1) - pd.DateOffset(
        months=1
    )
    linha = ptax_m[ptax_m["mes"] == mes_anterior]
    var_mes = (
        _variacao_percentual(valor, float(linha["fechamento"].iloc[0]))
        if not linha.empty
        else None
    )
    corpo += (
        f'<p class="contexto">vs fechamento de {_e(formatar_mes(mes_anterior))}: '
        f'{_html_variacao(var_mes, "%")}</p>'
    )
    return _card("card-ptax", titulo, corpo)


# --------------------------------------------------------------------------
# Gráficos
# --------------------------------------------------------------------------


def _posicao(data, mensal: bool) -> float:
    """Posição de uma data no eixo X: índice de mês ou dia ordinal."""
    return float(_indice_mes(data)) if mensal else float(pd.Timestamp(data).toordinal())


def _marcas_x(primeira, ultima) -> list[tuple[pd.Timestamp, str]]:
    """Datas e rótulos do eixo X: anos (janela >= 2 anos) ou meses (até 6)."""
    inicio = pd.Timestamp(primeira)
    fim = pd.Timestamp(ultima)
    if fim - inicio >= pd.Timedelta(days=730):
        anos = pd.date_range(inicio.normalize(), fim, freq="YS")
        return [(t, str(t.year)) for t in anos]
    meses = list(pd.date_range(inicio.normalize(), fim, freq="MS"))
    if len(meses) > _MAX_MARCAS_X:
        passo = math.ceil(len(meses) / _MAX_MARCAS_X)
        meses = meses[::passo]
    return [(t, formatar_mes(t)) for t in meses]


def _calcular_largura_barra(escala: float) -> float:
    """Largura da barra mensal: `escala` menos o espaço, entre 1 e o teto."""
    return float(min(max(escala - ESPACO_ENTRE_BARRAS, 1), LARGURA_MAXIMA_BARRA))


def _caminho_barra(x: float, largura: float, y_zero: float, y_fim: float) -> str:
    """Path de uma barra, com cantos arredondados só na ponta do dado."""
    altura = abs(y_fim - y_zero)
    raio = min(_RAIO_BARRAS, largura / 2.0, altura)
    x2 = x + largura
    if raio <= 0:
        return f"M{_c(x)} {_c(y_zero)}V{_c(y_fim)}H{_c(x2)}V{_c(y_zero)}Z"
    r = _c(raio)
    if y_fim < y_zero:  # positiva: ponta em cima
        return (
            f"M{_c(x)} {_c(y_zero)}V{_c(y_fim + raio)}"
            f"A{r} {r} 0 0 1 {_c(x + raio)} {_c(y_fim)}"
            f"H{_c(x2 - raio)}"
            f"A{r} {r} 0 0 1 {_c(x2)} {_c(y_fim + raio)}"
            f"V{_c(y_zero)}Z"
        )
    return (  # negativa: ponta embaixo
        f"M{_c(x)} {_c(y_zero)}V{_c(y_fim - raio)}"
        f"A{r} {r} 0 0 0 {_c(x + raio)} {_c(y_fim)}"
        f"H{_c(x2 - raio)}"
        f"A{r} {r} 0 0 0 {_c(x2)} {_c(y_fim - raio)}"
        f"V{_c(y_zero)}Z"
    )


def _grafico(
    id_base: str,
    titulo: str,
    serie: pd.DataFrame,
    *,
    tipo: str,
    mensal: bool,
    formato_valor,
    formato_data,
) -> str:
    """Bloco `<section>` de um gráfico: título, SVG (ou aviso) e tooltip.

    `tipo`: `"linha"`, `"degrau"` ou `"barras"`. `serie` já está na janela
    de 5 anos; valores não finitos são ignorados.
    """
    serie = serie[serie["valor"].map(_finito)].reset_index(drop=True)
    minimo_pontos = 1 if tipo == "barras" else 2
    cabecalho = f'<h3>{_e(titulo)}</h3>'
    if len(serie) < minimo_pontos:
        return (
            f'<section class="grafico-bloco" id="{id_base}">{cabecalho}'
            f"{_vazio()}</section>"
        )

    datas = list(serie["data"])
    valores = [float(v) for v in serie["valor"]]
    barras = tipo == "barras"

    # Escala Y (barras sempre incluem o zero).
    y_min, y_max = min(valores), max(valores)
    if barras:
        y_min, y_max = min(y_min, 0.0), max(y_max, 0.0)
    marcas = calcular_marcas_eixo(y_min, y_max)
    casas = _casas_do_passo(marcas)
    lo, hi = marcas[0], marcas[-1]

    def y_de(v: float) -> float:
        return _Y1 - (v - lo) / (hi - lo) * (_Y1 - _Y0)

    # Escala X.
    posicoes = [_posicao(d, mensal) for d in datas]
    x_ini = posicoes[0]
    x_fim = posicoes[-1] + (1.0 if barras else 0.0)
    amplitude = max(x_fim - x_ini, 1e-9)
    escala = (_X1 - _X0) / amplitude

    def x_de(p: float) -> float:
        return _X0 + (p - x_ini) * escala

    partes: list[str] = []

    # Grid e rótulos do eixo Y (uma coluna só, à esquerda).
    for marca in marcas:
        y = y_de(marca)
        partes.append(
            f'<line class="grid" x1="{_c(_X0)}" x2="{_c(_X1)}" y1="{_c(y)}" y2="{_c(y)}"/>'
        )
        partes.append(
            f'<text class="rotulo-y" x="{_c(_X0 - 8)}" y="{_c(y + 4)}" '
            f'text-anchor="end">{_e(formatar_numero(marca, casas))}</text>'
        )
    # Linha de base: zero nas barras, borda inferior nas linhas.
    y_base = y_de(0.0) if barras else _Y1
    partes.append(
        f'<line class="base" x1="{_c(_X0)}" x2="{_c(_X1)}" '
        f'y1="{_c(y_base)}" y2="{_c(y_base)}"/>'
    )

    # Eixo X.
    for data_marca, rotulo in _marcas_x(datas[0], datas[-1]):
        x = x_de(_posicao(data_marca, mensal))
        if x < _X0 - 0.5 or x > _X1 + 0.5:
            continue
        partes.append(
            f'<text class="rotulo-x" x="{_c(x)}" y="{_c(_Y1 + 20)}" '
            f'text-anchor="middle">{_e(rotulo)}</text>'
        )

    # Dados.
    pontos_tooltip: list[list] = []
    if barras:
        largura = _calcular_largura_barra(escala)
        y_zero = y_de(0.0)
        for data, pos, valor in zip(datas, posicoes, valores, strict=True):
            x_barra = x_de(pos) + (escala - largura) / 2.0
            y_fim = y_de(valor)
            if valor != 0.0:
                classe = "barra" if valor > 0 else "barra-negativa"
                partes.append(
                    f'<path class="{classe}" '
                    f'd="{_caminho_barra(x_barra, largura, y_zero, y_fim)}"/>'
                )
            pontos_tooltip.append(
                [
                    round(x_barra + largura / 2.0, 1),
                    round(y_fim, 1),
                    f"{formato_data(data)} — {formato_valor(valor)}",
                ]
            )
        x_ultimo = x_de(posicoes[-1]) + escala / 2.0
    else:
        xs = [x_de(p) for p in posicoes]
        ys = [y_de(v) for v in valores]
        if tipo == "degrau":
            caminho = f"M{_c(xs[0])} {_c(ys[0])}"
            ultimo_x = xs[0]
            for i in range(1, len(valores)):
                if valores[i] != valores[i - 1]:
                    caminho += f"H{_c(xs[i])}V{_c(ys[i])}"
                    ultimo_x = xs[i]
            if _c(xs[-1]) != _c(ultimo_x):
                caminho += f"H{_c(xs[-1])}"
        else:
            caminho = "M" + "L".join(f"{_c(x)} {_c(y)}" for x, y in zip(xs, ys, strict=True))
        partes.append(f'<path class="linha" d="{caminho}"/>')
        for data, x, y, valor in zip(datas, xs, ys, valores, strict=True):
            pontos_tooltip.append(
                [round(x, 1), round(y, 1), f"{formato_data(data)} — {formato_valor(valor)}"]
            )
        x_ultimo = xs[-1]

    # Rótulo direto só no último ponto.
    y_ultimo = y_de(valores[-1])
    partes.append(
        f'<circle class="ponto-final" cx="{_c(x_ultimo)}" cy="{_c(y_ultimo)}" r="4"/>'
    )
    partes.append(
        f'<text class="rotulo-final" x="{_c(_X1 + 10)}" y="{_c(y_ultimo + 4)}">'
        f"{_e(formato_valor(valores[-1]))}</text>"
    )

    # Elementos do tooltip (escondidos até o JS acioná-los).
    partes.append(
        f'<line class="crosshair oculto" x1="0" x2="0" y1="{_c(_Y0)}" y2="{_c(_Y1)}"/>'
    )
    partes.append('<circle class="marcador oculto" cx="0" cy="0" r="4"/>')

    descricao = (
        f"{titulo}: de {formato_data(datas[0])} a {formato_data(datas[-1])}; "
        f"último valor {formato_valor(valores[-1])}."
    )
    dados_pontos = html.escape(
        json.dumps(pontos_tooltip, ensure_ascii=False, separators=(",", ":")),
        quote=True,
    )
    svg = (
        f'<svg viewBox="0 0 {_LARGURA} {_ALTURA}" role="img" '
        f'aria-labelledby="{id_base}-t {id_base}-d" data-pontos="{dados_pontos}">'
        f'<title id="{id_base}-t">{_e(titulo)}</title>'
        f'<desc id="{id_base}-d">{_e(descricao)}</desc>'
        + "".join(partes)
        + "</svg>"
    )
    return (
        f'<section class="grafico-bloco" id="{id_base}">{cabecalho}'
        f'<div class="grafico"><div class="rolagem">{svg}</div>'
        '<div class="tooltip" hidden></div></div></section>'
    )


# --------------------------------------------------------------------------
# Tabelas
# --------------------------------------------------------------------------


def _tabela(
    id_tabela: str,
    titulo: str,
    caption: str,
    cabecalhos: list[tuple[str, bool]],
    linhas: list[list[str]],
    vazio: str = _SEM_DADOS,
    nota: str | None = None,
) -> str:
    """Seção com tabela. `cabecalhos`: (texto, numérica). Células já em HTML."""
    abertura = f'<section class="tabela-bloco" id="{id_tabela}"><h4>{_e(titulo)}</h4>'
    if not linhas:
        return f"{abertura}{_vazio(vazio)}</section>"
    classe = {True: ' class="num"', False: ""}
    ths = "".join(
        f'<th scope="col"{classe[num]}>{_e(t)}</th>' for t, num in cabecalhos
    )
    corpo = []
    for linha in linhas:
        tds = "".join(
            f"<td{classe[cabecalhos[i][1]]}>{cel}</td>" for i, cel in enumerate(linha)
        )
        corpo.append(f"<tr>{tds}</tr>")
    rodape = f'<p class="nota">{_e(nota)}</p>' if nota else ""
    return (
        f'{abertura}<div class="rolagem"><table><caption>{_e(caption)}</caption>'
        f"<thead><tr>{ths}</tr></thead><tbody>{''.join(corpo)}</tbody></table></div>"
        f"{rodape}</section>"
    )


def _tabela_selic(mudancas: pd.DataFrame, selic_presente: bool) -> str:
    linhas = [
        [
            _e(formatar_data(r["data"])),
            _e(formatar_percentual(r["valor_anterior"])),
            _e(formatar_percentual(r["valor_novo"])),
            _html_variacao(r["variacao_pp"], "p.p."),
        ]
        for _, r in mudancas.iloc[::-1].iterrows()
    ]
    return _tabela(
        "tabela-selic",
        "Mudanças da Selic meta",
        "Mudanças da meta Selic na janela de 5 anos, mais recentes primeiro",
        [("Data", False), ("De", True), ("Para", True), ("Variação", False)],
        linhas,
        vazio="Sem mudanças no período" if selic_presente else _SEM_DADOS,
    )


def _tabela_ipca(ipca12: pd.DataFrame) -> str:
    linhas = [
        [
            _e(formatar_mes(r["data"])),
            _e(formatar_percentual(r["ipca_mensal"])),
            _e(formatar_percentual(r["acumulado_12m"])),
        ]
        for _, r in ipca12.tail(12).iloc[::-1].iterrows()
    ]
    return _tabela(
        "tabela-ipca",
        "IPCA mensal e acumulado em 12 meses",
        "IPCA mensal e acumulado em 12 meses, últimos 12 meses, mais recente primeiro",
        [("Mês", False), ("IPCA mensal", True), ("Acumulado 12m", True)],
        linhas,
    )


def _tabela_ptax(ptax_m: pd.DataFrame) -> str:
    linhas = [
        [
            _e(formatar_mes(r["mes"])),
            _e(formatar_moeda(r["media"])),
            _e(formatar_moeda(r["fechamento"])),
            _e(formatar_moeda(r["minimo"])),
            _e(formatar_moeda(r["maximo"])),
            _e(int(r["dias_com_dado"])),
        ]
        for _, r in ptax_m.tail(12).iloc[::-1].iterrows()
    ]
    return _tabela(
        "tabela-ptax",
        "PTAX venda mensal",
        "PTAX venda mensal, últimos 12 meses, mais recente primeiro",
        [
            ("Mês", False),
            ("Média", True),
            ("Fechamento", True),
            ("Mínimo", True),
            ("Máximo", True),
            ("Dias", True),
        ],
        linhas,
        nota="O mês mais recente pode estar incompleto (veja a coluna Dias).",
    )


# --------------------------------------------------------------------------
# Documento
# --------------------------------------------------------------------------

_CSS = """
:root {
  color-scheme: light dark;
  --superficie: #fcfcfb;
  --pagina: #f9f9f7;
  --texto: #0b0b0b;
  --texto-2: #52514e;
  --eixo: #52514e;
  --grid: #e1e0d9;
  --base: #c3c2b7;
  --serie: #2a78d6;
  --negativo: #e34948;
}
@media (prefers-color-scheme: dark) {
  :root {
    --superficie: #1a1a19;
    --pagina: #0d0d0d;
    --texto: #ffffff;
    --texto-2: #c3c2b7;
    --eixo: #898781;
    --grid: #2c2c2a;
    --base: #383835;
    --serie: #3987e5;
    --negativo: #e66767;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--pagina);
  color: var(--texto);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  line-height: 1.4;
}
main { max-width: 1100px; margin: 0 auto; padding: 16px; }
h1 { font-size: 1.6rem; margin: 0 0 4px; }
h2 { font-size: 1.2rem; margin: 28px 0 12px; }
h3 { font-size: 1rem; margin: 0 0 8px; }
h4 { font-size: 1rem; margin: 0 0 8px; }
.meta { color: var(--texto-2); margin: 2px 0; font-size: 0.9rem; }
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}
.card, .grafico-bloco, .tabela-bloco {
  background: var(--superficie);
  border: 1px solid var(--grid);
  border-radius: 8px;
  padding: 12px 16px;
}
.card-titulo { margin: 0; color: var(--texto-2); font-size: 0.9rem; }
.card-valor { margin: 4px 0; font-size: 2rem; font-weight: 600; color: var(--texto); }
.unidade { font-size: 0.9rem; font-weight: 400; color: var(--texto-2); }
.contexto { margin: 2px 0; font-size: 0.85rem; color: var(--texto-2); }
.variacao { color: var(--texto); font-weight: 600; }
.sem-variacao { color: var(--texto-2); }
.vazio { color: var(--texto-2); margin: 4px 0; }
.graficos { display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; }
.graficos > * { min-width: 0; }
.cards > * { min-width: 0; overflow-wrap: anywhere; }
h1, .meta { overflow-wrap: anywhere; }
table { min-width: max-content; }
.tabela-bloco { margin-bottom: 12px; }
.grafico { position: relative; }
.rolagem { overflow-x: auto; }
svg { width: 100%; min-width: 520px; height: auto; display: block; }
@media (min-width: 560px) {
  svg { touch-action: pan-y; }
}
svg text { font-size: 12px; font-family: inherit; }
.grid { stroke: var(--grid); stroke-width: 1; }
.base { stroke: var(--base); stroke-width: 1; }
.rotulo-y, .rotulo-x { fill: var(--eixo); }
.rotulo-final { fill: var(--texto); font-weight: 600; }
.linha { fill: none; stroke: var(--serie); stroke-width: 2; stroke-linejoin: round; }
.barra { fill: var(--serie); }
.barra-negativa { fill: var(--negativo); }
.ponto-final { fill: var(--superficie); stroke: var(--serie); stroke-width: 2; }
.crosshair { stroke: var(--base); stroke-width: 1; }
.marcador { fill: var(--superficie); stroke: var(--texto); stroke-width: 2; }
.oculto { display: none; }
.tooltip {
  position: absolute;
  top: 0;
  left: 0;
  pointer-events: none;
  background: var(--superficie);
  color: var(--texto);
  border: 1px solid var(--base);
  border-radius: 4px;
  padding: 4px 8px;
  font-size: 0.85rem;
  white-space: nowrap;
}
.tooltip[hidden] { display: none; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
caption { text-align: left; color: var(--texto-2); padding-bottom: 6px; }
th { text-align: left; border-bottom: 1px solid var(--base); padding: 6px 8px; }
td { padding: 6px 8px; border-bottom: 1px solid var(--grid); }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.nota { color: var(--texto-2); font-size: 0.8rem; margin: 6px 0 0; }
footer { margin-top: 28px; color: var(--texto-2); font-size: 0.85rem; }
footer p { margin: 2px 0; }
"""

_SCRIPT = """
(function () {
  var graficos = document.querySelectorAll('svg[data-pontos]');
  Array.prototype.forEach.call(graficos, function (svg) {
    var pontos = JSON.parse(svg.getAttribute('data-pontos') || '[]');
    var caixa = svg.closest('.grafico');
    var dica = caixa.querySelector('.tooltip');
    var cruz = svg.querySelector('.crosshair');
    var marcador = svg.querySelector('.marcador');
    if (!pontos.length || !dica || !cruz || !marcador) { return; }
    function esconder() {
      dica.hidden = true;
      cruz.classList.add('oculto');
      marcador.classList.add('oculto');
    }
    function mostrar(evento) {
      var ret = svg.getBoundingClientRect();
      var escala = 720 / ret.width;
      var x = (evento.clientX - ret.left) * escala;
      var melhor = pontos[0];
      var menor = Math.abs(melhor[0] - x);
      for (var i = 1; i < pontos.length; i++) {
        var d = Math.abs(pontos[i][0] - x);
        if (d < menor) { menor = d; melhor = pontos[i]; }
      }
      cruz.setAttribute('x1', melhor[0]);
      cruz.setAttribute('x2', melhor[0]);
      marcador.setAttribute('cx', melhor[0]);
      marcador.setAttribute('cy', melhor[1]);
      cruz.classList.remove('oculto');
      marcador.classList.remove('oculto');
      dica.textContent = melhor[2];
      dica.hidden = false;
      var ref = caixa.getBoundingClientRect();
      var esq = ret.left - ref.left + melhor[0] / escala + 12;
      var topo = ret.top - ref.top + melhor[1] / escala - 36;
      if (esq + dica.offsetWidth > caixa.clientWidth) {
        esq = esq - dica.offsetWidth - 24;
      }
      dica.style.left = Math.max(0, esq) + 'px';
      dica.style.top = Math.max(0, topo) + 'px';
    }
    svg.addEventListener('pointermove', mostrar);
    svg.addEventListener('pointerdown', mostrar);
    svg.addEventListener('pointerleave', esconder);
    svg.addEventListener('pointercancel', esconder);
  });
})();
"""


def _nome_da_serie(dados: pd.DataFrame, codigo: int) -> str:
    """Nome da série: coluna `serie` dos dados (se houver) ou o padrão."""
    if "serie" in dados.columns:
        linhas = dados.loc[dados["codigo"] == codigo, ["data", "serie"]].dropna()
        linhas = linhas[linhas["serie"].astype(str).str.strip() != ""]
        if not linhas.empty:
            recentes = linhas[linhas["data"] == linhas["data"].max()]
            return min(str(nome) for nome in recentes["serie"])
    return _NOMES_PADRAO[codigo]


def gerar_html(dados: pd.DataFrame, gerado_em: datetime) -> str:
    """Gera o relatório HTML completo a partir de `dados`.

    `dados` precisa das colunas `codigo`, `data` e `valor` (mesmo contrato
    de `analise.py`; `ErroAnalise` propaga se faltar alguma). `gerado_em` é
    um `datetime` naive em horário de Brasília. Função pura: sem I/O, sem
    relógio, não altera `dados`.
    """
    mudancas_todas = mudancas_selic(dados)  # também valida as colunas
    ipca12 = ipca_acumulado_12m(dados)
    ptax_m = ptax_mensal(dados)

    selic = _serie(dados, CODIGO_SELIC_META)
    ipca = _serie(dados, CODIGO_IPCA_MENSAL)
    ptax = _serie(dados, CODIGO_PTAX_VENDA)

    selic_j = _janela(selic)
    ipca_j = _janela(ipca)
    ptax_j = _janela(ptax)
    ipca12_j = _janela(ipca12) if not ipca12.empty else ipca12

    if selic.empty or mudancas_todas.empty:
        mudancas = mudancas_todas.iloc[0:0]
    else:
        corte = _corte_janela(selic["data"].max())
        mudancas = mudancas_todas[mudancas_todas["data"] >= corte].reset_index(drop=True)

    # Período: menor e maior data entre as linhas exibidas das séries presentes.
    exibidas = [s["data"] for s in (selic_j, ipca_j, ptax_j) if not s.empty]
    if exibidas:
        todas = pd.concat(exibidas)
        periodo = f"de {formatar_data(todas.min())} a {formatar_data(todas.max())}"
    else:
        periodo = "sem dados"

    cards = "".join(
        [
            _card_selic(selic, mudancas),
            _card_ipca_12m(ipca12),
            _card_ipca_mensal(ipca),
            _card_ptax(ptax, ptax_m),
        ]
    )

    graficos = "".join(
        [
            _grafico(
                "grafico-selic",
                "Selic meta (% a.a.)",
                selic_j,
                tipo="degrau",
                mensal=False,
                formato_valor=formatar_percentual,
                formato_data=formatar_data,
            ),
            _grafico(
                "grafico-ipca12m",
                "IPCA acumulado em 12 meses (%)",
                ipca12_j.rename(columns={"acumulado_12m": "valor"})[["data", "valor"]]
                if not ipca12_j.empty
                else ipca12_j.reindex(columns=["data", "valor"]),
                tipo="linha",
                mensal=True,
                formato_valor=formatar_percentual,
                formato_data=formatar_mes,
            ),
            _grafico(
                "grafico-ipcamensal",
                "IPCA mensal (%)",
                ipca_j,
                tipo="barras",
                mensal=True,
                formato_valor=formatar_percentual,
                formato_data=formatar_mes,
            ),
            _grafico(
                "grafico-ptax",
                "Dólar PTAX venda (R$)",
                ptax_j,
                tipo="linha",
                mensal=False,
                formato_valor=formatar_moeda,
                formato_data=formatar_data,
            ),
        ]
    )

    tabelas = "".join(
        [
            _tabela_selic(mudancas, not selic.empty),
            _tabela_ipca(ipca12),
            _tabela_ptax(ptax_m),
        ]
    )

    series_rodape = " · ".join(
        f"{codigo} {_e(_nome_da_serie(dados, codigo))}"
        for codigo in (CODIGO_SELIC_META, CODIGO_IPCA_MENSAL, CODIGO_PTAX_VENDA)
    )
    gerado_txt = f"{formatar_data(gerado_em)} {gerado_em:%H:%M}"

    return (
        "<!DOCTYPE html>\n"
        '<html lang="pt-BR">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_e(_TITULO)}</title>\n<style>{_CSS}</style>\n</head>\n<body>\n<main>\n"
        f'<header><h1>{_e(_TITULO)}</h1>\n'
        f'<p class="meta">Gerado em {_e(gerado_txt)} (horário de Brasília) · Fonte: SGS/BCB</p>\n'
        f'<p class="meta">Período: {_e(periodo)}</p></header>\n'
        f'<section id="resumo"><h2>Resumo executivo</h2><div class="cards">{cards}</div></section>\n'
        f'<section id="graficos"><h2>Gráficos históricos</h2><div class="graficos">{graficos}</div></section>\n'
        f'<section id="tabelas"><h2>Tabelas de análise</h2>{tabelas}</section>\n'
        "<footer>\n"
        "<p>Fonte: SGS — Sistema Gerenciador de Séries Temporais, Banco Central do Brasil.</p>\n"
        f"<p>Séries: {series_rodape}</p>\n"
        "<p>Os dados podem ser revisados pelo Banco Central do Brasil após a publicação.</p>\n"
        "</footer>\n</main>\n"
        f"<script>{_SCRIPT}</script>\n</body>\n</html>\n"
    )


def gravar_relatorio(html: str, caminho: Path = RELATORIO_PADRAO) -> None:
    """Grava `html` em `caminho` de forma atômica (UTF-8, `\\n`).

    Escreve num temporário `<nome>.tmp` no mesmo diretório e troca com
    `os.replace`, depois de `flush` + `fsync`. Em qualquer interrupção
    (inclusive `KeyboardInterrupt`), remove o temporário (melhor esforço),
    deixa o arquivo anterior intacto e propaga a exceção original.
    """
    caminho = Path(caminho)
    temporario = caminho.with_name(caminho.name + ".tmp")
    try:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        with temporario.open("w", encoding="utf-8", newline="\n") as arquivo:
            arquivo.write(html)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(temporario, caminho)
    except BaseException:
        try:
            temporario.unlink(missing_ok=True)
        except OSError:
            pass
        raise
