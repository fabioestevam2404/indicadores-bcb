# Especificação — Relatório HTML (`src/indicadores/relatorio.py`)

## Status
Sétimo módulo do projeto. `analise.py`, `persistencia.py`, `pipeline.py` e
`__main__.py` já estão implementados (lidos diretamente para confirmar
contratos): `persistencia.ler(conexao)` devolve `DataFrame` com colunas
`["codigo", "serie", "data", "valor", "atualizado_em"]`; `analise.py`
expõe `mudancas_selic`, `ipca_acumulado_12m` e `ptax_mensal` (puras,
`ErroAnalise` se faltar `codigo`/`data`/`valor`); a CLI tem os exit codes
`0`, `1`, `2` e `3`, e o wrapper `scripts/executar_diario.ps1` repassa o
exit code do Python e só repete a execução no exit `1`. Este módulo
consome os dados já gravados e produz um relatório HTML. Segue o mesmo
formato das specs anteriores.

Spec **aprovada pelo usuário em 09/10/2026**, com as respostas às cinco
dúvidas registradas em "Decisões da revisão de 09/10/2026". Não há mais
dúvidas em aberto. As mudanças que esta spec descreve em
`specs/pipeline.md` e `specs/agendamento.md` já foram aplicadas nesses
arquivos (ver "Mudanças em outros documentos e testes existentes").

Atualização (09/10/2026, depois da revisão de código do `code-reviewer`):
o usuário aprovou 4 melhorias de baixa severidade e 2 ajustes de
robustez, registrados como decisões 12 a 17 em "Melhorias da revisão de
código de 09/10/2026". Elas alteram trechos desta spec (marcados com a
referência à decisão), `specs/pipeline.md` (passo a passo de `main`) e
`specs/persistencia.md` (`abrir_conexao`). Os casos de teste novos são os
de número 58 a 70.

Estado real do banco em 08/10/2026 (referência para a verificação manual,
não para os testes automáticos): tabela `indicadores` com a série `1`
(`dolar_ptax_venda`, diária, 1264 linhas), `432` (`selic_meta`, diária,
1837 linhas) e `433` (`ipca_mensal`, mensal, 60 linhas, último mês
2026-08).

## Objetivo
Gerar, a cada execução do pipeline (e sob demanda, sem acessar a API), um
**único arquivo HTML autocontido**, `relatorio.html`, com:
1. **Resumo executivo**: um card por indicador.
2. **Gráficos históricos**: um gráfico por indicador, cada um com seu
   próprio eixo.
3. **Tabelas de análise**: mudanças da Selic, IPCA mensal + acumulado em
   12 meses, PTAX mensal.

**Não** há seção de status da execução (inseridos, atualizados,
descartes, séries ignoradas): isso continua sendo o resumo em stdout e o
log (`specs/pipeline.md`). O relatório descreve os **dados**, não a
execução.

## Decisões já tomadas pelo usuário (não reabertas)
1. **Módulo novo `src/indicadores/relatorio.py`, com núcleo puro.** Uma
   função recebe o `DataFrame` (`codigo`/`data`/`valor`, como `analise`
   recebe) e a data/hora de geração (injetada, para testes
   determinísticos) e devolve a `str` HTML. Gravar o arquivo é outra
   função, separada e fina. O módulo **não acessa o banco nem a rede**
   (mesmo princípio de `analise.py`); quem lê do banco é o `__main__`.
2. **HTML autocontido, sem dependência nova.** CSS inline, gráficos em
   SVG inline gerado em Python, JS mínimo inline só para o tooltip.
   Proibido CDN, fonte externa, imagem externa ou qualquer requisição de
   rede — o arquivo abre offline. Só biblioteca padrão + `pandas` (nada de
   matplotlib/plotly/jinja). Todo texto interpolado no HTML passa por
   `html.escape`.
3. **Saída `relatorio.html` na raiz do projeto**, ignorada pelo git
   (acrescentar ao `.gitignore`, ao lado de `relatorio.md`). Escrita
   atômica (arquivo temporário + `os.replace`).
4. **Integração na CLI**: o relatório é regenerado depois de cada execução
   normal; `--so-relatorio` só regenera (sem API) e `--sem-relatorio`
   pula. Falha no relatório nunca impede nem desfaz a gravação dos
   dados. Detalhes e exit codes na seção "Integração na CLI" (parte
   dessas escolhas é minha, com motivo registrado lá).
5. **Formatação de exibição em pt-BR** (vírgula decimal, `dd/mm/aaaa`,
   `ago/2026`, percentual com 2 casas, PTAX com 4 casas e `R$`). É
   **só exibição**: os dados continuam em ISO/`float` conforme o
   `CLAUDE.md`. Não depende de locale do SO.
6. **Conteúdo, design e valores de cor** conforme as seções "Conteúdo" e
   "Design" abaixo (valores fornecidos pelo usuário).

### Decisões da revisão de 09/10/2026 (aprovadas pelo usuário)
Respostas às cinco dúvidas que estavam em aberto; já refletidas nas seções
correspondentes abaixo.
7. **Rótulos das marcas dos eixos no tema claro usam `#52514e`** (texto
   secundário, contraste AA), não `#898781`. As linhas de eixo/base
   continuam como especificado (`--grid` e `--base`; nenhuma linha usa
   `--eixo`).
8. **Rótulos dos eixos no tema escuro: `#898781`** (~4,9:1 sobre
   `#1a1a19`) — aceito.
9. **`.gitignore` ganha `relatorio.html` e `relatorio.html.tmp`.**
10. **A tabela do IPCA mostra apenas meses com janela completa de 12
    meses** (saída de `ipca_acumulado_12m`) — aceito. Com menos de 23
    meses de histórico ela mostra menos de 12 linhas.
11. **Exit `1` com falha do relatório:** o exit continua `1` e a falha do
    relatório fica só no log (`ERROR` no stderr) — aceito.

### Melhorias da revisão de código de 09/10/2026 (aprovadas pelo usuário)
Quatro melhorias de baixa severidade apontadas pelo `code-reviewer`
(decisões 12 a 15) e dois ajustes de robustez (16 e 17). A numeração
continua a das decisões acima. As decisões 12 a 15 mudam comportamento ou
contrato de forma localizada, descrita em cada uma; as 16 e 17 não mudam
nada observável além do que está escrito nelas. Os trechos afetados nas
seções abaixo trazem a referência `(decisão N)`.

12. **`--so-relatorio` abre o banco em somente leitura.**
    - `persistencia.abrir_conexao` ganha o parâmetro opcional
      **keyword-only** `somente_leitura: bool = False`. O default
      preserva o comportamento atual (leitura/escrita, cria o diretório
      pai). Com `somente_leitura=True`: abre com
      `duckdb.connect(str(caminho), read_only=True)`, **não** cria
      diretório pai nem arquivo (arquivo inexistente → erro nativo do
      DuckDB) e continua não chamando `criar_tabela`. Assinatura em
      `specs/persistencia.md`.
    - `_so_relatorio` passa a chamar
      `abrir_conexao(args.banco, somente_leitura=True)`. A checagem
      "banco não encontrado → exit `4`" continua **antes** da abertura
      (dá o log claro `banco não encontrado: <caminho>` em vez de um erro
      de driver). O fluxo normal (com `executar`) não muda: continua
      abrindo em leitura/escrita e o relatório reaproveita essa conexão.
    - Motivo: tornar a regra "`--so-relatorio` nunca escreve no banco"
      garantida pelo próprio DuckDB, e não só pela ausência de `INSERT`.
      Em modo leitura/escrita o DuckDB pode reaplicar o WAL e fazer
      checkpoint ao abrir ou fechar, alterando o arquivo mesmo sem
      nenhuma gravação nossa.
    - Preferido a chamar `duckdb.connect(..., read_only=True)` direto em
      `__main__`: a abertura de conexão continua num só lugar
      (`persistencia`) e o `__main__` não ganha conhecimento de opções do
      driver.
    - `somente_leitura=True` com `":memory:"` não é suportado (o DuckDB
      recusa); não há tratamento nem teste, porque `_so_relatorio` nunca
      chega a esse ponto (`:memory:` cai em "banco não encontrado").
    - **Não verificado:** o comportamento do DuckDB em `read_only` quando
      o arquivo tem um `.wal` pendente (ex.: execução anterior
      encerrada à força). Se a abertura falhar, o resultado é exit `4`
      com `ERROR` no log; rodar a CLI normal (leitura/escrita) uma vez
      reaplica o WAL e destrava o `--so-relatorio`.
13. **`gravar_relatorio` limpa o temporário em qualquer interrupção e
    sincroniza o conteúdo com o disco antes de trocar.**
    - O `except` que remove o `.tmp` passa de `Exception` para
      **`BaseException`**: `KeyboardInterrupt` (Ctrl+C) e `SystemExit`
      também removem o temporário. A sequência é sempre: remover o
      temporário (melhor esforço) → relançar (`raise`) a exceção original.
      O `caminho` anterior nunca é tocado.
    - Antes do `os.replace`, depois de escrever: `arquivo.flush()` e
      `os.fsync(arquivo.fileno())`, ainda dentro do `with` que abre o
      temporário (o arquivo só é fechado antes do `os.replace`, requisito
      do Windows). Motivo: sem o `fsync`, uma queda de energia logo após o
      `os.replace` pode deixar o diretório apontando para um arquivo novo
      ainda vazio, perdendo o relatório anterior e o novo.
    - Falha ao remover o temporário (`OSError` no `unlink`) é suprimida:
      nunca mascara a exceção original.
    - Fora do relatório, `_gerar_relatorio` continua com `except
      Exception`: um Ctrl+C **não** é convertido em exit `4`, aborta a CLI
      (o `finally` de `main` fecha client e conexão; os dados já estão
      commitados por série).
    - Não é feito `fsync` do diretório pai (não portável no Windows). Com
      isso, numa queda de energia imediatamente após o `os.replace` o
      sistema pode ainda mostrar o relatório **anterior**, íntegro; o que
      não pode mais acontecer é aparecer um arquivo vazio ou truncado.
14. **A mensagem de sucesso no stdout não pode derrubar a CLI.**
    - O `print()` da linha em branco e o `print("Relatório gravado em:
      <caminho>")` ficam num `try/except (OSError, UnicodeError)` **próprio**,
      separado do `try` que gera e grava o relatório.
    - **Efeito decidido:** se o arquivo **já foi gravado** e só o `print`
      falhou (`UnicodeEncodeError` num console com codepage que não
      representa o caminho, `OSError` por stdout fechado ou pipe
      quebrado), registra `logger.warning` (stderr, logger
      `indicadores.pipeline`, texto `relatório gravado em <caminho>, mas
      não foi possível exibir a mensagem de sucesso: <erro>`),
      `_gerar_relatorio` **devolve `True`** e o exit **não muda** (`0`
      continua `0`, `1` continua `1`). Só conta como falha do relatório
      (`False`, exit `4` quando o resto daria `0`) se o arquivo **não**
      foi gravado.
    - Motivos: (a) o relatório existe e está íntegro, então `4`
      ("relatório não pôde ser gerado ou gravado") seria falso e o
      Agendador mostraria uma falha que não houve; (b) deixar a exceção
      escapar viraria traceback e exit `1` do interpretador, e o `1` faz o
      wrapper `executar_diario.ps1` repetir **todo** o pipeline (rebuscar
      a API) por um problema que é só de exibição; (c) o aviso em `WARNING`
      aparece mesmo sem `-v` e fica no log mensal do wrapper.
    - Escopo: só a linha em branco e a linha `Relatório gravado em:`.
      O `print` do resumo (`formatar_resumo`, passo 7.1 de `main`) **não**
      muda nesta decisão (ver "Limitações conhecidas").
    - O `logger.info("relatório gravado ...")` continua sendo emitido
      **antes** dos `print`, para que o log registre o sucesso mesmo se a
      exibição falhar.
15. **Tooltip em telas de toque.**
    - Os `<svg>` dos gráficos recebem `touch-action: pan-y` **somente
      dentro de `@media (min-width: 560px)`** (a regra do `svg` dos
      gráficos, que já define `width: 100%; min-width: 520px; height:
      auto`, fica sem `touch-action`; o `touch-action` vai numa regra
      própria dentro da `@media`). Em telas de 560px ou mais: rolar a
      página na vertical continua funcionando; o gesto horizontal fica com
      o tooltip (Pointer Events passam a chegar ao script em vez de o
      navegador assumir o gesto). Abaixo de 560px o `<svg>` **não** declara
      `touch-action`: o gesto horizontal sobre o gráfico continua rolando o
      contêiner `overflow-x: auto`.
    - O JS passa a tratar também **`pointercancel`**, escondendo o tooltip,
      o crosshair e o marcador (mesmo tratamento de `pointerleave`), **em
      qualquer largura**. Motivo: quando o navegador assume um gesto (por
      exemplo a rolagem vertical que o `pan-y` permite, ou a rolagem
      horizontal do contêiner abaixo de 560px), ele dispara `pointercancel`
      e não `pointerleave`; sem o tratador o tooltip ficaria preso na tela.
    - **Motivo da condição `min-width: 560px`:** abaixo disso o SVG
      (`min-width: 520px`) é mais largo que o contêiner `overflow-x: auto`
      e a rolagem horizontal do contêiner é o único jeito de alcançar o
      lado direito do gráfico (inclusive o último ponto) por toque. Com
      `pan-y` aplicado em qualquer largura, o gesto horizontal sobre o
      gráfico iria para o tooltip e não rolaria o contêiner.
    - **Alternativa descartada:** `touch-action: pan-y` incondicional nos
      `<svg>` (em qualquer largura). Descartada porque, em telas abaixo de
      ~560px, deixaria a parte direita do gráfico (inclusive o último
      ponto) inalcançável por arrastar sobre o gráfico (a tabela
      correspondente continuaria acessível, mas o gráfico perderia
      alcance). Decisão do orquestrador, adotando a alternativa que esta
      decisão registrava antes como "a confirmar".
    - O comportamento real em dispositivo de toque **é verificação
      manual** (não há navegador na suíte): ver "Verificação manual". Os
      testes cobrem só o texto de CSS e JS gerado.
    - O limite de 560px é aproximado e **não foi medido** contra a largura
      real do contêiner (que depende de margens e padding); ver "Limitações
      conhecidas".
16. **`_nome_da_serie` usa o nome da linha de maior data.** O nome da série
    exibido no rodapé (e usado nos `<title>` dos gráficos) vem da linha de
    **maior `data`** do código, não da primeira linha encontrada: o
    resultado independe da ordem das linhas de `dados` (que já é declarada
    irrelevante em "Contrato de entrada"). Considera só linhas com nome
    não vazio (coluna `serie` ausente, `NaN`, `None` ou só espaços são
    ignoradas); sem nenhuma, usa o nome padrão. Empate de `data` (só
    possível em `DataFrame` sintético, no banco `(codigo, data)` é chave
    primária): menor nome em ordem alfabética, para ser determinístico.
    Motivo: se o BCB/`SERIES` renomear a série, o rodapé mostra o nome
    vigente, e `gerar_html` fica realmente determinístico com entrada
    embaralhada. Sem mudança observável quando todas as linhas do código
    têm o mesmo nome (o caso real hoje).
17. **Largura das barras do IPCA mensal.** Sendo `escala` a largura, em
    unidades do `viewBox`, que cada índice de mês ocupa no eixo X
    (`largura_da_área_de_plotagem / quantidade_de_posições_de_mês`):
    `largura_da_barra = min(max(escala - ESPACO_ENTRE_BARRAS, 1),
    LARGURA_MAXIMA_BARRA)`, com `ESPACO_ENTRE_BARRAS = 2` e
    `LARGURA_MAXIMA_BARRA = 24`. A barra fica **centrada** na posição do
    seu mês. Motivos: o `max(..., 1)` evita largura zero ou negativa se
    uma série tiver muitos meses (com a janela de 5 anos são no máximo 61
    posições, `escala` ~ 9,4, então o piso só atua em dados fora do
    previsto); o teto de 24 evita que 1 mês isolado vire uma barra de
    ~572px (a área de plotagem inteira). Observável: com `escala` até 26
    nada muda (espaço de 2px); acima disso as barras param de crescer em
    24px e o espaço entre elas fica `escala - 24` (> 2px). O raio dos
    cantos continua `min(4, largura/2, altura)`. Valor 24 escolhido por
    mim (o usuário deu "ex.: 24px"); ajustável por constante.

## Localização
```
src/indicadores/relatorio.py     # núcleo puro (gerar_html) + escrita atômica
src/indicadores/__main__.py      # integração: flags, leitura do banco, exit codes
```
Testes em `tests/test_relatorio.py` (módulo) e `tests/test_main.py`
(integração com a CLI); um teste do wrapper em `tests/test_scripts.py`;
um teste de `abrir_conexao` em `tests/test_persistencia.py` (caso 58,
decisão 12).
Nenhum teste acessa a internet; todos usam `DataFrame` sintético e data
de geração fixa, exceto os de CLI (`tmp_path` + `httpx.MockTransport`,
como em `tests/test_main.py`).

## API pública de `relatorio.py`
```python
RELATORIO_PADRAO: Path = Path("relatorio.html")
JANELA_ANOS: int = 5
ESPACO_ENTRE_BARRAS: int = 2        # decisão 17
LARGURA_MAXIMA_BARRA: int = 24      # decisão 17

def gerar_html(dados: pd.DataFrame, gerado_em: datetime) -> str: ...
def gravar_relatorio(html: str, caminho: Path = RELATORIO_PADRAO) -> None: ...

# Formatação de exibição (puras, públicas para serem testadas direto)
def formatar_numero(valor: float, casas: int) -> str: ...      # "1.234,50"
def formatar_percentual(valor: float, casas: int = 2) -> str:  # "14,25%"
def formatar_moeda(valor: float, casas: int = 4) -> str:       # "R$ 5,3784"
def formatar_data(data) -> str:                                # "31/08/2026"
def formatar_mes(data) -> str:                                 # "ago/2026"
def formatar_variacao(valor: float, unidade: str) -> str:      # "▲ alta de +0,25 p.p."
def calcular_marcas_eixo(minimo: float, maximo: float) -> list[float]: ...

# Auxiliares privadas, testadas direto por terem regra própria (decisões 16 e 17)
def _nome_da_serie(dados: pd.DataFrame, codigo: int) -> str: ...
def _calcular_largura_barra(escala: float) -> float: ...
```
`gerar_html` e as funções de formatação **não** fazem I/O. Nenhuma
exceção própria é criada: `ErroAnalise` (colunas faltando) propaga como
está — quem chama (`__main__`) trata qualquer exceção como "falha ao
gerar o relatório" (ver exit code `4`).

### Contrato de entrada de `gerar_html`
- `dados`: mesmo contrato de `analise.py` — precisa ter as colunas
  `codigo`, `data`, `valor`; `serie`/`atualizado_em` são opcionais.
  Aceita a saída de `persistencia.ler` ou de `limpeza.limpar`. Dtypes
  não são revalidados (mesma decisão de `analise.py`).
- `gerado_em`: `datetime` **naive**, interpretado como horário de
  Brasília (mesma convenção de `atualizado_em`). Exibido como
  `dd/mm/aaaa HH:MM`. Nunca lê o relógio dentro de `gerar_html`.
- Ordem das linhas de `dados` é irrelevante; `dados` **não é alterado**.
- Reutilização, sem duplicar cálculo: Selic → `analise.mudancas_selic`;
  IPCA acumulado → `analise.ipca_acumulado_12m`; PTAX mensal →
  `analise.ptax_mensal`. Os códigos vêm das constantes de `analise`
  (`CODIGO_SELIC_META`, etc.), sem números mágicos. O que o relatório
  calcula por conta própria é só **apresentação**: selecionar a última
  linha de uma série, a janela de 5 anos, e a variação percentual do
  card PTAX (que não existe em `analise`) — ver "Cards".
- Nome da série no rodapé: `_nome_da_serie(dados, codigo)` — coluna
  `serie` de `dados`, se existir e não for vazia para aquele código,
  **tomada da linha de maior `data`** (decisão 16; independe da ordem das
  linhas); senão o nome padrão (`selic_meta`, `ipca_mensal`,
  `dolar_ptax_venda`). Por isso o nome também passa por `html.escape`.

### Janela de 5 anos
Gráficos e tabela de mudanças da Selic usam `JANELA_ANOS = 5` contados a
partir da **última data de cada série** (não de "hoje"; mantém o
relatório determinístico e coerente quando o banco está desatualizado):
linhas com `data >= ultima_data - pd.DateOffset(years=5)`. Motivo: o
pipeline busca 5 anos por execução mas o upsert nunca apaga, então o
banco pode acumular mais que 5 anos com o tempo; o relatório não deve
crescer junto. Os **cards** usam a série inteira (último valor); as
tabelas de IPCA e PTAX têm janela própria (últimos 12 meses).

## Conteúdo

### Cabeçalho
- `<h1>`: `Indicadores Econômicos — Banco Central do Brasil`.
- Data/hora de geração: `Gerado em 09/10/2026 14:30 (horário de Brasília)`.
- Fonte: `SGS/BCB`.
- Período coberto: `de dd/mm/aaaa a dd/mm/aaaa` — menor e maior `data`
  entre as linhas **exibidas** (dentro da janela de cada série) das
  séries presentes. Sem nenhuma série: `Período: sem dados`.

### Resumo executivo — 4 cards, nesta ordem
Cada card: título, valor principal grande, linha(s) de contexto. Grid
responsivo (`repeat(auto-fit, minmax(...))`) que quebra em telas
estreitas.

1. **Selic meta** — valor vigente (último valor da série 432, ex.
   `14,75%`, rotulado "a.a."), mais "última mudança em dd/mm/aaaa:
   ▼ queda de −0,25 p.p. (de 15,00% para 14,75%)", usando a **última
   linha** de `mudancas_selic`. Sem nenhuma mudança na janela:
   `Sem mudanças no período`.
2. **IPCA acumulado 12m** — último `acumulado_12m` de
   `ipca_acumulado_12m` (ex. `4,50%`), mês de referência (`ago/2026`) e
   variação **em p.p.** contra a linha imediatamente anterior da mesma
   saída, **só se for o mês civil anterior**; se a linha anterior não for
   o mês anterior (lacuna) ou não existir: `variação indisponível`.
3. **IPCA mensal** — último valor da série 433 (não exige janela de 12
   meses), ex. `−0,11%` e `ago/2026`.
4. **PTAX venda** — último valor da série 1 (`R$ 5,3784`), data
   (`31/08/2026`) e duas variações **percentuais**:
   - vs **dia anterior com dado** (linha imediatamente anterior da
     série; a PTAX não tem cotação em feriado, então é "dia útil
     anterior" na prática);
   - vs **fechamento do mês anterior** (`fechamento` de `ptax_mensal`
     para o mês civil imediatamente anterior ao do último dado; se esse
     mês não existe em `ptax_mensal`: `variação indisponível`).
   Fórmula: `(valor / referencia - 1) * 100`; referência `0`/não finita →
   `variação indisponível`.

Regras de **variação** (todas as variações dos cards e da tabela da
Selic):
- Nunca só cor: sempre **seta + sinal + texto**. Formato
  `▲ alta de +0,25 p.p.`, `▼ queda de −0,25 p.p.`, `▶ estável (0,00 p.p.)`
  (unidade `p.p.` ou `%`). O sinal negativo é o caractere `−` (U+2212),
  o positivo é `+`.
- A classificação (alta/queda/estável) é feita pelo valor **já
  arredondado para as casas exibidas**: `+0,004` exibido com 2 casas é
  `estável`, nunca `▲ +0,00`.
- Texto e seta usam a **cor de texto** (`--texto`), não a cor da série.

### Gráficos — 4, um por indicador, cada um com seu próprio eixo
**Nunca eixo duplo.** Uma série por gráfico, **sem caixa de legenda**: o
`<h3>` do gráfico nomeia a série e a unidade.

| # | Título | Tipo | Fonte |
|---|---|---|---|
| a | Selic meta (% a.a.) | linha em degrau | série 432, janela de 5 anos |
| b1 | IPCA acumulado em 12 meses (%) | linha | `ipca_acumulado_12m`, janela de 5 anos |
| b2 | IPCA mensal (%) | barras | série 433, janela de 5 anos |
| c | Dólar PTAX venda (R$) | linha | série 1, janela de 5 anos |

Regras comuns:
- **SVG inline** com `viewBox="0 0 720 320"`, `role="img"`,
  `aria-labelledby` apontando para `<title>` (nome da série) e `<desc>`
  (resumo textual: período, último valor). Sem atributo `xmlns` (não é
  necessário em SVG inline no HTML5 e evita qualquer `http://` no
  arquivo).
- **Largura mínima**: `svg { width: 100%; min-width: 520px; height: auto }`
  dentro de um contêiner com `overflow-x: auto`; o `touch-action` **não**
  fica nessa regra: `touch-action: pan-y` vem numa regra própria para os
  `svg` dos gráficos dentro de `@media (min-width: 560px)` (decisão 15).
  Motivo da largura: encolher o `viewBox` em telas de 360px deixaria o
  texto do eixo com ~7px, ilegível; em tela estreita o gráfico rola na
  horizontal, e abaixo de 560px o gesto horizontal sobre o gráfico segue
  rolando o contêiner (sem `touch-action`).
- **Coordenadas SVG** são escritas com ponto decimal e 1 casa (formato
  exigido pelo SVG); é o **único** lugar sem vírgula decimal — não é texto
  exibido.
- **Eixo Y**: 4 a 6 marcas "redondas" (`calcular_marcas_eixo`, abaixo),
  grid horizontal discreto (`--grid`), rótulos das marcas em `--eixo`
  (`#52514e` no tema claro, `#898781` no escuro — decisões 7 e 8),
  com tantas casas decimais quanto o passo exigir (0 a 4), em pt-BR. O
  eixo cobre da primeira à última marca. Linhas **não** são forçadas a
  começar em zero (a PTAX e a Selic ficariam planas); **barras** sempre
  incluem o zero.
- **Eixo X**: rótulos de ano (`2022`, `2023`...) no 1º de janeiro de cada
  ano dentro da janela; se a janela cobrir menos de 2 anos, rótulos
  `mmm/aaaa` em no máximo 6 posições. Nunca sobrepostos.
- **Rótulo direto** só no **último ponto**: ponto marcado + texto com o
  valor completo (`14,75%`, `4,50%`, `−0,11%`, `R$ 5,3784`) em `--texto`
  (texto nunca na cor da série). Sem rótulo em nenhum outro ponto.
- **Linhas**: 2px (`stroke-width: 2`), cor `--serie`, sem preenchimento.
- **Selic em degrau**: o `<path>` só tem segmentos horizontais e
  verticais (`H`/`V`), com vértices apenas nas datas em que o valor muda
  mais o último ponto — o valor vale do dia da mudança até o dia anterior à
  próxima. Não gera um vértice por dia (1837 linhas) para o desenho.
- **Barras do IPCA mensal**: uma barra por mês da janela; positivas em
  `--serie`, negativas em `--negativo`; linha zero em `--base`; barras
  negativas desenhadas **abaixo** da linha zero. Espaço de **2px**
  (`ESPACO_ENTRE_BARRAS`) entre barras; **largura** da barra =
  `min(max(escala - 2, 1), 24)` (`_calcular_largura_barra`, decisão 17),
  centrada na posição do mês: com `escala` até 26 o espaço entre barras é
  exatamente 2px; acima disso as barras param em 24px (`LARGURA_MAXIMA_BARRA`)
  e o espaço é maior, de modo que 1 mês isolado nunca vira uma barra do
  tamanho da área de plotagem. Cantos arredondados de **4px só na ponta do
  dado** (topo da positiva, base da negativa; o lado que encosta na linha
  zero é reto), com raio limitado a `min(4, largura/2, altura)`. Barra de
  valor `0` não desenha nada. Por usar cor para distinguir o sinal, a
  informação nunca é só cor: o sinal também é posicional (acima/abaixo do
  zero) e está na tabela de IPCA.
- **Eixo X temporal**: séries diárias (Selic, PTAX) usam escala linear
  por data; séries mensais (IPCA) usam índice de mês (lacunas deixam
  espaço vazio, não são "esticadas").
- **Tooltip** (JS mínimo, ver abaixo) com crosshair, data e valor.
- **Legível sem JS**: com o `<script>` removido, o gráfico continua
  completo (linha/barras, grid, marcas, rótulo final) e o tooltip simplesmente
  não existe; as tabelas abaixo são a "visão em tabela" acessível.
- Gráfico com **menos de 2 pontos** (linhas) ou **0 pontos** (barras):
  no lugar do `<svg>`, `Sem dados suficientes`.

**`calcular_marcas_eixo(minimo, maximo)`** — algoritmo:
1. Passos candidatos: `{1, 2, 2.5, 5} x 10^k`.
2. Escolhe o **menor** passo para o qual as marcas
   `[floor(minimo/passo)*passo, ..., ceil(maximo/passo)*passo]` sejam no
   máximo 6.
3. Intervalo degenerado (`minimo == maximo`): expande para
   `valor ± max(1, abs(valor) * 0.05)` antes de calcular.
4. Cada marca é calculada como `k * passo` e arredondada para eliminar
   ruído de ponto flutuante (`0.30000000000000004` nunca aparece).
Garantias verificáveis: 4 a 6 marcas, todas múltiplas do passo, a
primeira `<= minimo` e a última `>= maximo`, em ordem crescente.

### Tooltip (JS mínimo, inline)
- Um único `<script>` no documento, sem `src`, sem `fetch`,
  `XMLHttpRequest`, `import` nem `eval`.
- Cada `<svg>` de gráfico carrega `data-pontos`: JSON (lista de
  `[x_svg, y_svg, "texto"]`, um item por observação da janela), escapado
  com `html.escape(..., quote=True)`. O texto já vem formatado em pt-BR
  (`31/08/2026 — R$ 5,3784`; mensais: `ago/2026 — −0,11%`; Selic:
  valor vigente naquela data).
- O script usa Pointer Events (funciona com mouse e toque): ao mover o
  ponteiro sobre o gráfico, acha o ponto de `x` mais próximo, move uma
  linha vertical (crosshair) e um marcador, e mostra uma `<div>` de
  tooltip (`hidden` por padrão) com o texto. Ao sair (`pointerleave`) **ou
  quando o navegador cancela o gesto (`pointercancel`, decisão 15, em
  qualquer largura)**, esconde tudo.
- Em telas de toque com 560px ou mais, o CSS dos `<svg>` dos gráficos
  declara `touch-action: pan-y` **apenas dentro de `@media (min-width:
  560px)`** (decisão 15): a rolagem vertical da página segue com o
  navegador (e dispara `pointercancel`, que esconde o tooltip); o gesto
  horizontal chega ao script como `pointermove`. Abaixo de 560px não há
  `touch-action` no `<svg>`: o gesto horizontal rola o contêiner
  `overflow-x: auto` do gráfico (o navegador assume o gesto e dispara
  `pointercancel`, que esconde o tooltip).
- Cobertura: o JS **não** é executado pelo `pytest` (não há navegador);
  os testes verificam só o que o Python gera (ver "Limitações"). O
  comportamento real em toque é **verificação manual**.

### Tabelas — 3, números à direita com `tabular-nums`
Cada tabela tem `<caption>`, `<th scope="col">`, fica dentro de um
contêiner com `overflow-x: auto`, e serve como visão acessível do gráfico
correspondente.

1. **Mudanças da Selic** (janela de 5 anos, **mais recente primeiro**):
   `Data | De | Para | Variação`. Linhas de `mudancas_selic`. Vazia (série
   presente, sem mudanças): `Sem mudanças no período`.
2. **IPCA mensal e acumulado 12m** (últimos 12 meses, mais recente
   primeiro): `Mês | IPCA mensal | Acumulado 12m`. Vem das últimas 12
   linhas de `ipca_acumulado_12m`; portanto só existem meses com janela
   de 12 meses completa (decisão 10; ver "Limitações").
3. **PTAX mensal** (últimos 12 meses, mais recente primeiro):
   `Mês | Média | Fechamento | Mínimo | Máximo | Dias`. Valores de
   `ptax_mensal`, `R$` com 4 casas. A coluna **Dias** (`dias_com_dado`) é
   acréscimo meu ao pedido, pelo motivo já registrado em `specs/analise.md`
   (é a forma documentada de sinalizar mês incompleto); `<caption>` ou
   nota abaixo avisa que o último mês pode estar incompleto.

### Rodapé
- Fonte: `Fonte: SGS — Sistema Gerenciador de Séries Temporais, Banco
  Central do Brasil.` **Sem URL e sem link** (decisão: manter o arquivo
  100% livre de `http://`/`https://` permite um teste simples e estrito de
  "nada externo"; o nome da fonte basta).
- Séries: `432 selic_meta · 433 ipca_mensal · 1 dolar_ptax_venda`.
- Aviso: `Os dados podem ser revisados pelo Banco Central do Brasil após a
  publicação.`

## Dados insuficientes (comportamento especificado)
Regra geral: **nunca quebrar** e nunca omitir uma seção. Onde não há
dados suficientes, a seção continua presente (mesmo `id`, mesmo título) e
mostra `Sem dados suficientes` num `<p class="vazio">`.

| Situação | Efeito |
|---|---|
| `DataFrame` vazio (com as colunas certas) | HTML completo e válido: cabeçalho, 4 cards, 4 gráficos e 3 tabelas, todos com `Sem dados suficientes`; período `sem dados`; nenhum `<svg>`; rodapé normal. |
| Colunas `codigo`/`data`/`valor` faltando | `ErroAnalise` propaga (não é tratada aqui). |
| Série 432 ausente | Card Selic, gráfico (a) e tabela de mudanças: `Sem dados suficientes`. Demais seções normais. |
| Série 433 ausente | Cards IPCA 12m e IPCA mensal, gráficos b1 e b2 e tabela de IPCA: `Sem dados suficientes`. |
| Série 1 ausente | Card PTAX, gráfico (c) e tabela de PTAX: `Sem dados suficientes`. |
| 433 com menos de 12 meses (ou sem 12 consecutivos) | `ipca_acumulado_12m` vazio: card IPCA 12m, gráfico b1 e tabela de IPCA: `Sem dados suficientes`; card IPCA mensal e gráfico b2 **funcionam** (usam a série bruta). |
| 432 com 1 observação | Card mostra o valor e `Sem mudanças no período`; gráfico (a): `Sem dados suficientes` (precisa de 2 pontos). |
| 1 com 1 observação | Card mostra valor e data; as duas variações: `variação indisponível`; gráfico: `Sem dados suficientes`. |
| Lacuna entre o último e o penúltimo mês (IPCA 12m) ou mês anterior ausente em `ptax_mensal` | Só a variação correspondente vira `variação indisponível`; o resto do card é normal. |
| Valor não finito (`NaN`/`inf`) | Não esperado (coluna `valor` é `NOT NULL` no banco); defensivamente, `formatar_numero` devolve `—` e o gráfico ignora o ponto. |

## Formatação de exibição (pt-BR) — só exibição
Os dados **não** são alterados: continuam datas ISO e `float` (regra do
`CLAUDE.md`). Formatação manual, **sem `locale`** (o resultado não pode
depender do SO):
- `formatar_numero(valor, casas)`: arredonda, usa `.` como separador de
  milhar e `,` como decimal (`1234.5, 2` → `1.234,50`). Negativo usa `−`
  (U+2212). Resultado que arredonda para zero **não** tem sinal
  (`-0.001, 2` → `0,00`, nunca `−0,00`). Não finito → `—`.
- `formatar_percentual`: `formatar_numero` + `%` (2 casas por padrão):
  `14,25%`.
- `formatar_moeda`: `R$ ` + `formatar_numero` com 4 casas por padrão
  (PTAX): `R$ 5,3784`.
- `formatar_data`: `dd/mm/aaaa`. `formatar_mes`: abreviação minúscula de 3
  letras de uma tabela fixa em Python (`jan fev mar abr mai jun jul ago
  set out nov dez`) + `/` + ano: `ago/2026`.
- Variação: ver "Regras de variação".

## Design
Fonte: `system-ui, -apple-system, "Segoe UI", sans-serif`. Tudo em
**variáveis CSS** em `:root` (claro) e redefinidas em
`@media (prefers-color-scheme: dark)`. `color-scheme: light dark`.

| Variável | Claro | Escuro |
|---|---|---|
| `--superficie` (cards, gráficos, tabelas) | `#fcfcfb` | `#1a1a19` |
| `--pagina` | `#f9f9f7` | `#0d0d0d` |
| `--texto` | `#0b0b0b` | `#ffffff` |
| `--texto-2` (secundário) | `#52514e` | `#c3c2b7` |
| `--eixo` (rótulos das marcas dos eixos) | `#52514e` (decisão 7) | `#898781` (decisão 8) |
| `--grid` | `#e1e0d9` | `#2c2c2a` |
| `--base` (linha de base/zero) | `#c3c2b7` | `#383835` |
| `--serie` | `#2a78d6` | `#3987e5` |
| `--negativo` | `#e34948` | `#e66767` |

Regras:
- **Nenhuma cor literal fora desses dois blocos de variáveis**: regras
  CSS e atributos SVG usam `var(--...)`; sem `fill="#..."`, `stroke="#..."`
  nem `style="color:..."` inline.
- **Texto sempre em `--texto` ou `--texto-2`** (e `--eixo` nos rótulos de
  marcas), **nunca** em `--serie`/`--negativo`. Variação positiva/negativa
  nos cards: texto e seta em `--texto`.
- `--eixo` é usada **só** nos rótulos das marcas; linhas de grid usam
  `--grid` e a linha de base/zero usa `--base` (inalteradas pela decisão 7).
- Cards e gráficos com fundo `--superficie` e borda de 1px `--grid`; página
  `--pagina`. Cards em grid `repeat(auto-fit, minmax(220px, 1fr))`.
- Tabelas: cabeçalho com borda inferior `--base`, linhas separadas por
  `--grid`; células numéricas com `text-align: right` e
  `font-variant-numeric: tabular-nums`.
- `<html lang="pt-BR">`, `<meta charset="utf-8">`, `<meta name="viewport"
  content="width=device-width, initial-scale=1">`.

## Escrita do arquivo
```python
def gravar_relatorio(html: str, caminho: Path = RELATORIO_PADRAO) -> None:
```
1. Cria o diretório pai se não existir.
2. Escreve `html` em **UTF-8**, sem conversão de quebra de linha
   (`newline="\n"`), num temporário **no mesmo diretório** de `caminho`
   (nome fixo `<nome>.tmp`, ex. `relatorio.html.tmp`). Ainda com o
   arquivo aberto, faz `flush()` e `os.fsync(arquivo.fileno())` (decisão
   13), e só então o fecha.
3. `os.replace(temporario, caminho)` — atômico no mesmo volume (Windows
   e POSIX).
4. **Qualquer interrupção em 1–3, inclusive `KeyboardInterrupt` e
   `SystemExit`** (`except BaseException`, decisão 13): remove o
   temporário (melhor esforço; um `OSError` ao remover é suprimido e nunca
   mascara a exceção original), **não toca** no `caminho` anterior e
   **propaga** a exceção original (`raise`).
Motivo do nome fixo (e não aleatório): se o processo for encerrado à
força (logoff, `0xC000013A`, ver `specs/agendamento.md`) entre os passos
2 e 3, sobra no máximo um arquivo conhecido, que o próximo ciclo
sobrescreve e que o `.gitignore` cobre (decisão 9). Execuções simultâneas
já são serializadas pelo bloqueio do arquivo DuckDB.
O `fsync` do passo 2 evita que, numa queda de energia logo após o
`os.replace`, o destino fique com um arquivo vazio; o `fsync` do diretório
pai **não** é feito (não portável no Windows).

## Integração na CLI

### Decisão
**O relatório é regenerado, a partir do banco, ao final de cada execução
normal.** Como o agendamento (16:00) chama `python -m indicadores`, o
relatório fica atualizado sem mexer em `executar_diario.ps1`. Motivos de
ler **do banco** (e não do `DataFrame` recém-limpo): o relatório precisa da
série inteira, não só da janela buscada; e o mesmo caminho serve ao
`--so-relatorio`. A leitura acontece com a **mesma conexão**, depois do
`executar` e antes de fechar, na cláusula `else` do `try` existente de
`main` (que já roda antes do `finally`), então não abre o arquivo duas
vezes.

### Argumentos novos
```
python -m indicadores [...existentes...] [--relatorio CAMINHO]
                       [--sem-relatorio | --so-relatorio]
```
- `--relatorio CAMINHO` (`type=Path`, `metavar="CAMINHO"`, default
  `RELATORIO_PADRAO` = `relatorio.html`, relativo ao diretório de
  trabalho — o wrapper roda com `WorkingDirectory` = raiz do repositório,
  então cai na raiz; mesma semântica de `--banco`). Existe principalmente
  para os testes não escreverem na raiz real.
- `--sem-relatorio` (`store_true`): não gera o relatório. `--relatorio`
  junto com ele é aceito e **ignorado** (documentado no `--help`).
- `--so-relatorio` (`store_true`): **só** regenera o relatório a partir do
  banco existente. Não cria client HTTP, não chama `executar`, não acessa
  a rede, não grava nada no banco — e abre o banco em **somente leitura**
  (`abrir_conexao(..., somente_leitura=True)`, decisão 12).
- `--sem-relatorio` e `--so-relatorio` são **mutuamente exclusivos**
  (`add_mutually_exclusive_group`) → `argparse` recusa com exit `2`.
- `--so-relatorio` com `--series` ou `--data-referencia` → `parser.error`
  (exit `2`): esses argumentos só fazem sentido para a extração, e
  ignorá-los em silêncio esconderia um engano.
- Decisão: **flag, não subcomando** — a CLI atual não tem subcomandos e
  os argumentos existentes continuam valendo sem mudança.

### Exit codes (acrescenta o `4`; o significado de `0`–`3` não muda)
| Código | Situação |
|---|---|
| `0` | Como antes; e, se o relatório foi pedido, ele foi gravado. |
| `1` | Como antes (série ignorada). **Prevalece** sobre falha do relatório. |
| `2` | Como antes (uso inválido), agora incluindo as combinações de flags acima. |
| `3` | Como antes (erro inesperado no pipeline). Nesse caso o relatório **não é tentado**. |
| `4` | **Novo.** Os dados foram gravados normalmente (a execução seria `0`), mas o relatório **não pôde ser gerado ou gravado**; ou, em `--so-relatorio`, o relatório não pôde ser gerado. Falha só ao **exibir** a mensagem de sucesso, com o arquivo já gravado, **não** é `4` (decisão 14). |

Decisões, cada uma com motivo:
1. **Falha do relatório vira exit `4`, não `0`.** Com `0` a falha ficaria
   invisível para o Agendador ("Resultado da última execução" = 0) e
   só apareceria se alguém lesse o log. Com `1` ou `3`, o significado
   existente seria diluído, e o `1` dispararia no wrapper a nova
   tentativa de **todo o pipeline** (rebuscar a API) por um problema que
   não é de rede.
2. **`4` nunca dispara nova tentativa**: o wrapper só repete no `1`
   (`$exitFinal -eq 1`) e repassa qualquer outro inteiro como veio; o
   `4` já está abaixo da faixa `>= 10` reservada ao wrapper. **Nenhuma
   mudança em `executar_diario.ps1` é necessária** (conferido no script).
3. **Falha do relatório não desfaz os dados.** Os dados já estão
   commitados (uma transação por série) quando o relatório é tentado; o
   relatório roda depois, em `try/except` próprio, e o arquivo anterior
   fica intacto (escrita atômica). Reexecutar é seguro (upsert).
4. **Precedência**: `3` (não tenta relatório) > `1` (relatório tentado;
   se falhar, só o log registra — decisão 11) > `4` > `0`. Se houve série
   ignorada **e** o relatório falhou, o exit continua `1`; o `ERROR` no
   log mostra a segunda falha. Motivo: `1` é a causa mais grave e a nova
   tentativa do wrapper, que ele provoca, também regenera o relatório.
5. **Erro `3` não tenta relatório**: o estado do banco depois de um erro
   inesperado é incerto; melhor manter o relatório anterior (consistente)
   do que gerar um novo a partir de um estado duvidoso.
6. **Execução com série ignorada (`1`) gera relatório** normalmente: o
   banco guarda os dados das execuções anteriores, então o relatório
   continua útil.
7. Os testes do wrapper e da CLI **nunca** devem usar o `relatorio.html`
   real (ver "Mudanças em outros documentos e testes existentes").

### Log e saída padrão
- Sucesso: linha em **stdout**, depois do resumo e separada por linha em
  branco: `Relatório gravado em: <caminho>`; mais `logger.info`
  (visível com `-v`), emitido **antes** dos `print`. A linha em branco e a
  mensagem são impressas dentro de um `try/except (OSError,
  UnicodeError)` próprio: se o arquivo já foi gravado e só a impressão
  falha, `logger.warning` (stderr, logger `indicadores.pipeline`) e o
  resultado continua "relatório gravado" — o exit não muda (decisão 14).
- Falha: `logger.exception("falha ao gerar o relatório; os dados foram
  gravados normalmente")` (nível `ERROR`, logger `indicadores.pipeline`,
  stderr — nível que aparece mesmo sem `-v`, portanto fica no log mensal
  do wrapper). Nada em stdout.
- Em `--so-relatorio`, a mensagem de falha muda para `falha ao gerar o
  relatório` (sem a frase sobre dados). O resumo do pipeline não existe
  nesse modo.

### Passo a passo (acréscimos a `main`)
1. `_parse_args`: novos argumentos e validações acima.
2. Se `--so-relatorio`: `_so_relatorio(args)` e retorna.
   - Se `args.banco` **não existe** como arquivo: log `ERROR`
     (`banco não encontrado: <caminho>`), exit `4`, **sem criar** o arquivo
     nem o diretório (`abrir_conexao` em leitura/escrita criaria ambos; por
     isso a checagem vem antes, mesmo com `somente_leitura=True`).
     `--banco :memory:` cai aqui (nada a ler).
   - Caso contrário: `abrir_conexao(args.banco, somente_leitura=True)`
     (decisão 12), `_gerar_relatorio`, `finally` fecha a conexão. Falha ao
     abrir (arquivo bloqueado por outra execução) → `ERROR` + exit `4`.
     Não chama `criar_tabela`: banco sem a tabela `indicadores` faz `ler`
     falhar → exit `4` (não é tratado como "vazio").
3. Fluxo normal inalterado até o `else` do `try`: imprime o resumo,
   calcula o exit (`1` se `series_ignoradas`, senão `0`) e, **se não for
   `--sem-relatorio`**, chama `_gerar_relatorio(conexao, args.relatorio)`;
   se devolver `False` e o exit atual for `0`, vira `4`.
4. `_gerar_relatorio(conexao, caminho) -> bool`: `try`: `ler(conexao)` →
   `gerar_html(dados, gerado_em=<agora de Brasília, naive, via
   FUSO_BRASILIA>)` → `gravar_relatorio(html, caminho)`; `except
   Exception`: loga `ERROR` e devolve `False`. Se o arquivo foi gravado:
   `logger.info`, depois — num segundo `try/except (OSError,
   UnicodeError)`, fora do primeiro — imprime a linha em branco e a linha
   de sucesso; se a impressão falhar, `logger.warning`; em ambos os casos
   devolve `True` (decisão 14). Nunca propaga `Exception` (a exceção em
   `else` não passaria pelo `except` do `try` de `main`, então o
   `try/except` próprio é obrigatório); `KeyboardInterrupt`/`SystemExit`
   não são capturados (decisão 13).
5. `finally` de `main` continua fechando client e conexão em todos os
   caminhos.

## Mudanças em outros documentos e testes existentes
**Aplicadas em 09/10/2026** (specs, a pedido do usuário, depois da
aprovação desta spec):
- `specs/pipeline.md`: "Argumentos" (`--relatorio`, `--sem-relatorio`,
  `--so-relatorio` e regras de exclusão/erro de uso, apontando para esta
  spec); "Passo a passo de `main`" (geração do relatório no `else`, com a
  precedência); "Exit codes" (linha `4`; regra 5 e critério de aceite 7
  passam a mencionar o `4`); "Fora de escopo" (exportação para outros
  formatos continua fora para os **dados**; o relatório HTML é a exceção,
  descrita aqui).
- `specs/agendamento.md`: menções e tabelas de exit codes passam de
  `0`–`3` para `0`–`4` repassados; "Nunca há nova tentativa" passa a
  listar também o `4`. O script e o wrapper não mudam.
- `specs/analise.md`: "Fora de escopo — Gráficos/visualização" continua
  verdadeiro para `analise.py` (que segue pura); o relatório é outro
  módulo. Sem mudança.

**Aplicadas depois, na revisão de código de 09/10/2026** (decisões 12 a
17):
- `specs/pipeline.md`: "Argumentos" (`--so-relatorio` abre o banco em
  somente leitura); "Passo a passo de `main`" (passos 2a e 7.3: leitura
  somente, e a falha só do `print` de sucesso não altera o exit);
  "Exit codes" (linha `4`); referência aos casos de teste novos.
- `specs/persistencia.md`: assinatura e docstring de `abrir_conexao`
  (parâmetro `somente_leitura`), regra de negócio, critério de aceite e
  caso de teste 21 (que aponta para o caso 58 desta spec).
- `specs/agendamento.md`: **sem mudança** (o wrapper continua repassando
  qualquer exit code; nenhum exit novo).

**Ainda não aplicadas** (fora do escopo das specs; a edição fica para o
fluxo normal — implementer / test-writer / doc-writer):
- `HANDOFF.md`: tabela de exit codes de `0`–`3` para `0`–`4`.
- `.gitignore`: acrescentar `relatorio.html` e `relatorio.html.tmp`
  (decisão 9).
- `src/indicadores/persistencia.py` (`abrir_conexao`), `src/indicadores/
  __main__.py` e `src/indicadores/relatorio.py`: implementar as decisões 12
  a 17 (implementer).
- **Testes existentes que passam a gerar relatório sem querer:** todo
  teste de `main` que roda o fluxo normal (`tests/test_main.py`) e todo
  teste de `tests/test_scripts.py` que roda o pipeline de verdade (os do
  proxy local, que terminam em exit `1` e agora também geram relatório)
  precisam receber `--sem-relatorio` ou `--relatorio <tmp_path>/r.html`.
  Salvaguarda adicional: `autouse` fixture em `tests/test_main.py` com
  `monkeypatch.chdir(tmp_path)`, para que um esquecimento nunca escreva
  `relatorio.html` na raiz do repositório. Os testes `--help` não mudam.
- `README.md`: documentar flags, `relatorio.html` e exit `4` (doc-writer).
- **Cópia para pasta sincronizada (09/10/2026):** `specs/agendamento.md`
  (decisão 8) passa a copiar `relatorio.html` para uma pasta de destino
  (padrão `%OneDrive%\indicadores-bcb`) ao fim do wrapper, quando o exit é
  `0` ou `1` e o arquivo foi regenerado na execução. Esta spec e o
  `__main__` não mudam; as afirmações de que `executar_diario.ps1` não muda
  referem-se só ao exit `4`.

## Regras de negócio
1. `gerar_html` é pura: mesma entrada (`dados` em qualquer ordem e
   `gerado_em`) produz exatamente a mesma `str`; sem relógio, sem I/O.
2. O relatório reutiliza `analise.py`; nenhuma regra de IPCA, Selic ou
   PTAX mensal é reimplementada em `relatorio.py`.
3. Uma série por gráfico, um eixo Y por gráfico, sem legenda, sem eixo
   duplo.
4. O arquivo não contém `http://`, `https://`, `src=`, `href=`,
   `url(`, `@import`, `<link`, `<img`, `<iframe`; tem exatamente um
   `<script>`, sem `src`.
5. Todo texto interpolado (inclusive nome de série vindo dos dados e
   atributos como `data-pontos`) passa por `html.escape`.
6. Dados insuficientes nunca quebram a geração nem removem a seção.
7. Formatação pt-BR é só exibição e não depende de `locale`.
8. Falha do relatório nunca impede nem desfaz a gravação dos dados e
   nunca altera o significado dos exit codes `0`–`3`.
9. `--so-relatorio` nunca cria client, nunca usa rede, nunca escreve no
   banco (abre em somente leitura, decisão 12) e nunca cria o arquivo de
   banco.
10. `gravar_relatorio` remove o temporário em qualquer interrupção
    (inclusive `KeyboardInterrupt`/`SystemExit`), grava o conteúdo em
    disco (`flush` + `fsync`) antes do `os.replace` e nunca altera o
    relatório anterior em caso de falha (decisão 13).
11. Falha apenas ao imprimir a mensagem de sucesso, com o arquivo já
    gravado, nunca escapa como exceção nem altera o exit code (decisão
    14).
12. O nome da série vem da linha de maior data do código, independente da
    ordem das linhas (decisão 16); a largura das barras do IPCA mensal
    fica entre 1 e 24 unidades do `viewBox` (decisão 17).

## Dependências
Nenhuma nova: só biblioteca padrão (`html`, `json`, `math`, `os`,
`pathlib`, `datetime`) e `pandas` (já em `requirements.txt`).
`relatorio.py` importa apenas `pandas`, a biblioteca padrão e
`indicadores.analise` — **não** importa `duckdb`, `httpx`, `persistencia`,
`pipeline` nem `extracao`.

## Limitações conhecidas e não verificado
- **Tooltip/JS não é testado automaticamente** (não há navegador na
  suíte): os testes cobrem o HTML/SVG/`data-pontos` gerado, não o
  comportamento do script. **Não verificado** até abrir o arquivo num
  navegador (ver "Verificação manual").
- **Toque (decisão 15) não é testado automaticamente:** os testes só
  conferem que o CSS contém `touch-action: pan-y` para os `<svg>` dos
  gráficos dentro de `@media (min-width: 560px)` (e não fora dela) e que o
  JS menciona/trata `pointercancel`. O comportamento real (rolagem
  vertical preservada, tooltip no gesto horizontal, tooltip escondido no
  cancelamento) é **verificação manual**, **não verificado**.
- **`touch-action: pan-y` só a partir de 560px (decisão 15):** abaixo de
  ~560px o SVG (`min-width: 520px`) é mais largo que o contêiner
  `overflow-x: auto`, e ali o `<svg>` **não** declara `touch-action`; o
  gesto horizontal sobre o gráfico rola o contêiner (o lado direito e o
  último ponto continuam alcançáveis por toque), e por isso, nessas
  larguras, o tooltip por arrastar na horizontal sobre o gráfico não é
  garantido: o navegador assume o gesto e dispara `pointercancel`, que
  esconde o tooltip. A tabela correspondente continua sendo a visão
  acessível. O limite de 560px é aproximado e **não foi medido** contra a
  largura real do contêiner (margens e padding da página): numa faixa
  próxima de 560px o SVG pode ainda exceder levemente o contêiner com
  `pan-y` ativo; **não verificado**, a conferir na verificação manual.
- **Visual não é testado**: contraste, quebra de layout, aparência do
  tema escuro e das barras arredondadas dependem de inspeção humana.
- **Contraste dos rótulos dos eixos:** no tema claro os rótulos usam
  `#52514e` (texto secundário, contraste AA, decisão 7); no escuro,
  `#898781` sobre `#1a1a19` dá cerca de **4,9:1**, aceito (decisão 8). Os
  valores vêm do usuário; a razão de contraste não é medida por teste
  automático (conferir na verificação manual). O valor mais importante
  (último ponto) usa `--texto`, e todos os valores estão nas tabelas.
- **Tabela de IPCA** só mostra meses com janela de 12 meses completa
  (decisão 10); com menos de 23 meses de histórico ela mostra menos de 12
  linhas (com os 60 meses reais, mostra as 12 pedidas).
- **Último mês da PTAX é parcial** (e seu `fechamento` é o último dia com
  dado, não o fechamento do mês): a coluna `Dias` sinaliza, mas não há
  marca de "mês em andamento" (mesma decisão de `analise.py`). O card
  "vs fechamento do mês anterior" usa o fechamento de um mês já completo,
  então não sofre disso.
- **Variações dependem de dados contíguos:** lacunas viram `variação
  indisponível`, não são interpoladas.
- **Janela de 5 anos relativa à última data da série**: se uma série
  estiver desatualizada, o relatório mostra o que existe, sem aviso de
  defasagem (não há seção de status, por pedido).
- **Gráficos rolam na horizontal em telas < ~560px** (largura mínima do
  SVG) em vez de encolher (ver também o efeito do `touch-action` acima).
- **`--relatorio` e `--banco` são relativos ao diretório de trabalho**:
  rodar a CLI de outra pasta grava o relatório lá.
- **Falha do relatório com série ignorada fica só no log:** nesse caso o
  exit é `1` (decisão 11) e a falha do relatório só aparece como `ERROR` no
  stderr/log mensal; o Agendador não a distingue.
- **Falha de impressão fica só no log:** se a mensagem de sucesso não puder
  ser impressa (decisão 14), só o `WARNING` no stderr/log a registra; o
  Agendador não a distingue. O `print` do **resumo** (passo 7.1 de `main`,
  `formatar_resumo`) **não** foi protegido por esta decisão: uma falha de
  I/O ali continua escapando como antes (fora do pedido aprovado).
- **`read_only` com `.wal` pendente (decisão 12):** não verificado como o
  DuckDB se comporta ao abrir em somente leitura um arquivo cujo `.wal`
  não foi reaplicado; se falhar, `--so-relatorio` termina em `4` até uma
  execução normal reaplicar o WAL.
- **Sem `fsync` do diretório pai (decisão 13):** após queda de energia
  imediatamente depois do `os.replace`, o sistema pode mostrar o relatório
  anterior (íntegro). **Não verificado** em queda real.
- **Concorrência:** `--so-relatorio` com outra execução gravando o mesmo
  banco falha ao abrir o arquivo (bloqueio do DuckDB, vale também para
  `read_only`) → exit `4`; basta repetir depois.
- **Windows:** `os.replace` falha (`PermissionError`) se outro processo
  mantiver `relatorio.html` aberto sem compartilhamento de escrita;
  vira exit `4` com o arquivo antigo intacto. **Não verificado** com
  navegadores reais (em geral não travam o arquivo).
- **Tamanho:** ~3,2 mil pontos de tooltip no `data-pontos` (1837 + 1264 +
  60 + ...) estimam algumas centenas de KB de HTML; **não medido**.
- **Valores `NaN`** são tratados defensivamente, mas não há teste com
  banco real que os produza (a coluna é `NOT NULL`).
- **Piso de 1 unidade na largura da barra (decisão 17)** só é atingível
  com mais de ~190 meses na mesma série; com a janela de 5 anos é
  defensivo e coberto apenas pelo teste direto de
  `_calcular_largura_barra`.

### Verificação manual (depois de implementado, contra o banco real)
1. `python -m indicadores --so-relatorio` e abrir `relatorio.html` com a
   rede desligada: tudo aparece, sem requisição externa (aba Rede do
   navegador vazia). Conferir também que `dados/indicadores.duckdb` não
   mudou (tamanho/data de modificação) e que não surgiu `.wal` ao lado.
2. Alternar tema claro/escuro do SO; conferir cores e legibilidade
   (inclusive os rótulos dos eixos nos dois temas).
3. Estreitar a janela até ~360px: cards quebram, tabelas e gráficos rolam.
4. Passar o mouse em cada gráfico: crosshair, data e valor corretos;
   desabilitar JS e conferir que o gráfico continua legível.
5. **Em dispositivo de toque real (ou emulação do DevTools com toque)**
   (decisão 15): rolar a página na vertical com o dedo sobre um gráfico
   (deve rolar); iniciar uma rolagem vertical com o tooltip visível (o
   tooltip deve sumir, `pointercancel`); abaixo de 560px (ex.: ~360px),
   arrastar na horizontal sobre o gráfico deve rolar o gráfico na
   horizontal com o dedo; a partir de 560px, arrastar na horizontal sobre
   o gráfico deve mostrar o tooltip (crosshair e marcador seguem o dedo) e
   a página ainda deve rolar na vertical.
6. Conferir que os números dos cards batem com `mudancas_selic`,
   `ipca_acumulado_12m` e `ptax_mensal` rodados à mão sobre o mesmo banco.
7. Conferir a aparência de IPCA mensal com poucos meses (ex.: banco de
   teste com 1 e 3 meses): barras estreitas e centradas, nunca uma barra
   larga (decisão 17).

## Fora de escopo
- Seção de status da execução, histórico de execuções, notificação.
- Exportar PDF/imagens, imprimir com layout próprio, múltiplos idiomas.
- Interatividade além do tooltip (zoom, seleção de período, filtros).
- Novas análises (correlações, projeções) e novos indicadores.
- Servir o relatório por HTTP, publicar, enviar por e-mail.
- Nova dependência (matplotlib, plotly, jinja2) e qualquer recurso externo.
- Alterar `analise.py`, `executar_diario.ps1`, e `persistencia.py` além do
  parâmetro opcional `somente_leitura` de `abrir_conexao` (decisão 12).
- Calendário de feriados para decidir se um mês PTAX está completo.
- Proteger o `print` do resumo do pipeline contra falha de I/O (decisão 14
  cobre só a mensagem de sucesso do relatório).

## Critérios de aceite
1. `gerar_html(dados, gerado_em)` devolve HTML válido com cabeçalho, 4
   cards, 4 gráficos SVG, 3 tabelas e rodapé, sem seção de status.
2. Os valores dos cards e das tabelas batem com `analise.*` (IPCA via
   `math.prod(1 + v/100)`) formatados em pt-BR, inclusive casos negativos.
3. Cada gráfico tem um só eixo Y com 4–6 marcas redondas, sem legenda,
   rótulo direto só no último ponto, e legível com o `<script>` removido.
4. Selic em degrau (só `H`/`V`); IPCA mensal em barras com negativas abaixo
   do zero; linhas de 2px; barras com 2px de espaço (quando abaixo do
   máximo de 24px de largura) e raio de 4px só na ponta do dado.
5. Nenhuma cor literal fora dos blocos de variáveis (claro/escuro com os
   valores desta spec, incluindo `--eixo` = `#52514e` no claro e `#898781`
   no escuro); texto nunca em `--serie`/`--negativo`.
6. O arquivo não contém nenhum recurso externo nem `http(s)://`; um
   único `<script>` inline; todo texto de dados é escapado.
7. `DataFrame` vazio, série ausente, histórico curto e lacunas produzem
   `Sem dados suficientes`/`variação indisponível` nos lugares certos,
   sem exceção; colunas faltando propagam `ErroAnalise`.
8. `gravar_relatorio` é atômica: em falha **ou interrupção** (inclusive
   `KeyboardInterrupt`/`SystemExit`), o arquivo anterior fica intacto e
   nenhum temporário sobra; o conteúdo é sincronizado em disco
   (`flush` + `fsync`) antes do `os.replace`.
9. `python -m indicadores` grava o relatório depois da execução; com
   `--sem-relatorio` não grava; com `--so-relatorio` grava sem cliente,
   sem rede e sem alterar o banco (aberto em somente leitura; bytes e
   data de modificação do arquivo inalterados).
10. Falha do relatório: exit `4` se o resto daria `0`; exit `1` preservado
    (a falha fica só no log, nível `ERROR` no stderr); exit `3` não tenta
    relatório; dados sempre preservados; erro em stderr nível `ERROR`.
11. `--so-relatorio` com `--sem-relatorio`, `--series` ou
    `--data-referencia` → exit `2`, nada criado.
12. `.gitignore` contém `relatorio.html` (e `relatorio.html.tmp`).
13. Os testes existentes de `main` e do wrapper não escrevem na raiz do
    repositório.
14. `relatorio.py` importa só biblioteca padrão, `pandas` e
    `indicadores.analise`.
15. Nenhum teste acessa a internet; cobertura de `relatorio.py` >= 90%
    (o JS inline é string e não conta como lógica Python a cobrir).
16. Falha de `OSError`/`UnicodeError` ao imprimir a mensagem de sucesso,
    com o arquivo gravado, não gera exceção nem traceback, registra
    `WARNING` e não altera o exit code (`0` segue `0`, `1` segue `1`).
17. O CSS dos `<svg>` dos gráficos contém `touch-action: pan-y` dentro de
    `@media (min-width: 560px)` (e não fora dela) e o JS trata
    `pointercancel` escondendo o tooltip; o comportamento em toque real
    fica como verificação manual.
18. O nome da série vem da linha de maior data; a largura das barras do
    IPCA mensal é `min(max(escala - 2, 1), 24)`, centrada no mês.

## Casos de teste (pytest)
Dataset de referência (helper de teste `dados_referencia()`), com
`gerado_em = datetime(2026, 10, 9, 14, 30)`:
- **Selic (432)**, diária, 2025-09-01 a 2026-08-31: `14.75` até
  2025-12-10; `15.00` de 2025-12-11 a 2026-03-18 (alta de +0,25); `14.75`
  de 2026-03-19 em diante (queda de −0,25). Card esperado: `14,75%`,
  última mudança `19/03/2026`, `▼ queda de −0,25 p.p.`.
- **IPCA (433)**, mensal (dia 1), 2024-08 a 2026-08 (25 meses), valores
  variados definidos no teste; o último (2026-08) é `-0.11`. Esperados
  calculados no teste com `math.prod`, nunca literal à mão.
- **PTAX (1)**, dias úteis seg–sex, 2025-08-01 a 2026-08-31, valores
  determinísticos; `2026-07-31 = 5.2000`, `2026-08-28 = 5.4000`,
  `2026-08-31 = 5.3784`. Esperado: `R$ 5,3784`, `31/08/2026`,
  `−0,40%` vs dia anterior, `+3,43%` vs fechamento de julho.

**Formatação e eixo**
1. `test_formatar_numero_pt_br` — milhar com ponto, decimal com vírgula,
   casas pedidas, negativo com `−`, `-0.001` com 2 casas vira `0,00`, não
   finito vira `—`; independe de `locale` (teste troca o locale do
   processo para um com formato diferente e o resultado não muda, ou
   confirma que `locale` não é importado).
2. `test_formatar_data_e_mes` — `formatar_data` dá `dd/mm/aaaa`;
   `formatar_mes` cobre os 12 meses (`jan` a `dez`).
3. `test_formatar_percentual_e_moeda` — `14,25%`; `R$ 5,3784`; casas
   padrão (2 e 4).
4. `test_formatar_variacao_seta_sinal_texto` — alta, queda e estável; o
   valor `+0,004` com 2 casas é `estável`, nunca `▲ +0,00`; sinal
   negativo é U+2212.
5. `test_calcular_marcas_eixo` — parametrizado (PTAX `4.8`–`6.2`; Selic
   `10.5`–`15`; IPCA com negativos `-0.5`–`1.2`; `0`–`1`; intervalo
   degenerado `15`–`15`): 4 a 6 marcas, múltiplas do passo, primeira
   `<= min`, última `>= max`, crescentes, sem ruído de ponto flutuante.

**Estrutura**
6. `test_estrutura_basica_do_documento` — `<!DOCTYPE html>`,
   `lang="pt-BR"`, `charset`, `viewport`, `<title>`, `<h1>` com o título
   exato.
7. `test_cabecalho_data_geracao_fonte_e_periodo` — `09/10/2026 14:30`,
   `SGS/BCB`, período `de 01/08/2024 a 31/08/2026` (menor e maior data
   exibidas).
8. `test_rodape_fonte_codigos_e_aviso_de_revisao` — fonte, `432`, `433`,
   `1`, nomes e aviso de que os dados podem ser revisados.
9. `test_quatro_cards_tres_tabelas_e_sem_secao_de_status` — 4 cards na
   ordem, 3 `<table>`, 4 gráficos; ausência de `inseridos`, `atualizados`,
   `descartes`, `série ignorada`, `status`.

**Cards**
10. `test_card_selic_queda_com_data_e_tamanho_da_mudanca` — `14,75%`,
    `19/03/2026`, `▼ queda de −0,25 p.p.`, de `15,00%` para `14,75%`.
11. `test_card_selic_alta_e_sem_mudancas` — parametrizado: série cuja
    última mudança é alta (`▲ alta de +0,25 p.p.`) e série constante
    (`Sem mudanças no período`).
12. `test_card_ipca_12m_via_produto_e_variacao_em_pp` — valor igual a
    `(math.prod(1 + v/100 ...) - 1) * 100` formatado, `ago/2026`, e
    variação em p.p. contra o mês anterior calculada no teste.
13. `test_card_ipca_mensal_negativo` — `−0,11%` e `ago/2026`.
14. `test_card_ptax_valor_data_e_duas_variacoes` — `R$ 5,3784`,
    `31/08/2026`, `−0,40%` vs dia anterior, `+3,43%` vs fechamento do mês
    anterior (caso negativo e positivo no mesmo card).
15. `test_toda_variacao_tem_seta_sinal_e_texto` — em todo o HTML, cada
    variação contém `▲`/`▼`/`▶` e a palavra `alta`/`queda`/`estável`;
    nenhuma regra de cor é aplicada às variações (classe de variação sem
    `color:` diferente de `--texto`).
16. `test_reutiliza_funcoes_de_analise` — troca `mudancas_selic`,
    `ipca_acumulado_12m` e `ptax_mensal` em `indicadores.relatorio` por
    espiões que devolvem `DataFrame`s conhecidos e diferentes dos reais;
    assert que o HTML mostra os valores dos espiões (prova que o
    relatório usa `analise` e não recalcula).

**Gráficos**
17. `test_quatro_graficos_svg_acessiveis_sem_legenda` — 4 `<svg>` com
    `role="img"`, `<title>`, `<desc>`; títulos dos `<h3>` nomeiam a série;
    nenhum elemento/classe de legenda.
18. `test_cada_grafico_tem_um_unico_eixo_y_com_4_a_6_marcas` — por SVG:
    uma só coluna de rótulos Y, 4–6 marcas, uma linha de grid por marca;
    nenhum rótulo de eixo à direita (sem eixo duplo).
19. `test_selic_em_degrau_so_h_e_v` — o `path` da Selic contém só comandos
    `M`, `H`, `V`; número de vértices igual ao de mudanças + extremos (não
    um por dia).
20. `test_ipca_mensal_barras_com_sinal_espaco_e_cantos` — número de barras
    igual ao de meses da janela; a do último mês (negativa) usa a classe
    de negativo e fica abaixo da linha zero, positivas acima; espaço de
    2px entre barras adjacentes (no dataset de referência, 25 meses,
    `escala` fica abaixo de 26 e a barra não atinge o máximo de 24px —
    decisão 17); raio 4 só na ponta do dado, reto no lado do zero.
21. `test_janela_de_5_anos_a_partir_da_ultima_data` — PTAX com 7 anos: o
    primeiro ponto de `data-pontos` é `>= ultima_data - 5 anos`; mudanças
    da Selic mais antigas ficam fora da tabela.
22. `test_rotulo_direto_so_no_ultimo_ponto` — por gráfico, exatamente um
    rótulo final, com o último valor formatado, em classe de cor de
    texto.
23. `test_data_pontos_para_tooltip` — `data-pontos` decodifica em JSON; um
    item por observação da janela; `x` não decrescente; texto com data
    pt-BR e valor formatado (`31/08/2026 — R$ 5,3784`).
24. `test_grafico_legivel_sem_javascript` — removendo o `<script>`, cada
    gráfico ainda tem linha/barras, marcas, grid e rótulo final; tooltip
    `hidden`; exatamente um `<script>` no documento original.

**Tabelas**
25. `test_tabela_selic_mais_recente_primeiro` — colunas
    `Data | De | Para | Variação`; ordem decrescente; formato pt-BR; só
    mudanças da janela.
26. `test_tabela_ipca_12_meses_bate_com_analise` — 12 linhas, mais recente
    primeiro, valores iguais a `ipca_acumulado_12m` formatados.
27. `test_tabela_ptax_mensal_bate_com_analise` — 12 linhas; média,
    fechamento, mínimo, máximo (`R$` 4 casas) e `Dias` iguais a
    `ptax_mensal`.
28. `test_tabelas_acessiveis_e_numeros_alinhados` — `<caption>`,
    `<th scope="col">`, células numéricas com classe de alinhamento,
    CSS com `text-align: right` e `font-variant-numeric: tabular-nums`,
    contêiner com `overflow-x: auto`.

**Design**
29. `test_css_variaveis_claro_e_escuro_com_os_valores_da_spec` — todos os
    valores hex da tabela de Design presentes no bloco certo (inclusive
    `--eixo` = `#52514e` no claro e `#898781` no escuro), e
    `@media (prefers-color-scheme: dark)`.
30. `test_nenhuma_cor_literal_fora_das_variaveis` — removidos os dois
    blocos de variáveis do `<style>`, não sobra `#rrggbb`; SVG sem
    `fill="#"`/`stroke="#"`/`style=` de cor; nenhuma regra `color:
    var(--serie)` ou `var(--negativo)`; linha com `stroke-width: 2`.
31. `test_layout_responsivo` — grid de cards com `auto-fit` e `minmax`,
    `min-width` do SVG e `overflow-x: auto` nos contêineres.

**Autocontido e segurança**
32. `test_sem_recursos_externos` — (dataset de referência **e** vazio) o
    HTML não contém `http://`, `https://`, `src=`, `href=`, `url(`,
    `@import`, `<link`, `<img`, `<iframe`, `fetch(`, `XMLHttpRequest`,
    `import(`, `eval(`; exatamente um `<script>` sem `src`.
33. `test_html_escape_em_texto_vindo_dos_dados` — coluna `serie` com
    `<script>alert(1)</script>"><b>`: no HTML aparece escapado (`&lt;`),
    sem `<script>alert` e sem quebrar atributos; segue havendo um só
    `<script>`.
34. `test_relatorio_usa_apenas_stdlib_pandas_e_analise` — por `ast` do
    arquivo, os imports são biblioteca padrão, `pandas` e
    `indicadores.analise` (sem `duckdb`, `httpx`, `matplotlib`, `plotly`,
    `jinja2`, `persistencia`, `pipeline`, `extracao`).
35. `test_deterministico_e_nao_altera_a_entrada` — duas chamadas iguais
    dão `str` idêntica; entrada embaralhada dá o mesmo resultado; `dados`
    é igual (`DataFrame.equals`) antes e depois; `gerado_em` diferente
    muda só o trecho do cabeçalho.

**Dados insuficientes**
36. `test_dataframe_vazio_gera_documento_completo` — 4 cards, 4 gráficos e
    3 tabelas com `Sem dados suficientes`, período `sem dados`, nenhum
    `<svg>`, sem exceção.
37. `test_serie_ausente_so_afeta_as_secoes_da_serie` — parametrizado por
    código ausente (432, 433, 1): as seções da série mostram
    `Sem dados suficientes` (433 afeta 2 cards, 2 gráficos e 1 tabela) e
    as demais permanecem completas.
38. `test_historico_curto` — IPCA com 11 meses: card/gráfico/tabela de 12m
    vazios, mas card e barras do IPCA mensal presentes; Selic com 1
    observação: card com valor e `Sem mudanças no período`, gráfico vazio;
    PTAX com 1 observação: card sem variações (`variação indisponível`).
39. `test_lacunas_viram_variacao_indisponivel` — IPCA sem o mês anterior ao
    último; PTAX sem o mês anterior em `ptax_mensal`: só a variação
    afetada vira `variação indisponível`.
40. `test_colunas_faltando_propaga_erro_analise` — sem `valor` (e sem
    `codigo`, sem `data`): `ErroAnalise`.

**Escrita**
41. `test_gravar_relatorio_utf8_cria_diretorio_e_substitui` — conteúdo
    com acentos e `—` lido de volta igual (UTF-8); cria diretório pai;
    sobrescreve arquivo existente; não sobra `.tmp`.
42. `test_gravar_relatorio_atomico_quando_replace_falha` —
    `os.replace` forçado a levantar `OSError`: arquivo anterior com
    conteúdo original intacto, `.tmp` removido, exceção propagada.
43. `test_gravar_relatorio_limpa_temporario_quando_escrita_falha` — falha
    durante a escrita do temporário: `.tmp` removido, destino intacto,
    exceção propagada.

**Integração com a CLI** (`tests/test_main.py`, `tmp_path`, client com
`httpx.MockTransport`)
44. `test_main_grava_relatorio_depois_da_execucao` — exit `0`;
    `relatorio.html` em `--relatorio` contém os valores recém-gravados
    (lidos do banco); stdout tem o resumo seguido de `Relatório gravado
    em: <caminho>`; stderr sem `ERROR`.
45. `test_main_sem_relatorio_nao_gera_arquivo` — exit `0`, nenhum
    arquivo de relatório, sem a linha de relatório no stdout.
46. `test_main_so_relatorio_sem_rede_e_sem_alterar_o_banco` — banco
    previamente populado via `persistencia`; `criar_client` substituído
    por função que falha se chamada; exit `0`; relatório gerado; conteúdo
    do banco (inclusive `atualizado_em`) idêntico; conexão fechada (o
    arquivo pode ser reaberto para escrita logo em seguida).
47. `test_main_so_relatorio_com_banco_inexistente` — exit `4`; o arquivo
    `.duckdb` e seu diretório **não** foram criados; nenhum relatório;
    `ERROR` em stderr.
48. `test_main_flags_incompativeis_retornam_dois` — parametrizado:
    `--so-relatorio --sem-relatorio`, `--so-relatorio --series 432`,
    `--so-relatorio --data-referencia 2026-01-01`: exit `2` e nenhum
    arquivo criado.
49. `test_main_falha_ao_gerar_relatorio_retorna_quatro_e_preserva_dados`
    — `gerar_html` forçado a levantar `RuntimeError`; exit `4`; dados
    gravados (conferidos com `persistencia.ler`); resumo no stdout; `ERROR`
    `falha ao gerar o relatório` em stderr mesmo sem `-v`; relatório
    anterior (criado antes pelo teste) intacto.
50. `test_main_falha_do_relatorio_com_serie_ignorada_mantem_um` — uma
    série responde 404 e `gerar_html` levanta: exit `1` (não `4`), `ERROR`
    no log.
51. `test_main_serie_ignorada_ainda_gera_relatorio` — uma série 404, banco
    com dados antigos: exit `1` e relatório gerado com esses dados.
52. `test_main_erro_inesperado_nao_tenta_relatorio` — `executar` levanta:
    exit `3`; `gerar_html` (espião) nunca chamado; relatório anterior
    intacto.
53. `test_main_falha_de_escrita_do_relatorio_retorna_quatro` —
    `--relatorio` apontando para um diretório existente (ou `os.replace`
    forçado a falhar): exit `4`, sem traceback em stdout, sem `.tmp`.
54. `test_main_passa_horario_de_brasilia_naive_ao_relatorio` — espião em
    `gerar_html`: `gerado_em` é `datetime` sem `tzinfo`, dentro de poucos
    segundos de `datetime.now(FUSO_BRASILIA)`; conexão fechada ao fim.
55. `test_main_defaults_e_ajuda_das_flags_novas` — default de `--relatorio`
    é `Path("relatorio.html")`; `--help` mostra `--relatorio CAMINHO`,
    `--sem-relatorio` e `--so-relatorio`.

**Repositório e wrapper**
56. `test_gitignore_ignora_relatorio_html` — o `.gitignore` tem uma linha
    `relatorio.html` (e `relatorio.html.tmp`).
57. `test_wrapper_repassa_exit_4_sem_nova_tentativa` — Windows-only
    (`skipif`, em `tests/test_scripts.py`); roda
    `executar_diario.ps1 --so-relatorio --banco <tmp_path>\nao_existe.duckdb`
    com `INDICADORES_LOG_DIR=tmp_path`: exit `4`, log com `fim (exit code:
    4)` e **sem** `tentativa 2`. Offline e rápido (não chega à API).

**Melhorias da revisão de código de 09/10/2026** (decisões 12 a 17)

*Banco em somente leitura (decisão 12)*
58. `test_abrir_conexao_somente_leitura` — em `tests/test_persistencia.py`,
    com arquivo real em `tmp_path` previamente criado e populado
    (`abrir_conexao` padrão + `criar_tabela` + `gravar`, depois
    `close()`): (a) `abrir_conexao(caminho, somente_leitura=True)` devolve
    conexão em que `ler(conexao)` funciona e um `INSERT`/`CREATE TABLE`
    levanta o erro nativo do `duckdb`; (b) com caminho cujo arquivo e
    diretório pai **não existem**, `somente_leitura=True` levanta erro e
    **não cria** nem o arquivo nem o diretório; (c) sem o parâmetro
    (default), o comportamento anterior é preservado (cria o diretório
    pai, conexão grava) — o caso 11 de `persistencia.md` continua
    passando sem alteração.
59. `test_main_so_relatorio_abre_banco_somente_leitura` — em
    `tests/test_main.py`; espião que envolve `abrir_conexao` no namespace
    de `__main__` registrando os argumentos e devolvendo a conexão real:
    em `--so-relatorio`, chamado uma vez com `somente_leitura=True`; no
    fluxo normal (`--sem-relatorio`), chamado **sem** `somente_leitura`
    verdadeiro. Prova adicional da conexão efetiva: a conexão devolvida ao
    relatório rejeita escrita (um `INSERT` feito pelo espião logo após a
    chamada levanta o erro nativo do `duckdb`).
60. `test_main_so_relatorio_nao_altera_bytes_nem_mtime_do_banco` — em
    `tests/test_main.py`; banco populado e fechado antes; registra
    `hashlib.sha256` do conteúdo e `os.stat(...).st_mtime_ns` do arquivo
    `.duckdb`; roda `main(["--so-relatorio", "--banco", ..., "--relatorio",
    ...])` com exit `0`; depois, o hash e o `st_mtime_ns` são **iguais** e
    não existe arquivo `.wal` ao lado. Complementa o caso 46 (que compara
    conteúdo lógico das linhas).

*Escrita robusta do relatório (decisão 13)*
61. `test_gravar_relatorio_interrupcao_limpa_temporario_e_preserva_anterior`
    — parametrizado por `KeyboardInterrupt` e `SystemExit`; `os.replace`
    (monkeypatch) levanta a exceção no momento em que o temporário já está
    escrito; assert: a exceção **propaga** (`pytest.raises`), o `.tmp` não
    existe mais, e o relatório anterior (criado antes pelo teste) mantém
    exatamente o conteúdo original.
62. `test_gravar_relatorio_faz_flush_e_fsync_antes_do_replace` — espiões em
    `os.fsync` e `os.replace` (delegando às funções reais) registram a
    ordem das chamadas; assert: `fsync` foi chamado ao menos uma vez com o
    descritor do temporário **antes** do `replace`, e, no instante do
    `replace`, o `.tmp` já contém o HTML completo (lido de volta dentro do
    espião do `replace`, igual a `html`).
63. `test_gravar_relatorio_falha_ao_remover_temporario_nao_mascara_a_excecao`
    — `os.replace` levanta `OSError("replace")` e a remoção do temporário
    (`Path.unlink`, monkeypatch) levanta `OSError("unlink")`; assert: a
    exceção que propaga é a do `replace` (mensagem `replace`), não a do
    `unlink`, e o relatório anterior segue intacto.

*Mensagem de sucesso (decisão 14)*
64. `test_main_falha_ao_imprimir_sucesso_nao_escapa_e_mantem_exit` — em
    `tests/test_main.py`; parametrizado por exceção (`UnicodeEncodeError`,
    `OSError`) e por cenário (todas as séries OK → exit `0`; uma série 404 →
    exit `1`). `print` substituído, no namespace de `__main__`, por função
    que levanta a exceção só para a linha em branco e para a mensagem
    `Relatório gravado em: ...` e delega ao `print` real para o resumo.
    Assert: `main` retorna o exit esperado (`0` ou `1`, nunca `4`), sem
    exceção e sem traceback; o arquivo do relatório existe com o conteúdo
    esperado; os dados estão no banco; stderr tem um `WARNING` de
    `indicadores.pipeline` mencionando o caminho e **não** tem `ERROR`
    (nem `falha ao gerar o relatório`); conexão e client fechados.
65. `test_main_so_relatorio_falha_ao_imprimir_sucesso_mantem_exit_zero` —
    mesmo `print` com falha, em `--so-relatorio` sobre banco populado:
    exit `0` (não `4`), relatório gravado, `WARNING` no stderr, conexão
    fechada.

*Toque (decisão 15) — teste estático; o comportamento real é manual*
66. `test_css_svg_dos_graficos_tem_touch_action_pan_y` — `gerar_html` com o
    dataset de referência: o `<style>` contém `touch-action: pan-y` dentro
    de `@media (min-width: 560px)`, em regra cujo seletor atinge os `<svg>`
    dos gráficos; e **não** contém `touch-action` fora dessa `@media`
    (removido o bloco da `@media (min-width: 560px)`, o restante do
    `<style>` não tem `touch-action`, o que cobre também a regra do `svg`
    que define `min-width`, as regras de `body`/`html` e a página toda).
67. `test_js_trata_pointercancel_escondendo_o_tooltip` — o único
    `<script>` contém um `addEventListener` para `pointercancel` e, no
    mesmo tratador (ou no mesmo ramo de `pointerleave`), a ação de
    esconder o tooltip (atributo `hidden`/função compartilhada de
    esconder). Teste de texto: não executa o JS.

*Robustez (decisões 16 e 17)*
68. `test_nome_da_serie_usa_a_linha_de_maior_data` — `_nome_da_serie` e o
    rodapé de `gerar_html`: linhas do mesmo código com nomes diferentes
    (`selic_antiga` na data mais antiga, `selic_meta` na mais recente), com
    a entrada na ordem original **e** embaralhada: o nome é sempre
    `selic_meta`. Parametrizado também para: nome vazio/`None`/só espaços
    na linha mais recente → usa o mais recente não vazio; coluna `serie`
    ausente ou toda vazia → nome padrão; empate de data → menor nome em
    ordem alfabética.
69. `test_largura_da_barra_formula_e_limites` — `_calcular_largura_barra`
    parametrizado: `escala` `0.5`, `2`, `3` → `1` (piso); `9.5` → `7.5`;
    `26` → `24`; `100` e `572` → `24` (teto). Também confirma as
    constantes `ESPACO_ENTRE_BARRAS == 2` e `LARGURA_MAXIMA_BARRA == 24`.
70. `test_ipca_poucos_meses_nao_gera_barra_gigante` — `gerar_html` com a
    série 433 de **1 mês** (e também de 2 e 3 meses): a largura medida na
    geometria emitida de cada barra do gráfico b2 é `<= 24` (nunca perto de
    572); a barra única fica **centrada** na área de plotagem; e com 25
    meses (dataset de referência) a largura é `escala - 2`, igual à
    fórmula.

**Total previsto: 70 casos de teste novos** (57 originais + 13 da revisão
de código; alguns parametrizados, o que dá bem mais execuções). Mais os
ajustes nos testes existentes descritos em "Mudanças em outros documentos
e testes existentes".

## Convenções seguidas
- Nomes de função, variável e constante em português, snake_case
  (`gerar_html`, `gravar_relatorio`, `calcular_marcas_eixo`,
  `JANELA_ANOS`), exceto identificadores de bibliotecas.
- Núcleo puro e I/O na borda (`gerar_html` vs `gravar_relatorio`/`__main__`),
  mesmo princípio de `analise.py` vs `persistencia.py`.
- Constantes de código de série reaproveitadas de `analise.py`.
- Dados em ISO/`float` por todo o código; pt-BR só na camada de exibição.
- Nenhum teste faz chamada de rede nem cria arquivo fora de `tmp_path`.
- Relógio injetado (`gerado_em`), nunca lido dentro do núcleo puro.
- `main` continua responsável por fechar client e conexão em todos os
  caminhos; o relatório roda antes do `finally`.
