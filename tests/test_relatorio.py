"""Testes do módulo `indicadores.relatorio` (spec: specs/relatorio.md).

Tudo é feito com `DataFrame` sintético e data de geração fixa: nenhum teste
acessa banco, rede ou o relógio. Só `gravar_relatorio` toca o disco, e
sempre dentro de `tmp_path`.

O que se testa é o HTML/SVG/`data-pontos` que o Python gera. O JavaScript
do tooltip não é executado (não há navegador na suíte).
"""

from __future__ import annotations

import ast
import html as html_lib
import itertools
import json
import locale
import math
import re
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from indicadores import relatorio
from indicadores.analise import (
    ErroAnalise,
    ipca_acumulado_12m,
    ptax_mensal,
)
from indicadores.relatorio import (
    calcular_marcas_eixo,
    formatar_data,
    formatar_mes,
    formatar_moeda,
    formatar_numero,
    formatar_percentual,
    formatar_variacao,
    gerar_html,
    gravar_relatorio,
)

GERADO_EM = datetime(2026, 10, 9, 14, 30)  # noqa: DTZ001 (naive de propósito)
ARQUIVO_RELATORIO = Path(relatorio.__file__)

SEM_DADOS = "Sem dados suficientes"
INDISPONIVEL = "variação indisponível"


# ---------------------------------------------------------------------------
# Dados sintéticos
# ---------------------------------------------------------------------------


def _serie_df(codigo: int, nome: str, datas, valores) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "codigo": pd.Series([codigo] * len(datas), dtype="int64"),
            "serie": nome,
            "data": pd.to_datetime(list(datas)),
            "valor": pd.Series([float(v) for v in valores], dtype="float64"),
        }
    )


def _juntar(*partes: pd.DataFrame) -> pd.DataFrame:
    return pd.concat(partes, ignore_index=True)


def _selic_degraus(inicio: str, fim: str, degraus: list[tuple[str, float]]) -> pd.DataFrame:
    """Selic diária; `degraus` = [(data_inicio_do_valor, valor), ...] em ordem."""
    datas = pd.date_range(inicio, fim, freq="D")
    valores = []
    for d in datas:
        vigente = degraus[0][1]
        for inicio_degrau, valor in degraus:
            if d >= pd.Timestamp(inicio_degrau):
                vigente = valor
        valores.append(vigente)
    return _serie_df(432, "selic_meta", datas, valores)


def _ipca_df(inicio: str, valores: list[float]) -> pd.DataFrame:
    datas = pd.date_range(inicio, periods=len(valores), freq="MS")
    return _serie_df(433, "ipca_mensal", datas, valores)


IPCA_VALORES: list[float] = [round(0.10 + ((i * 7) % 10) / 20, 2) for i in range(24)] + [-0.11]


def dados_referencia() -> pd.DataFrame:
    """Dataset de referência da spec (Selic, IPCA e PTAX)."""
    selic = _selic_degraus(
        "2025-09-01",
        "2026-08-31",
        [("2025-09-01", 14.75), ("2025-12-11", 15.00), ("2026-03-19", 14.75)],
    )
    ipca = _ipca_df("2024-08-01", IPCA_VALORES)
    dias = pd.bdate_range("2025-08-01", "2026-08-31")
    valores = [round(5.0 + ((i * 13) % 40) / 100, 4) for i in range(len(dias))]
    ptax = _serie_df(1, "dolar_ptax_venda", dias, valores)
    ptax.loc[ptax["data"] == "2026-07-31", "valor"] = 5.2000
    ptax.loc[ptax["data"] == "2026-08-28", "valor"] = 5.4000
    ptax.loc[ptax["data"] == "2026-08-31", "valor"] = 5.3784
    return _juntar(selic, ipca, ptax)


def _dados_vazios() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "codigo": pd.Series(dtype="int64"),
            "serie": pd.Series(dtype=object),
            "data": pd.Series(dtype="datetime64[ns]"),
            "valor": pd.Series(dtype="float64"),
        }
    )


@pytest.fixture(scope="module")
def dados_ref() -> pd.DataFrame:
    return dados_referencia()


@pytest.fixture(scope="module")
def html_ref(dados_ref: pd.DataFrame) -> str:
    return gerar_html(dados_ref, GERADO_EM)


# ---------------------------------------------------------------------------
# Helpers de leitura do HTML gerado
# ---------------------------------------------------------------------------


def _bloco(html: str, tag: str, id_: str) -> str:
    m = re.search(rf'<{tag} [^>]*id="{id_}">(.*?)</{tag}>', html, re.DOTALL)
    assert m, f"bloco <{tag} id={id_}> não encontrado"
    return m.group(1)


def _texto(fragmento: str) -> str:
    sem_codigo = re.sub(r"<(script|style)\b.*?</\1>", " ", fragmento, flags=re.DOTALL)
    sem_tags = re.sub(r"<[^>]+>", " ", sem_codigo)
    return re.sub(r"\s+", " ", html_lib.unescape(sem_tags)).strip()


def _attrs(trecho: str) -> dict[str, str]:
    return dict(re.findall(r'([\w:-]+)="([^"]*)"', trecho))


def _svg(html: str, id_grafico: str) -> str:
    bloco = _bloco(html, "section", id_grafico)
    m = re.search(r"<svg\b.*?</svg>", bloco, re.DOTALL)
    assert m, f"{id_grafico} não tem <svg>"
    return m.group(0)


def _elementos(svg: str, tag: str) -> list[tuple[dict[str, str], str]]:
    """Elementos `<tag ...>texto</tag>` ou `<tag .../>` com atributos e texto."""
    saida = []
    for m in re.finditer(rf"<{tag}\b([^>]*?)(?:/>|>(.*?)</{tag}>)", svg, re.DOTALL):
        saida.append((_attrs(m.group(1)), html_lib.unescape(m.group(2) or "")))
    return saida


def _com_classe(svg: str, tag: str, classe: str):
    return [(a, t) for a, t in _elementos(svg, tag) if a.get("class") == classe]


def _pontos(svg: str) -> list[list]:
    m = re.search(r'data-pontos="([^"]*)"', svg)
    assert m
    return json.loads(html_lib.unescape(m.group(1)))


def _tabela(html: str, id_tabela: str) -> tuple[list[str], list[list[str]]]:
    bloco = _bloco(html, "section", id_tabela)
    cabecalhos = [_texto(c) for c in re.findall(r"<th\b[^>]*>(.*?)</th>", bloco, re.DOTALL)]
    corpo = re.search(r"<tbody>(.*?)</tbody>", bloco, re.DOTALL)
    assert corpo, f"{id_tabela} sem <tbody>"
    linhas = []
    for tr in re.findall(r"<tr>(.*?)</tr>", corpo.group(1), re.DOTALL):
        linhas.append([_texto(c) for c in re.findall(r"<td\b[^>]*>(.*?)</td>", tr, re.DOTALL)])
    return cabecalhos, linhas


def _sem_script(html: str) -> str:
    return re.sub(r"<script>.*?</script>", "", html, flags=re.DOTALL)


def _css(html: str) -> str:
    m = re.search(r"<style>(.*?)</style>", html, re.DOTALL)
    assert m
    return m.group(1)


def _declaracoes(bloco: str) -> dict[str, str]:
    return {k: v.strip() for k, v in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", bloco)}


_IDS_CARDS = ["card-selic", "card-ipca12m", "card-ipcamensal", "card-ptax"]
_IDS_GRAFICOS = ["grafico-selic", "grafico-ipca12m", "grafico-ipcamensal", "grafico-ptax"]
_IDS_TABELAS = ["tabela-selic", "tabela-ipca", "tabela-ptax"]


def _acumulado_12m(valores: list[float]) -> float:
    return (math.prod(1 + v / 100 for v in valores) - 1) * 100


# ===========================================================================
# Formatação e eixo (1-5)
# ===========================================================================


def test_formatar_numero_pt_br():
    assert formatar_numero(1234.5, 2) == "1.234,50"
    assert formatar_numero(1234567.891, 1) == "1.234.567,9"
    assert formatar_numero(5.3784, 4) == "5,3784"
    assert formatar_numero(7, 0) == "7"
    assert formatar_numero(0.0, 2) == "0,00"
    # Negativo usa U+2212, não hífen.
    assert formatar_numero(-1234.5, 2) == "\u22121.234,50"
    assert "-" not in formatar_numero(-0.11, 2)
    # Resultado que arredonda para zero não tem sinal.
    assert formatar_numero(-0.001, 2) == "0,00"
    assert formatar_numero(-0.00001, 4) == "0,0000"
    # Não finito.
    assert formatar_numero(float("nan"), 2) == "—"
    assert formatar_numero(float("inf"), 2) == "—"
    assert formatar_numero(float("-inf"), 2) == "—"


def test_formatacao_nao_depende_de_locale():
    # 1) o módulo não importa `locale`;
    arvore = ast.parse(ARQUIVO_RELATORIO.read_text(encoding="utf-8"))
    importados = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            importados.update(a.name.split(".")[0] for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module:
            importados.add(no.module.split(".")[0])
    assert "locale" not in importados

    # 2) se houver um locale com formato diferente instalado, o resultado
    #    não muda.
    original = locale.setlocale(locale.LC_ALL)
    try:
        for candidato in ("de_DE.UTF-8", "German_Germany.1252", "en_US.UTF-8", "English_United States.1252"):
            try:
                locale.setlocale(locale.LC_ALL, candidato)
            except locale.Error:
                continue
            assert formatar_numero(1234.5, 2) == "1.234,50"
            assert formatar_moeda(5.3784) == "R$ 5,3784"
    finally:
        locale.setlocale(locale.LC_ALL, original)


def test_formatar_data_e_mes():
    assert formatar_data(date(2026, 8, 31)) == "31/08/2026"
    assert formatar_data(pd.Timestamp("2026-03-05")) == "05/03/2026"
    assert formatar_data("2026-01-09") == "09/01/2026"
    abreviacoes = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
    for mes, abreviacao in enumerate(abreviacoes, start=1):
        assert formatar_mes(pd.Timestamp(2026, mes, 1)) == f"{abreviacao}/2026"
    assert formatar_mes(date(2026, 8, 31)) == "ago/2026"


def test_formatar_percentual_e_moeda():
    assert formatar_percentual(14.25) == "14,25%"
    assert formatar_percentual(14.2, 1) == "14,2%"
    assert formatar_percentual(-0.11) == "\u22120,11%"
    assert formatar_moeda(5.3784) == "R$ 5,3784"
    assert formatar_moeda(1234.5, 2) == "R$ 1.234,50"
    assert formatar_moeda(5.3) == "R$ 5,3000"


def test_formatar_variacao_seta_sinal_texto():
    assert formatar_variacao(0.25, "p.p.") == "▲ alta de +0,25 p.p."
    assert formatar_variacao(-0.25, "p.p.") == "▼ queda de \u22120,25 p.p."
    assert formatar_variacao(0.0, "p.p.") == "▶ estável (0,00 p.p.)"
    assert formatar_variacao(3.43, "%") == "▲ alta de +3,43%"
    assert formatar_variacao(-0.40, "%") == "▼ queda de \u22120,40%"
    assert formatar_variacao(0.0, "%") == "▶ estável (0,00%)"
    # Classificação pelo valor já arredondado para as casas exibidas.
    for quase_zero in (0.004, -0.004):
        texto = formatar_variacao(quase_zero, "p.p.")
        assert texto == "▶ estável (0,00 p.p.)"
        assert "▲" not in texto and "▼" not in texto
    assert "alta de +0,00" not in formatar_variacao(0.004, "p.p.")
    # O sinal negativo é U+2212 (nunca o hífen).
    assert "-" not in formatar_variacao(-0.25, "p.p.")


@pytest.mark.parametrize(
    ("minimo", "maximo"),
    [(4.8, 6.2), (10.5, 15), (-0.5, 1.2), (0, 1), (15, 15)],
)
def test_calcular_marcas_eixo(minimo: float, maximo: float):
    marcas = calcular_marcas_eixo(minimo, maximo)

    assert 4 <= len(marcas) <= 6
    assert marcas == sorted(marcas)
    assert len(set(marcas)) == len(marcas)
    assert marcas[0] <= minimo
    assert marcas[-1] >= maximo

    passo = marcas[1] - marcas[0]
    assert passo > 0
    # Passo no formato {1, 2, 2.5, 5} x 10^k.
    mantissa = passo / 10 ** math.floor(math.log10(passo))
    assert any(math.isclose(mantissa, c, rel_tol=1e-6) for c in (1, 2, 2.5, 5))
    for anterior, atual in itertools.pairwise(marcas):
        assert math.isclose(atual - anterior, passo, rel_tol=1e-6)
    # Todas múltiplas do passo e sem ruído de ponto flutuante.
    for marca in marcas:
        razao = marca / passo
        assert abs(razao - round(razao)) < 1e-6
        assert marca == round(marca, 6), f"ruído de ponto flutuante em {marca!r}"


# ===========================================================================
# Estrutura (6-9)
# ===========================================================================


def test_estrutura_basica_do_documento(html_ref: str):
    assert html_ref.startswith("<!DOCTYPE html>")
    assert '<html lang="pt-BR">' in html_ref
    assert '<meta charset="utf-8">' in html_ref
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in html_ref
    assert re.search(r"<title>[^<]+</title>", html_ref)
    h1 = re.findall(r"<h1>(.*?)</h1>", html_ref, re.DOTALL)
    assert h1 == ["Indicadores Econômicos — Banco Central do Brasil"]


def test_cabecalho_data_geracao_fonte_e_periodo(html_ref: str):
    texto = _texto(_sem_script(html_ref))
    assert "Gerado em 09/10/2026 14:30 (horário de Brasília)" in texto
    assert "SGS/BCB" in texto
    # Regra da spec: menor e maior data entre as linhas EXIBIDAS das três
    # séries. No dataset de referência o IPCA começa em 2024-08-01 (dentro
    # da janela de 5 anos) e a Selic/PTAX terminam em 2026-08-31.
    # (O exemplo "de 01/09/2025 a 31/08/2026" da spec é incompatível com o
    # dataset de referência; vale a regra textual.)
    assert "de 01/08/2024 a 31/08/2026" in texto


def test_rodape_fonte_codigos_e_aviso_de_revisao(html_ref: str):
    rodape = _texto(re.search(r"<footer>(.*?)</footer>", html_ref, re.DOTALL).group(1))
    assert (
        "Fonte: SGS — Sistema Gerenciador de Séries Temporais, Banco Central do Brasil."
        in rodape
    )
    assert "432 selic_meta" in rodape
    assert "433 ipca_mensal" in rodape
    assert "1 dolar_ptax_venda" in rodape
    assert "Os dados podem ser revisados pelo Banco Central do Brasil após a publicação." in rodape


def test_quatro_cards_tres_tabelas_e_sem_secao_de_status(html_ref: str):
    ordem = re.findall(r'<article class="card" id="([^"]+)"', html_ref)
    assert ordem == _IDS_CARDS
    assert html_ref.count("<table") == 3
    assert re.findall(r'<section class="grafico-bloco" id="([^"]+)"', html_ref) == _IDS_GRAFICOS
    assert re.findall(r'<section class="tabela-bloco" id="([^"]+)"', html_ref) == _IDS_TABELAS
    minusculo = _texto(_sem_script(html_ref)).lower()
    for proibido in ("inseridos", "atualizados", "descartes", "série ignorada", "status"):
        assert proibido not in minusculo


# ===========================================================================
# Cards (10-16)
# ===========================================================================


def test_card_selic_queda_com_data_e_tamanho_da_mudanca(html_ref: str):
    card = _texto(_bloco(html_ref, "article", "card-selic"))
    assert "14,75%" in card
    assert "a.a." in card
    assert "19/03/2026" in card
    assert "▼ queda de \u22120,25 p.p." in card
    assert "de 15,00% para 14,75%" in card


@pytest.mark.parametrize(
    ("dados", "esperado", "ausente"),
    [
        pytest.param(
            _selic_degraus("2025-01-01", "2025-03-31", [("2025-01-01", 14.50), ("2025-02-10", 14.75)]),
            ["14,75%", "10/02/2025", "▲ alta de +0,25 p.p.", "de 14,50% para 14,75%"],
            ["Sem mudanças no período"],
            id="ultima_mudanca_e_alta",
        ),
        pytest.param(
            _selic_degraus("2025-01-01", "2025-03-31", [("2025-01-01", 10.50)]),
            ["10,50%", "Sem mudanças no período"],
            ["última mudança", "▲", "▼"],
            id="serie_constante",
        ),
    ],
)
def test_card_selic_alta_e_sem_mudancas(dados, esperado, ausente):
    card = _texto(_bloco(gerar_html(dados, GERADO_EM), "article", "card-selic"))
    for trecho in esperado:
        assert trecho in card
    for trecho in ausente:
        assert trecho not in card


def test_card_ipca_12m_via_produto_e_variacao_em_pp(html_ref: str):
    ultimo = _acumulado_12m(IPCA_VALORES[-12:])
    anterior = _acumulado_12m(IPCA_VALORES[-13:-1])
    card = _texto(_bloco(html_ref, "article", "card-ipca12m"))

    assert formatar_percentual(ultimo) in card
    assert "ago/2026" in card
    assert formatar_variacao(ultimo - anterior, "p.p.") in card
    assert INDISPONIVEL not in card


def test_card_ipca_mensal_negativo(html_ref: str):
    card = _texto(_bloco(html_ref, "article", "card-ipcamensal"))
    assert "\u22120,11%" in card
    assert "ago/2026" in card


def test_card_ptax_valor_data_e_duas_variacoes(html_ref: str):
    card = _texto(_bloco(html_ref, "article", "card-ptax"))
    assert "R$ 5,3784" in card
    assert "31/08/2026" in card
    # Negativo vs dia anterior (28/08 = 5,4000) e positivo vs fechamento de
    # julho (31/07 = 5,2000), no mesmo card.
    assert re.search(r"dia anterior.*?▼ queda de \u22120,40%", card)
    assert re.search(r"fechamento.*?▲ alta de \+3,43%", card)
    assert INDISPONIVEL not in card


def test_toda_variacao_tem_seta_sinal_e_texto(html_ref: str):
    variacoes = re.findall(r'<span class="variacao">(.*?)</span>', html_ref, re.DOTALL)
    # 1 (Selic card) + 1 (IPCA 12m) + 2 (PTAX) + 2 (tabela Selic).
    assert len(variacoes) >= 6
    for v in variacoes:
        texto = html_lib.unescape(v)
        assert re.search(r"[▲▼▶]", texto), texto
        assert re.search(r"alta|queda|estável", texto), texto
        if "▲" in texto:
            assert "+" in texto
        if "▼" in texto:
            assert "\u2212" in texto
    assert {"▲", "▼"} <= set("".join(variacoes))

    # Nenhuma regra de cor para a variação diferente de --texto.
    regras = re.findall(r"\.variacao\s*\{([^}]*)\}", _css(html_ref))
    assert regras
    for corpo in regras:
        for cor in re.findall(r"(?<![\w-])color\s*:\s*([^;]+);", corpo):
            assert cor.strip() == "var(--texto)"


def test_reutiliza_funcoes_de_analise(monkeypatch: pytest.MonkeyPatch, dados_ref: pd.DataFrame):
    falsas_mudancas = pd.DataFrame(
        {
            "data": pd.to_datetime(["2026-05-05"]),
            "valor_anterior": [10.00],
            "valor_novo": [10.50],
            "variacao_pp": [0.50],
        }
    )
    falso_ipca = pd.DataFrame(
        {
            "data": pd.to_datetime(["2026-06-01", "2026-07-01"]),
            "ipca_mensal": [0.50, 0.60],
            "acumulado_12m": [7.77, 8.88],
        }
    )
    falso_ptax = pd.DataFrame(
        {
            "mes": pd.to_datetime(["2026-07-01"]),
            "media": [5.1111],
            "fechamento": [5.0000],
            "minimo": [4.9999],
            "maximo": [5.2222],
            "dias_com_dado": pd.Series([17], dtype="int64"),
        }
    )
    monkeypatch.setattr(relatorio, "mudancas_selic", lambda dados: falsas_mudancas)
    monkeypatch.setattr(relatorio, "ipca_acumulado_12m", lambda dados: falso_ipca)
    monkeypatch.setattr(relatorio, "ptax_mensal", lambda dados: falso_ptax)

    html = gerar_html(dados_ref, GERADO_EM)

    selic = _texto(_bloco(html, "article", "card-selic"))
    assert "05/05/2026" in selic and "▲ alta de +0,50 p.p." in selic
    assert "de 10,00% para 10,50%" in selic

    ipca = _texto(_bloco(html, "article", "card-ipca12m"))
    assert "8,88%" in ipca and "jul/2026" in ipca
    assert "▲ alta de +1,11 p.p." in ipca

    ptax = _texto(_bloco(html, "article", "card-ptax"))
    # 5,3784 / 5,0000 - 1 = +7,57%
    assert "▲ alta de +7,57%" in ptax

    _, linhas_selic = _tabela(html, "tabela-selic")
    assert linhas_selic == [["05/05/2026", "10,00%", "10,50%", "▲ alta de +0,50 p.p."]]
    _, linhas_ipca = _tabela(html, "tabela-ipca")
    assert [l[0] for l in linhas_ipca] == ["jul/2026", "jun/2026"]
    _, linhas_ptax = _tabela(html, "tabela-ptax")
    assert linhas_ptax == [["jul/2026", "R$ 5,1111", "R$ 5,0000", "R$ 4,9999", "R$ 5,2222", "17"]]


# ===========================================================================
# Gráficos (17-24)
# ===========================================================================


def test_quatro_graficos_svg_acessiveis_sem_legenda(html_ref: str):
    svgs = re.findall(r"<svg\b.*?</svg>", html_ref, re.DOTALL)
    assert len(svgs) == 4
    for svg in svgs:
        assert 'role="img"' in svg
        assert 'viewBox="0 0 720 320"' in svg
        assert "xmlns" not in svg
        assert re.search(r"<title\b[^>]*>[^<]+</title>", svg)
        assert re.search(r"<desc\b[^>]*>[^<]+</desc>", svg)
        ids = re.search(r'aria-labelledby="([^"]+)"', svg).group(1).split()
        for id_ in ids:
            assert f'id="{id_}"' in svg

    titulos = [
        _texto(re.search(r"<h3>(.*?)</h3>", _bloco(html_ref, "section", id_), re.DOTALL).group(1))
        for id_ in _IDS_GRAFICOS
    ]
    assert titulos == [
        "Selic meta (% a.a.)",
        "IPCA acumulado em 12 meses (%)",
        "IPCA mensal (%)",
        "Dólar PTAX venda (R$)",
    ]
    minusculo = html_ref.lower()
    assert "legend" not in minusculo
    assert "legenda" not in minusculo


@pytest.mark.parametrize("id_grafico", _IDS_GRAFICOS)
def test_cada_grafico_tem_um_unico_eixo_y_com_4_a_6_marcas(html_ref: str, id_grafico: str):
    svg = _svg(html_ref, id_grafico)
    rotulos_y = _com_classe(svg, "text", "rotulo-y")
    grades = _com_classe(svg, "line", "grid")

    assert 4 <= len(rotulos_y) <= 6
    assert len(grades) == len(rotulos_y)
    # Uma só coluna de rótulos Y, alinhada à direita, à esquerda do gráfico.
    xs = {a["x"] for a, _ in rotulos_y}
    assert len(xs) == 1
    assert {a["text-anchor"] for a, _ in rotulos_y} == {"end"}
    x_grade = float(grades[0][0]["x1"])
    assert float(xs.pop()) < x_grade
    # Sem eixo duplo: nenhum rótulo de eixo à direita; os únicos textos
    # são rótulos Y, rótulos X e o rótulo final.
    classes = {a.get("class") for a, _ in _elementos(svg, "text")}
    assert classes <= {"rotulo-y", "rotulo-x", "rotulo-final"}
    assert len(_com_classe(svg, "text", "rotulo-final")) == 1


def test_selic_em_degrau_so_h_e_v(html_ref: str):
    svg = _svg(html_ref, "grafico-selic")
    caminhos = _com_classe(svg, "path", "linha")
    assert len(caminhos) == 1
    d = caminhos[0][0]["d"]

    comandos = re.findall(r"[A-Za-z]", d)
    assert set(comandos) <= {"M", "H", "V"}
    n_mudancas = 2  # 11/12/2025 e 19/03/2026
    # Um M, um H+V por mudança e um H final até o último ponto: vértices
    # nas mudanças + extremos, não um por dia (365 linhas).
    assert comandos.count("M") == 1
    assert comandos.count("V") == n_mudancas
    assert n_mudancas <= comandos.count("H") <= n_mudancas + 1
    assert len(comandos) <= 2 * n_mudancas + 2
    assert len(comandos) < 20


def _geometria_barra(d: str) -> dict:
    tokens = re.findall(r"[A-Za-z]|-?\d+(?:\.\d+)?", d)
    xs: list[float] = []
    ys: list[float] = []
    arcos: list[list[str]] = []
    cmd = ""
    y_zero = None
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.isalpha():
            cmd = t
            i += 1
            continue
        if cmd == "M":
            xs.append(float(t))
            ys.append(float(tokens[i + 1]))
            y_zero = float(tokens[i + 1])
            i += 2
        elif cmd == "V":
            ys.append(float(t))
            i += 1
        elif cmd == "H":
            xs.append(float(t))
            i += 1
        elif cmd == "A":
            args = tokens[i : i + 7]
            arcos.append(args)
            xs.append(float(args[5]))
            ys.append(float(args[6]))
            i += 7
        else:  # pragma: no cover - comando inesperado
            raise AssertionError(f"comando inesperado em {d!r}")
    return {
        "x_ini": min(xs),
        "x_fim": max(xs),
        "y_zero": y_zero,
        "y_min": min(ys),
        "y_max": max(ys),
        "arcos": arcos,
        "ultimo_y": ys[-1],
    }


def test_ipca_mensal_barras_com_sinal_espaco_e_cantos(html_ref: str):
    svg = _svg(html_ref, "grafico-ipcamensal")
    elementos = _elementos(svg, "path")
    barras = [(a, t) for a, t in elementos if a.get("class") in ("barra", "barra-negativa")]
    assert len(barras) == len(IPCA_VALORES) == 25

    base = _com_classe(svg, "line", "base")
    assert len(base) == 1
    y_base = float(base[0][0]["y1"])

    geoms = [_geometria_barra(a["d"]) for a, _ in barras]
    classes = [a["class"] for a, _ in barras]

    # Sinal: positivas acima do zero, negativas abaixo; classe acompanha.
    for valor, classe, g in zip(IPCA_VALORES, classes, geoms, strict=True):
        assert abs(g["y_zero"] - y_base) < 0.06
        assert abs(g["ultimo_y"] - y_base) < 0.06  # lado do zero é reto
        if valor > 0:
            assert classe == "barra"
            assert g["y_min"] < y_base
            assert g["y_max"] <= y_base + 0.06
        else:
            assert classe == "barra-negativa"
            assert g["y_max"] > y_base
            assert g["y_min"] >= y_base - 0.06
    assert classes[-1] == "barra-negativa"  # ago/2026 = -0,11
    assert classes.count("barra-negativa") == 1

    # Espaço de 2px entre barras adjacentes (coordenadas com 1 casa).
    for anterior, atual in itertools.pairwise(geoms):
        assert abs((atual["x_ini"] - anterior["x_fim"]) - 2.0) <= 0.2

    # Cantos: exatamente 2 arcos por barra, de raio 4, na ponta do dado.
    for classe, g in zip(classes, geoms, strict=True):
        assert len(g["arcos"]) == 2
        for arco in g["arcos"]:
            assert float(arco[0]) == pytest.approx(4.0)
            assert float(arco[1]) == pytest.approx(4.0)
            y_fim_arco = float(arco[6])
            if classe == "barra":
                assert y_fim_arco < y_base
            else:
                assert y_fim_arco > y_base


def test_ipca_mensal_raio_limitado_e_valor_zero_sem_barra():
    valores = [1.0, 1.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.01]
    html = gerar_html(_ipca_df("2025-01-01", valores), GERADO_EM)
    svg = _svg(html, "grafico-ipcamensal")

    barras = [(a, t) for a, t in _elementos(svg, "path") if a.get("class") in ("barra", "barra-negativa")]
    assert len(barras) == len(valores) - 1  # o zero não desenha nada
    assert len(_pontos(svg)) == len(valores)  # mas continua no tooltip

    g = _geometria_barra(barras[-1][0]["d"])
    altura = abs(g["y_min"] - g["y_max"])
    raio = float(g["arcos"][0][0])
    assert altura < 4  # barra baixa: o raio é limitado pela altura
    assert raio < 4
    assert raio == pytest.approx(altura, abs=0.15)


def test_janela_de_5_anos_a_partir_da_ultima_data():
    dias = pd.bdate_range("2019-10-01", "2026-08-31")
    ptax = _serie_df(1, "dolar_ptax_venda", dias, [5.0 + (i % 50) / 100 for i in range(len(dias))])
    selic = _selic_degraus(
        "2019-01-01",
        "2026-08-31",
        [
            ("2019-01-01", 10.0),
            ("2020-03-01", 11.0),
            ("2023-01-01", 12.0),
            ("2024-06-01", 11.5),
        ],
    )
    html = gerar_html(_juntar(selic, ptax), GERADO_EM)
    corte = pd.Timestamp("2021-08-31")  # 2026-08-31 menos 5 anos

    pontos_ptax = _pontos(_svg(html, "grafico-ptax"))
    primeira = datetime.strptime(pontos_ptax[0][2].split(" — ")[0], "%d/%m/%Y")  # noqa: DTZ007
    assert primeira >= corte
    assert len(pontos_ptax) == int((ptax["data"] >= corte).sum())
    assert len(pontos_ptax) < len(ptax)

    pontos_selic = _pontos(_svg(html, "grafico-selic"))
    primeira_selic = datetime.strptime(pontos_selic[0][2].split(" — ")[0], "%d/%m/%Y")  # noqa: DTZ007
    assert primeira_selic == corte
    assert len(pontos_selic) == int((selic["data"] >= corte).sum())

    # Mudanças mais antigas que a janela ficam fora da tabela e do card.
    _, linhas = _tabela(html, "tabela-selic")
    assert [l[0] for l in linhas] == ["01/06/2024", "01/01/2023"]
    assert "01/03/2020" not in html


def test_rotulo_direto_so_no_ultimo_ponto(html_ref: str):
    ultimo_ipca12 = _acumulado_12m(IPCA_VALORES[-12:])
    esperado = {
        "grafico-selic": "14,75%",
        "grafico-ipca12m": formatar_percentual(ultimo_ipca12),
        "grafico-ipcamensal": "\u22120,11%",
        "grafico-ptax": "R$ 5,3784",
    }
    css = _css(html_ref)
    assert re.search(r"\.rotulo-final\s*\{[^}]*fill:\s*var\(--texto\)", css)
    for id_grafico, texto in esperado.items():
        svg = _svg(html_ref, id_grafico)
        finais = _com_classe(svg, "text", "rotulo-final")
        assert [t for _, t in finais] == [texto], id_grafico
        assert len(_com_classe(svg, "circle", "ponto-final")) == 1
        # Fora o rótulo final, nenhum outro texto traz o valor de dado.
        outros = [a.get("class") for a, _ in _elementos(svg, "text") if a.get("class") != "rotulo-final"]
        assert set(outros) <= {"rotulo-y", "rotulo-x"}


def test_data_pontos_para_tooltip(html_ref: str, dados_ref: pd.DataFrame):
    esperado_n = {
        "grafico-selic": int((dados_ref["codigo"] == 432).sum()),
        "grafico-ipca12m": len(ipca_acumulado_12m(dados_ref)),
        "grafico-ipcamensal": len(IPCA_VALORES),
        "grafico-ptax": int((dados_ref["codigo"] == 1).sum()),
    }
    for id_grafico, n in esperado_n.items():
        svg = _svg(html_ref, id_grafico)
        bruto = re.search(r'data-pontos="([^"]*)"', svg).group(1)
        # Aspas do JSON escapadas no atributo (n > 0 em todos os gráficos).
        assert n > 0
        assert "&quot;" in bruto
        assert '"' not in bruto
        pontos = _pontos(svg)
        assert len(pontos) == n, id_grafico
        xs = [p[0] for p in pontos]
        assert xs == sorted(xs)
        assert all(len(p) == 3 and isinstance(p[2], str) for p in pontos)

    ptax = _pontos(_svg(html_ref, "grafico-ptax"))
    assert ptax[-1][2] == "31/08/2026 — R$ 5,3784"
    assert "28/08/2026 — R$ 5,4000" in [p[2] for p in ptax]
    mensal = _pontos(_svg(html_ref, "grafico-ipcamensal"))
    assert mensal[-1][2] == "ago/2026 — \u22120,11%"
    selic = {p[2].split(" — ")[0]: p[2].split(" — ")[1] for p in _pontos(_svg(html_ref, "grafico-selic"))}
    assert selic["10/12/2025"] == "14,75%"
    assert selic["11/12/2025"] == "15,00%"
    assert selic["18/03/2026"] == "15,00%"
    assert selic["19/03/2026"] == "14,75%"


def test_grafico_legivel_sem_javascript(html_ref: str):
    assert html_ref.count("<script") == 1
    sem_js = _sem_script(html_ref)
    assert "<script" not in sem_js
    assert '<div class="tooltip" hidden>' in sem_js
    for id_grafico in _IDS_GRAFICOS:
        svg = _svg(sem_js, id_grafico)
        if id_grafico == "grafico-ipcamensal":
            assert 'class="barra"' in svg
        else:
            assert 'class="linha"' in svg
        assert 'class="grid"' in svg
        assert 'class="rotulo-y"' in svg
        assert 'class="rotulo-x"' in svg
        assert 'class="rotulo-final"' in svg
        assert 'class="ponto-final"' in svg


# ===========================================================================
# Tabelas (25-28)
# ===========================================================================


def test_tabela_selic_mais_recente_primeiro(html_ref: str):
    cabecalhos, linhas = _tabela(html_ref, "tabela-selic")
    assert cabecalhos == ["Data", "De", "Para", "Variação"]
    assert linhas == [
        ["19/03/2026", "15,00%", "14,75%", "▼ queda de \u22120,25 p.p."],
        ["11/12/2025", "14,75%", "15,00%", "▲ alta de +0,25 p.p."],
    ]


def test_tabela_ipca_12_meses_bate_com_analise(html_ref: str, dados_ref: pd.DataFrame):
    cabecalhos, linhas = _tabela(html_ref, "tabela-ipca")
    assert cabecalhos == ["Mês", "IPCA mensal", "Acumulado 12m"]
    esperado = ipca_acumulado_12m(dados_ref).tail(12).iloc[::-1]
    assert len(linhas) == 12
    assert linhas == [
        [
            formatar_mes(r.data),
            formatar_percentual(r.ipca_mensal),
            formatar_percentual(r.acumulado_12m),
        ]
        for r in esperado.itertuples()
    ]
    assert linhas[0][0] == "ago/2026"
    assert linhas[0][1] == "\u22120,11%"
    # Acumulado também confere com o produto calculado à parte.
    assert linhas[0][2] == formatar_percentual(_acumulado_12m(IPCA_VALORES[-12:]))


def test_tabela_ptax_mensal_bate_com_analise(html_ref: str, dados_ref: pd.DataFrame):
    cabecalhos, linhas = _tabela(html_ref, "tabela-ptax")
    assert cabecalhos == ["Mês", "Média", "Fechamento", "Mínimo", "Máximo", "Dias"]
    esperado = ptax_mensal(dados_ref).tail(12).iloc[::-1]
    assert len(linhas) == 12
    assert linhas == [
        [
            formatar_mes(r.mes),
            formatar_moeda(r.media),
            formatar_moeda(r.fechamento),
            formatar_moeda(r.minimo),
            formatar_moeda(r.maximo),
            str(int(r.dias_com_dado)),
        ]
        for r in esperado.itertuples()
    ]
    assert linhas[0][0] == "ago/2026"
    assert linhas[0][2] == "R$ 5,3784"
    assert linhas[1][2] == "R$ 5,2000"  # fechamento de julho
    assert "incompleto" in _texto(_bloco(html_ref, "section", "tabela-ptax"))


def test_tabelas_acessiveis_e_numeros_alinhados(html_ref: str):
    tabelas = re.findall(r"<table>.*?</table>", html_ref, re.DOTALL)
    assert len(tabelas) == 3
    for tabela in tabelas:
        assert re.search(r"<caption>[^<]+</caption>", tabela)
        ths = re.findall(r"<th\b([^>]*)>", tabela)
        assert ths and all('scope="col"' in th for th in ths)
        for tr in re.findall(r"<tr>(.*?)</tr>", re.search(r"<tbody>(.*?)</tbody>", tabela, re.DOTALL).group(1), re.DOTALL):
            for attrs, conteudo in re.findall(r"<td\b([^>]*)>(.*?)</td>", tr, re.DOTALL):
                texto = _texto(conteudo)
                if re.fullmatch(r"(R\$ )?[\u2212\d.,]+%?", texto):
                    assert "num" in _attrs(attrs).get("class", "").split(), texto
        # Cada tabela fica dentro de um contêiner com rolagem horizontal.
    assert html_ref.count('<div class="rolagem"><table>') == 3

    css = _css(html_ref)
    regra_num = re.search(r"\.num\s*\{([^}]*)\}", css).group(1)
    assert "text-align: right" in regra_num
    assert "font-variant-numeric: tabular-nums" in regra_num
    assert re.search(r"\.rolagem\s*\{[^}]*overflow-x:\s*auto", css)


# ===========================================================================
# Design (29-31)
# ===========================================================================

_CLARO = {
    "--superficie": "#fcfcfb",
    "--pagina": "#f9f9f7",
    "--texto": "#0b0b0b",
    "--texto-2": "#52514e",
    "--eixo": "#52514e",
    "--grid": "#e1e0d9",
    "--base": "#c3c2b7",
    "--serie": "#2a78d6",
    "--negativo": "#e34948",
}
_ESCURO = {
    "--superficie": "#1a1a19",
    "--pagina": "#0d0d0d",
    "--texto": "#ffffff",
    "--texto-2": "#c3c2b7",
    "--eixo": "#898781",
    "--grid": "#2c2c2a",
    "--base": "#383835",
    "--serie": "#3987e5",
    "--negativo": "#e66767",
}
_BLOCO_ESCURO = re.compile(r"@media \(prefers-color-scheme: dark\)\s*\{\s*:root\s*\{([^}]*)\}\s*\}")


def test_css_variaveis_claro_e_escuro_com_os_valores_da_spec(html_ref: str):
    css = _css(html_ref)
    escuro = _BLOCO_ESCURO.search(css)
    assert escuro, "falta @media (prefers-color-scheme: dark) com :root"
    claro = re.search(r":root\s*\{([^}]*)\}", css)
    assert _declaracoes(claro.group(1)) == _CLARO
    assert _declaracoes(escuro.group(1)) == _ESCURO
    assert "color-scheme: light dark" in css


def test_nenhuma_cor_literal_fora_das_variaveis(html_ref: str):
    css = _css(html_ref)
    sem_variaveis = _BLOCO_ESCURO.sub("", css)
    sem_variaveis = re.sub(r":root\s*\{[^}]*\}", "", sem_variaveis, count=1)
    resto = html_ref.replace(css, sem_variaveis)
    assert re.findall(r"#[0-9a-fA-F]{3,8}(?![\w-])", resto) == []
    assert not re.search(r"\b(rgb|rgba|hsl|hsla)\(", resto)

    assert 'fill="#' not in html_ref
    assert 'stroke="#' not in html_ref
    assert not re.search(r'style="[^"]*color', html_ref)
    assert 'style="' not in html_ref

    # Texto nunca na cor da série (nem do negativo).
    for propriedade, valor in re.findall(r"(?<![\w-])(color|fill)\s*:\s*(var\(--[\w-]+\))", css):
        if propriedade == "color":
            assert valor not in ("var(--serie)", "var(--negativo)")
    for classe in ("rotulo-y", "rotulo-x", "rotulo-final"):
        m = re.search(rf"\.{classe}\b[^{{]*\{{([^}}]*)\}}", css)
        assert m and "var(--serie)" not in m.group(1) and "var(--negativo)" not in m.group(1)
    # Rótulos das marcas usam --eixo; o rótulo final usa --texto.
    assert re.search(r"\.rotulo-y,\s*\.rotulo-x\s*\{[^}]*fill:\s*var\(--eixo\)", css)
    # Linhas: 2px, sem preenchimento; barras usam as variáveis.
    assert re.search(r"\.linha\s*\{[^}]*stroke:\s*var\(--serie\)", css)
    assert re.search(r"\.linha\s*\{[^}]*stroke-width:\s*2\b", css)
    assert re.search(r"\.linha\s*\{[^}]*fill:\s*none", css)
    assert re.search(r"\.barra\s*\{[^}]*fill:\s*var\(--serie\)", css)
    assert re.search(r"\.barra-negativa\s*\{[^}]*fill:\s*var\(--negativo\)", css)
    # Grid usa --grid; base usa --base; nenhuma linha usa --eixo.
    assert re.search(r"\.grid\s*\{[^}]*stroke:\s*var\(--grid\)", css)
    assert re.search(r"\.base\s*\{[^}]*stroke:\s*var\(--base\)", css)
    assert not re.search(r"stroke:\s*var\(--eixo\)", css)


def test_layout_responsivo(html_ref: str):
    css = _css(html_ref)
    assert "grid-template-columns: repeat(auto-fit, minmax(220px, 1fr))" in css
    assert re.search(r"svg\s*\{[^}]*width:\s*100%", css)
    assert re.search(r"svg\s*\{[^}]*min-width:\s*520px", css)
    assert re.search(r"svg\s*\{[^}]*height:\s*auto", css)
    assert re.search(r"\.rolagem\s*\{[^}]*overflow-x:\s*auto", css)
    assert html_ref.count('<div class="rolagem">') >= 7  # 4 gráficos + 3 tabelas
    assert "system-ui" in css


# ===========================================================================
# Autocontido e segurança (32-35)
# ===========================================================================

_PROIBIDOS = [
    "http://",
    "https://",
    "src=",
    "href=",
    "url(",
    "@import",
    "<link",
    "<img",
    "<iframe",
    "fetch(",
    "XMLHttpRequest",
    "import(",
    "eval(",
]


@pytest.mark.parametrize("origem", ["referencia", "vazio"])
def test_sem_recursos_externos(origem: str, html_ref: str):
    html = html_ref if origem == "referencia" else gerar_html(_dados_vazios(), GERADO_EM)
    for proibido in _PROIBIDOS:
        assert proibido not in html, proibido
    assert html.count("<script") == 1
    assert html.count("<script>") == 1  # sem atributos, logo sem `src`
    assert html.count("</script>") == 1


def test_html_escape_em_texto_vindo_dos_dados(dados_ref: pd.DataFrame):
    malicioso = '<script>alert(1)</script>"><b>'
    escapado = "&lt;script&gt;alert(1)&lt;/script&gt;&quot;&gt;&lt;b&gt;"
    dados = dados_ref.copy()
    # A coluna `serie` é o único texto de dados exibido; injeta nas 3 séries.
    for codigo in (432, 433, 1):
        dados.loc[dados["codigo"] == codigo, "serie"] = malicioso

    html = gerar_html(dados, GERADO_EM)

    assert "<script>alert" not in html
    assert "<b>" not in html
    assert html.count(escapado) == 3  # uma vez por série, no rodapé
    rodape = _texto(re.search(r"<footer>.*?</footer>", html, re.DOTALL).group(0))
    assert rodape.count(malicioso) == 3  # desescapado, é o texto original
    assert html.count("<script") == 1
    assert len(re.findall(r"<svg\b", html)) == 4  # atributos não quebraram
    assert html.count("<table") == 3


def test_relatorio_usa_apenas_stdlib_pandas_e_analise():
    arvore = ast.parse(ARQUIVO_RELATORIO.read_text(encoding="utf-8"))
    importados: list[str] = []
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            importados.extend(a.name for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.level == 0 and no.module:
            importados.append(no.module)

    permitidos_nao_stdlib = {"pandas", "indicadores.analise"}
    for modulo in importados:
        raiz = modulo.split(".")[0]
        assert raiz in sys.stdlib_module_names or modulo in permitidos_nao_stdlib, modulo
    proibidos = {
        "duckdb", "httpx", "matplotlib", "plotly", "jinja2",
        "persistencia", "pipeline", "extracao",
    }  # fmt: skip
    for modulo in importados:
        assert not (set(modulo.split(".")) & proibidos), modulo
    assert "pandas" in importados and "indicadores.analise" in importados


def test_deterministico_e_nao_altera_a_entrada(dados_ref: pd.DataFrame):
    original = dados_ref.copy(deep=True)

    a = gerar_html(dados_ref, GERADO_EM)
    b = gerar_html(dados_ref, GERADO_EM)
    assert a == b
    assert dados_ref.equals(original)

    embaralhado = dados_ref.sample(frac=1.0, random_state=7).reset_index(drop=True)
    assert not embaralhado.equals(dados_ref)
    assert gerar_html(embaralhado, GERADO_EM) == a

    outra = gerar_html(dados_ref, datetime(2026, 11, 20, 8, 5))  # noqa: DTZ001
    assert outra != a
    assert outra.replace("20/11/2026 08:05", "09/10/2026 14:30") == a


# ===========================================================================
# Dados insuficientes (36-40)
# ===========================================================================


def test_dataframe_vazio_gera_documento_completo():
    html = gerar_html(_dados_vazios(), GERADO_EM)

    assert re.findall(r'<article class="card" id="([^"]+)"', html) == _IDS_CARDS
    for id_ in _IDS_CARDS:
        assert SEM_DADOS in _texto(_bloco(html, "article", id_))
    for id_ in _IDS_GRAFICOS + _IDS_TABELAS:
        bloco = _bloco(html, "section", id_)
        assert SEM_DADOS in _texto(bloco)
        assert '<p class="vazio">' in bloco
    assert "<svg" not in html
    assert "Período: sem dados" in _texto(html)
    assert "Fonte: SGS" in html
    assert "<h1>" in html and html.rstrip().endswith("</html>")


# (card, gráfico(s), tabela) afetados por cada código.
_SECOES_POR_SERIE = {
    432: (["card-selic"], ["grafico-selic"], ["tabela-selic"]),
    433: (
        ["card-ipca12m", "card-ipcamensal"],
        ["grafico-ipca12m", "grafico-ipcamensal"],
        ["tabela-ipca"],
    ),
    1: (["card-ptax"], ["grafico-ptax"], ["tabela-ptax"]),
}


@pytest.mark.parametrize("codigo", [432, 433, 1])
def test_serie_ausente_so_afeta_as_secoes_da_serie(codigo: int, dados_ref: pd.DataFrame):
    dados = dados_ref[dados_ref["codigo"] != codigo].reset_index(drop=True)
    html = gerar_html(dados, GERADO_EM)

    cards, graficos, tabelas = _SECOES_POR_SERIE[codigo]
    afetadas = set(cards + graficos + tabelas)
    for id_ in cards:
        assert SEM_DADOS in _texto(_bloco(html, "article", id_))
    for id_ in graficos + tabelas:
        assert SEM_DADOS in _texto(_bloco(html, "section", id_))
    assert len(afetadas) == {432: 3, 433: 5, 1: 3}[codigo]

    for id_ in _IDS_CARDS:
        if id_ not in afetadas:
            assert SEM_DADOS not in _texto(_bloco(html, "article", id_))
    for id_ in _IDS_GRAFICOS + _IDS_TABELAS:
        if id_ not in afetadas:
            assert SEM_DADOS not in _texto(_bloco(html, "section", id_))
    # Seções afetadas continuam presentes (mesmo id).
    assert html.count("<table") == 3 - len(tabelas)
    assert len(re.findall(r"<svg\b", html)) == 4 - len(graficos)


def test_historico_curto():
    ipca_11 = _ipca_df("2025-10-01", [0.3] * 10 + [-0.11])  # 2025-10 .. 2026-08
    selic_1 = _serie_df(432, "selic_meta", ["2026-08-31"], [14.75])
    ptax_1 = _serie_df(1, "dolar_ptax_venda", ["2026-08-31"], [5.3784])
    html = gerar_html(_juntar(ipca_11, selic_1, ptax_1), GERADO_EM)

    # IPCA com 11 meses: tudo que depende de 12 meses fica vazio...
    assert SEM_DADOS in _texto(_bloco(html, "article", "card-ipca12m"))
    assert SEM_DADOS in _texto(_bloco(html, "section", "grafico-ipca12m"))
    assert SEM_DADOS in _texto(_bloco(html, "section", "tabela-ipca"))
    # ...mas o IPCA mensal usa a série bruta e funciona.
    mensal = _texto(_bloco(html, "article", "card-ipcamensal"))
    assert "\u22120,11%" in mensal and "ago/2026" in mensal
    svg = _svg(html, "grafico-ipcamensal")
    barras = [a for a, _ in _elementos(svg, "path") if a.get("class") in ("barra", "barra-negativa")]
    assert len(barras) == 11

    # Selic com 1 observação.
    selic = _texto(_bloco(html, "article", "card-selic"))
    assert "14,75%" in selic and "Sem mudanças no período" in selic
    assert SEM_DADOS in _texto(_bloco(html, "section", "grafico-selic"))
    assert "Sem mudanças no período" in _texto(_bloco(html, "section", "tabela-selic"))

    # PTAX com 1 observação: valor e data, duas variações indisponíveis.
    ptax = _texto(_bloco(html, "article", "card-ptax"))
    assert "R$ 5,3784" in ptax and "31/08/2026" in ptax
    assert ptax.count(INDISPONIVEL) == 2
    assert SEM_DADOS in _texto(_bloco(html, "section", "grafico-ptax"))


def test_lacunas_viram_variacao_indisponivel():
    # IPCA: 12 meses (2024-01..2024-12), lacuna em 2025-01, depois 12 meses
    # (2025-02..2026-01). O acumulado 12m tem linhas em 2024-12 e 2026-01,
    # que não são meses consecutivos.
    primeiros = _ipca_df("2024-01-01", [0.4] * 12)
    ultimos = _ipca_df("2025-02-01", [0.5] * 12)
    # PTAX: sem nenhum dado em julho/2026 (mês anterior ao último dado).
    ptax = _serie_df(
        1,
        "dolar_ptax_venda",
        ["2026-06-30", "2026-08-28", "2026-08-31"],
        [5.1, 5.4, 5.3784],
    )
    html = gerar_html(_juntar(primeiros, ultimos, ptax), GERADO_EM)

    ipca12 = _texto(_bloco(html, "article", "card-ipca12m"))
    esperado = formatar_percentual(_acumulado_12m([0.5] * 12))
    assert esperado in ipca12 and "jan/2026" in ipca12
    assert ipca12.count(INDISPONIVEL) == 1
    # O IPCA mensal não é afetado.
    assert INDISPONIVEL not in _texto(_bloco(html, "article", "card-ipcamensal"))

    card_ptax = _texto(_bloco(html, "article", "card-ptax"))
    assert "R$ 5,3784" in card_ptax
    assert card_ptax.count(INDISPONIVEL) == 1
    assert re.search(r"dia anterior.*?▼ queda de \u22120,40%", card_ptax)  # esta segue normal
    assert re.search(r"fechamento.*?" + INDISPONIVEL, card_ptax)  # só esta falha


@pytest.mark.parametrize("coluna", ["valor", "codigo", "data"])
def test_colunas_faltando_propaga_erro_analise(coluna: str, dados_ref: pd.DataFrame):
    with pytest.raises(ErroAnalise):
        gerar_html(dados_ref.drop(columns=[coluna]), GERADO_EM)


# ===========================================================================
# Escrita (41-43)
# ===========================================================================


def test_gravar_relatorio_utf8_cria_diretorio_e_substitui(tmp_path: Path):
    destino = tmp_path / "saida" / "sub" / "relatorio.html"
    conteudo = "<p>Olá — ação ✓ −0,11%</p>\nlinha 2\n"

    gravar_relatorio(conteudo, destino)

    assert destino.read_bytes() == conteudo.encode("utf-8")  # UTF-8 e "\n" intactos
    assert destino.read_text(encoding="utf-8") == conteudo

    gravar_relatorio("novo conteúdo", destino)
    assert destino.read_text(encoding="utf-8") == "novo conteúdo"
    assert list(destino.parent.glob("*.tmp")) == []
    assert sorted(p.name for p in destino.parent.iterdir()) == ["relatorio.html"]


def test_gravar_relatorio_atomico_quando_replace_falha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    destino = tmp_path / "relatorio.html"
    destino.write_text("ORIGINAL", encoding="utf-8")

    def _replace_falho(origem, alvo):
        raise OSError("replace falhou")

    monkeypatch.setattr(relatorio.os, "replace", _replace_falho)

    with pytest.raises(OSError, match="replace falhou"):
        gravar_relatorio("NOVO", destino)

    assert destino.read_text(encoding="utf-8") == "ORIGINAL"
    assert not (tmp_path / "relatorio.html.tmp").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["relatorio.html"]


def test_gravar_relatorio_limpa_temporario_quando_escrita_falha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    destino = tmp_path / "relatorio.html"
    destino.write_text("ORIGINAL", encoding="utf-8")

    def _fsync_falho(fd):
        # O temporário já tem conteúdo escrito neste ponto.
        assert (tmp_path / "relatorio.html.tmp").exists()
        raise OSError("disco cheio")

    monkeypatch.setattr(relatorio.os, "fsync", _fsync_falho)

    with pytest.raises(OSError, match="disco cheio"):
        gravar_relatorio("NOVO", destino)

    monkeypatch.undo()
    assert destino.read_text(encoding="utf-8") == "ORIGINAL"
    assert not (tmp_path / "relatorio.html.tmp").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["relatorio.html"]


# ===========================================================================
# Revisão de código: coordenadas dos gráficos (M2)
# ===========================================================================

_MENOS = "−"
_MES_RE = re.compile(r"^(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)/\d{4}$")


def _numero_pt_br(texto: str) -> float:
    """Inverso de `formatar_numero` (aceita prefixo `R$ ` e sufixo `%`)."""
    limpo = texto.replace("R$", "").replace("%", "").strip()
    limpo = limpo.replace(_MENOS, "-").replace(".", "").replace(",", ".")
    return float(limpo)


def _escala_y(svg: str) -> list[tuple[float, float]]:
    """Pares (valor do rótulo Y, y da linha de grade), na ordem do SVG."""
    rotulos = _com_classe(svg, "text", "rotulo-y")
    grades = _com_classe(svg, "line", "grid")
    assert len(rotulos) == len(grades) >= 4
    pares = []
    for (a_rot, texto), (a_grade, _) in zip(rotulos, grades, strict=True):
        y_grade = float(a_grade["y1"])
        assert a_grade["y1"] == a_grade["y2"]
        # O rótulo fica 4 unidades abaixo da linha que ele nomeia.
        assert float(a_rot["y"]) == pytest.approx(y_grade + 4, abs=0.11)
        pares.append((_numero_pt_br(texto), y_grade))
    return pares


def _y_interpolado(pares: list[tuple[float, float]], valor: float) -> float:
    (v0, y0), (v1, y1) = pares[0], pares[-1]
    return y0 + (valor - v0) / (v1 - v0) * (y1 - y0)


def test_coordenadas_eixo_y_crescem_para_cima(html_ref: str):
    for id_grafico in _IDS_GRAFICOS:
        pares = _escala_y(_svg(html_ref, id_grafico))
        valores = [v for v, _ in pares]
        ys = [y for _, y in pares]
        assert valores == sorted(valores) and len(set(valores)) == len(valores)
        assert all(a > b for a, b in itertools.pairwise(ys)), id_grafico  # y diminui
        # Marcas igualmente espaçadas em valor => igualmente espaçadas em y.
        passos = [b - a for a, b in itertools.pairwise(valores)]
        deltas = [a - b for a, b in itertools.pairwise(ys)]
        for passo, delta in zip(passos, deltas, strict=True):
            assert delta / passo == pytest.approx(deltas[0] / passos[0], rel=0.01)
        # Todo o eixo cabe dentro da área de plotagem.
        assert ys[0] <= relatorio._Y1 + 0.05
        assert ys[-1] >= relatorio._Y0 - 0.05


def test_ponto_final_na_posicao_linear_do_ultimo_valor(html_ref: str):
    ultimo_ipca12 = _acumulado_12m(IPCA_VALORES[-12:])
    esperado = {
        "grafico-selic": 14.75,
        "grafico-ipca12m": ultimo_ipca12,
        "grafico-ipcamensal": -0.11,
        "grafico-ptax": 5.3784,
    }
    for id_grafico, ultimo in esperado.items():
        svg = _svg(html_ref, id_grafico)
        pares = _escala_y(svg)
        ((ponto, _),) = _com_classe(svg, "circle", "ponto-final")
        cy = float(ponto["cy"])
        assert cy == pytest.approx(_y_interpolado(pares, ultimo), abs=0.3), id_grafico
        # O texto do rótulo final fica 4 unidades abaixo do ponto.
        ((rotulo, _),) = _com_classe(svg, "text", "rotulo-final")
        assert float(rotulo["y"]) == pytest.approx(cy + 4, abs=0.11)
        # O tooltip do último ponto usa a mesma coordenada do ponto marcado.
        x_final = float(ponto["cx"])
        x_tip, y_tip, _ = _pontos(svg)[-1]
        assert y_tip == pytest.approx(cy, abs=0.11)
        assert x_tip == pytest.approx(x_final, abs=0.11)
        assert relatorio._X0 <= x_final <= relatorio._X1


def test_valores_maiores_ficam_mais_acima(html_ref: str):
    for id_grafico in _IDS_GRAFICOS:
        pontos = _pontos(_svg(html_ref, id_grafico))
        por_valor: dict[float, list[float]] = {}
        for _, y, texto in pontos:
            por_valor.setdefault(_numero_pt_br(texto.split(" — ")[1]), []).append(y)
        valores = sorted(por_valor)
        assert len(valores) >= 2
        for menor, maior in itertools.pairwise(valores):
            # maior valor => y menor (mais acima); tolerância de arredondamento
            assert max(por_valor[maior]) <= min(por_valor[menor]) + 0.3, (id_grafico, menor, maior)
        assert min(por_valor[valores[-1]]) < max(por_valor[valores[0]])


def _dados_cinco_anos() -> pd.DataFrame:
    """Selic, IPCA e PTAX cobrindo exatamente a janela de 5 anos (2021-09 a 2026-08)."""
    dias_selic = pd.date_range("2021-09-01", "2026-08-31")
    selic = _serie_df(
        432, "selic_meta", dias_selic, [10.0 + (i // 90 % 7) * 0.25 for i in range(len(dias_selic))]
    )
    ipca = _ipca_df("2021-09-01", [round(0.2 + (i % 5) / 10 - 0.1, 2) for i in range(60)])
    dias = pd.bdate_range("2021-09-01", "2026-08-31")
    ptax = _serie_df(
        1, "dolar_ptax_venda", dias, [round(5.0 + ((i * 7) % 50) / 100, 4) for i in range(len(dias))]
    )
    return _juntar(selic, ipca, ptax)


def _rotulos_x(svg: str) -> list[tuple[float, str]]:
    return [(float(a["x"]), t) for a, t in _com_classe(svg, "text", "rotulo-x")]


def test_rotulos_eixo_x_sao_os_anos_esperados_em_5_anos():
    html = gerar_html(_dados_cinco_anos(), GERADO_EM)
    esperado = {
        # 1º de janeiro de cada ano dentro da janela de cada gráfico.
        "grafico-selic": ["2022", "2023", "2024", "2025", "2026"],
        "grafico-ipcamensal": ["2022", "2023", "2024", "2025", "2026"],
        "grafico-ptax": ["2022", "2023", "2024", "2025", "2026"],
        # O acumulado 12m só começa em 2022-08 (12º mês), logo sem "2022".
        "grafico-ipca12m": ["2023", "2024", "2025", "2026"],
    }
    for id_grafico, anos in esperado.items():
        rotulos = _rotulos_x(_svg(html, id_grafico))
        assert [t for _, t in rotulos] == anos, id_grafico
        xs = [x for x, _ in rotulos]
        assert xs == sorted(xs)
        assert all(relatorio._X0 <= x <= relatorio._X1 for x in xs), id_grafico
        assert all(b - a > 40 for a, b in itertools.pairwise(xs))  # sem sobreposição


def test_rotulos_eixo_x_no_dataset_de_referencia_nao_vazam_da_area(html_ref: str):
    for id_grafico in _IDS_GRAFICOS:
        rotulos = _rotulos_x(_svg(html_ref, id_grafico))
        assert rotulos, id_grafico
        for x, texto in rotulos:
            assert relatorio._X0 <= x <= relatorio._X1, (id_grafico, texto)
            assert re.fullmatch(r"\d{4}", texto) or _MES_RE.match(texto), texto


@pytest.mark.parametrize(
    ("inicio", "fim"),
    [
        ("2026-06-01", "2026-08-31"),  # 3 meses
        ("2025-12-01", "2026-08-31"),  # 9 meses (> 6: é afinado)
        ("2025-01-01", "2026-08-31"),  # 20 meses, ainda < 2 anos
    ],
)
def test_rotulos_eixo_x_janela_curta_ate_6_meses_mmm_aaaa(inicio: str, fim: str):
    dias = pd.date_range(inicio, fim)
    selic = _serie_df(432, "selic_meta", dias, [14.0 + (i // 30) * 0.25 for i in range(len(dias))])
    html = gerar_html(selic, GERADO_EM)

    rotulos = _rotulos_x(_svg(html, "grafico-selic"))
    textos = [t for _, t in rotulos]
    assert 1 <= len(textos) <= 6
    assert all(_MES_RE.match(t) for t in textos), textos
    assert textos[0] == formatar_mes(inicio)
    xs = [x for x, _ in rotulos]
    assert xs == sorted(xs)
    assert all(relatorio._X0 <= x <= relatorio._X1 for x in xs)
    assert all(b - a > 40 for a, b in itertools.pairwise(xs))


# ===========================================================================
# Revisão de código: contrato de `serie` e valores não finitos (M4)
# ===========================================================================

_SERIES_PADRAO = "432 selic_meta · 433 ipca_mensal · 1 dolar_ptax_venda"


def _linha_series(html: str) -> str:
    m = re.search(r"<p>Séries: (.*?)</p>", html, re.DOTALL)
    assert m
    return html_lib.unescape(m.group(1))


def test_sem_coluna_serie_usa_nomes_padrao_no_rodape(dados_ref: pd.DataFrame):
    html = gerar_html(dados_ref.drop(columns=["serie"]), GERADO_EM)

    assert _linha_series(html) == _SERIES_PADRAO


@pytest.mark.parametrize("vazio", ["", "   ", None])
def test_serie_vazia_usa_nomes_padrao_no_rodape(dados_ref: pd.DataFrame, vazio):
    dados = dados_ref.copy()
    dados["serie"] = vazio

    html = gerar_html(dados, GERADO_EM)

    assert _linha_series(html) == _SERIES_PADRAO


def test_serie_vazia_so_numa_serie_e_nome_proprio_nas_outras(dados_ref: pd.DataFrame):
    dados = dados_ref.copy()
    dados.loc[dados["codigo"] == 432, "serie"] = ""
    dados.loc[dados["codigo"] == 433, "serie"] = "ipca_personalizado"

    html = gerar_html(dados, GERADO_EM)

    assert _linha_series(html) == "432 selic_meta · 433 ipca_personalizado · 1 dolar_ptax_venda"


def test_nan_no_meio_da_ptax_e_ignorado_pelo_grafico(dados_ref: pd.DataFrame):
    dados = dados_ref.copy()
    ptax_idx = dados.index[dados["codigo"] == 1]
    n_ptax = len(ptax_idx)
    meio = ptax_idx[n_ptax // 2]
    data_nan = dados.loc[meio, "data"]
    dados.loc[meio, "valor"] = float("nan")

    html = gerar_html(dados, GERADO_EM)  # não levanta

    svg = _svg(html, "grafico-ptax")
    pontos = _pontos(svg)
    assert len(pontos) == n_ptax - 1  # o NaN não conta
    assert formatar_data(data_nan) not in [p[2].split(" — ")[0] for p in pontos]
    assert not any("nan" in p[2].lower() for p in pontos)
    assert all(math.isfinite(p[0]) and math.isfinite(p[1]) for p in pontos)
    # O último ponto e o card seguem normais.
    assert pontos[-1][2] == "31/08/2026 — R$ 5,3784"
    assert "R$ 5,3784" in _texto(_bloco(html, "article", "card-ptax"))
    # O caminho da linha só tem coordenadas finitas.
    ((caminho, _),) = _com_classe(svg, "path", "linha")
    assert re.fullmatch(r"M[\d. -]+(?:L[\d. -]+)*", caminho["d"])


def test_nan_no_ultimo_valor_do_card_ptax(dados_ref: pd.DataFrame):
    dados = dados_ref.copy()
    ultimo = dados.index[(dados["codigo"] == 1) & (dados["data"] == "2026-08-31")][0]
    dados.loc[ultimo, "valor"] = float("nan")

    html = gerar_html(dados, GERADO_EM)  # não levanta

    card = _texto(_bloco(html, "article", "card-ptax"))
    # Spec: `formatar_numero` devolve "—" para não finito.
    assert "R$ —" in card
    assert "nan" not in card.lower()
    # Sem referência finita, as variações viram indisponíveis.
    assert card.count(INDISPONIVEL) == 2
    # O gráfico ignora o ponto: o rótulo final é o último valor finito.
    svg = _svg(html, "grafico-ptax")
    assert [t for _, t in _com_classe(svg, "text", "rotulo-final")] == ["R$ 5,4000"]
    assert _pontos(svg)[-1][2] == "28/08/2026 — R$ 5,4000"


def test_nan_no_ultimo_valor_do_card_ipca_mensal(dados_ref: pd.DataFrame):
    dados = dados_ref.copy()
    ultimo = dados.index[(dados["codigo"] == 433) & (dados["data"] == "2026-08-01")][0]
    dados.loc[ultimo, "valor"] = float("nan")

    html = gerar_html(dados, GERADO_EM)  # não levanta

    card = _texto(_bloco(html, "article", "card-ipcamensal"))
    assert "—" in card and "nan" not in card.lower()
    barras = [
        a
        for a, _ in _elementos(_svg(html, "grafico-ipcamensal"), "path")
        if a.get("class") in ("barra", "barra-negativa")
    ]
    assert len(barras) == len(IPCA_VALORES) - 1  # a barra do NaN não é desenhada


# ===========================================================================
# Revisão de código: decisões 13 a 17 (relatorio.md, casos 61-63 e 66-70)
# ===========================================================================


@pytest.mark.parametrize("excecao", [KeyboardInterrupt, SystemExit])
def test_gravar_relatorio_interrupcao_limpa_temporario_e_preserva_anterior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, excecao
):
    destino = tmp_path / "relatorio.html"
    destino.write_text("ORIGINAL", encoding="utf-8")

    def _replace_interrompido(origem, alvo):
        assert Path(origem).read_text(encoding="utf-8") == "NOVO"  # já escrito
        raise excecao()

    monkeypatch.setattr(relatorio.os, "replace", _replace_interrompido)

    with pytest.raises(excecao):
        gravar_relatorio("NOVO", destino)

    assert not (tmp_path / "relatorio.html.tmp").exists()
    assert destino.read_text(encoding="utf-8") == "ORIGINAL"


def test_gravar_relatorio_faz_flush_e_fsync_antes_do_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    destino = tmp_path / "relatorio.html"
    destino.write_text("ORIGINAL", encoding="utf-8")
    html = "<html>conteúdo completo</html>\n" * 50
    eventos: list[str] = []
    conteudo_no_replace: list[str] = []
    fsync_real, replace_real = relatorio.os.fsync, relatorio.os.replace

    def _fsync(fd):
        eventos.append("fsync")
        assert isinstance(fd, int)
        return fsync_real(fd)

    def _replace(origem, alvo):
        eventos.append("replace")
        conteudo_no_replace.append(Path(origem).read_text(encoding="utf-8"))
        return replace_real(origem, alvo)

    monkeypatch.setattr(relatorio.os, "fsync", _fsync)
    monkeypatch.setattr(relatorio.os, "replace", _replace)

    gravar_relatorio(html, destino)

    assert eventos.count("fsync") >= 1
    assert eventos.index("fsync") < eventos.index("replace")
    assert conteudo_no_replace == [html]
    assert destino.read_text(encoding="utf-8") == html


def test_gravar_relatorio_falha_ao_remover_temporario_nao_mascara_a_excecao(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    destino = tmp_path / "relatorio.html"
    destino.write_text("ORIGINAL", encoding="utf-8")

    def _replace_falho(origem, alvo):
        raise OSError("replace")

    def _unlink_falho(self, *args, **kwargs):
        raise OSError("unlink")

    monkeypatch.setattr(relatorio.os, "replace", _replace_falho)
    monkeypatch.setattr(Path, "unlink", _unlink_falho)

    with pytest.raises(OSError) as info:
        gravar_relatorio("NOVO", destino)

    assert str(info.value) == "replace"
    monkeypatch.undo()
    assert destino.read_text(encoding="utf-8") == "ORIGINAL"
    (tmp_path / "relatorio.html.tmp").unlink(missing_ok=True)


def test_css_svg_dos_graficos_tem_touch_action_pan_y(html_ref: str):
    css = _css(html_ref)
    m = re.search(r"@media\s*\(min-width:\s*560px\)\s*\{(.*?)\n\}", css, re.DOTALL)
    assert m, "falta @media (min-width: 560px)"
    bloco = m.group(1)
    regra = re.search(r"([^{}]+)\{[^}]*touch-action:\s*pan-y\s*;?[^}]*\}", bloco)
    assert regra, "touch-action: pan-y ausente dentro da @media"
    assert re.search(r"(^|[\s,])svg\b", regra.group(1).strip())
    resto = css.replace(m.group(0), "")
    assert "touch-action" not in resto
    fora_do_style = html_ref.replace(css, "")
    assert "touch-action" not in re.sub(r"<script>.*?</script>", "", fora_do_style, flags=re.DOTALL)


def test_js_trata_pointercancel_escondendo_o_tooltip(html_ref: str):
    scripts = re.findall(r"<script>(.*?)</script>", html_ref, re.DOTALL)
    assert len(scripts) == 1
    js = scripts[0]
    cancel = re.search(r"addEventListener\(\s*['\"]pointercancel['\"]\s*,\s*(\w+)", js)
    saiu = re.search(r"addEventListener\(\s*['\"]pointerleave['\"]\s*,\s*(\w+)", js)
    assert cancel and saiu
    assert cancel.group(1) == saiu.group(1)  # mesmo tratador
    nome = cancel.group(1)
    corpo = re.search(rf"function\s+{nome}\s*\([^)]*\)\s*\{{(.*?)\n\s*\}}", js, re.DOTALL)
    assert corpo
    assert "hidden = true" in corpo.group(1)
    assert "oculto" in corpo.group(1)


def _df_nomes(linhas: list[tuple[str, object]], codigo: int = 432) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "codigo": pd.Series([codigo] * len(linhas), dtype="int64"),
            "serie": pd.Series([n for _, n in linhas], dtype=object),
            "data": pd.to_datetime([d for d, _ in linhas]),
            "valor": [1.0] * len(linhas),
        }
    )


_CASOS_NOME = [
    pytest.param(
        [("2025-01-01", "selic_antiga"), ("2026-01-01", "selic_meta")], "selic_meta", id="renomeada"
    ),
    pytest.param(
        [("2025-01-01", "selic_antiga"), ("2026-01-01", "selic_meta"), ("2025-06-01", "x_meio")],
        "selic_meta",
        id="tres",
    ),
    pytest.param([("2025-01-01", "antiga"), ("2026-01-01", "")], "antiga", id="recente_vazio"),
    pytest.param([("2025-01-01", "antiga"), ("2026-01-01", None)], "antiga", id="recente_none"),
    pytest.param([("2025-01-01", "antiga"), ("2026-01-01", "   ")], "antiga", id="recente_espacos"),
    pytest.param([("2026-01-01", "zeta"), ("2026-01-01", "alfa")], "alfa", id="empate_alfabetico"),
]


@pytest.mark.parametrize("embaralhar", [False, True])
@pytest.mark.parametrize(("linhas", "esperado"), _CASOS_NOME)
def test_nome_da_serie_usa_a_linha_de_maior_data(linhas, esperado, embaralhar):
    df = _df_nomes(linhas)
    if embaralhar:
        df = df.iloc[::-1].reset_index(drop=True)
    assert relatorio._nome_da_serie(df, 432) == esperado


def test_nome_da_serie_sem_coluna_ou_toda_vazia_usa_padrao():
    padrao = relatorio._NOMES_PADRAO[432]
    sem_coluna = _df_nomes([("2026-01-01", "x")]).drop(columns=["serie"])
    assert relatorio._nome_da_serie(sem_coluna, 432) == padrao
    vazia = _df_nomes([("2026-01-01", None), ("2026-02-01", "  ")])
    assert relatorio._nome_da_serie(vazia, 432) == padrao


@pytest.mark.parametrize("embaralhar", [False, True])
def test_rodape_usa_nome_da_linha_mais_recente(dados_ref: pd.DataFrame, embaralhar: bool):
    dados = dados_ref.copy()
    antigas = (dados["codigo"] == 432) & (dados["data"] < "2026-01-01")
    dados.loc[antigas, "serie"] = "selic_antiga"
    if embaralhar:
        dados = dados.sample(frac=1, random_state=3).reset_index(drop=True)

    assert _linha_series(gerar_html(dados, GERADO_EM)) == _SERIES_PADRAO


@pytest.mark.parametrize(
    ("escala", "esperado"),
    [(0.5, 1), (2, 1), (3, 1), (9.5, 7.5), (26, 24), (100, 24), (572, 24)],
)
def test_largura_da_barra_formula_e_limites(escala, esperado):
    assert relatorio._calcular_largura_barra(escala) == pytest.approx(esperado)
    assert relatorio.ESPACO_ENTRE_BARRAS == 2
    assert relatorio.LARGURA_MAXIMA_BARRA == 24


def _barras_ipca(html: str) -> list[dict]:
    svg = _svg(html, "grafico-ipcamensal")
    return [
        _geometria_barra(a["d"])
        for a, _ in _elementos(svg, "path")
        if a.get("class") in ("barra", "barra-negativa")
    ]


@pytest.mark.parametrize("meses", [1, 2, 3])
def test_ipca_poucos_meses_nao_gera_barra_gigante(meses: int):
    html = gerar_html(_ipca_df("2026-06-01", [0.5, 0.3, 0.4][:meses]), GERADO_EM)
    barras = _barras_ipca(html)
    assert len(barras) == meses
    for g in barras:
        assert 0 < g["x_fim"] - g["x_ini"] <= 24.1
    centros = [(g["x_ini"] + g["x_fim"]) / 2 for g in barras]
    meio_area = (relatorio._X0 + relatorio._X1) / 2
    assert sum(centros) / meses == pytest.approx(meio_area, abs=0.2)
    if meses == 1:
        assert centros[0] == pytest.approx(meio_area, abs=0.2)


def test_ipca_25_meses_largura_e_escala_menos_espaco(html_ref: str):
    escala = (relatorio._X1 - relatorio._X0) / 25
    for g in _barras_ipca(html_ref):
        assert g["x_fim"] - g["x_ini"] == pytest.approx(escala - 2, abs=0.2)
