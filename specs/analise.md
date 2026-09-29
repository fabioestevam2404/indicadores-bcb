# Especificação — Módulo de Análise (`src/indicadores/analise.py`)

## Status
Sexto módulo do projeto. `extracao.py`, `limpeza.py` e `persistencia.py`
já estão implementados (lidos diretamente para confirmar contrato:
`persistencia.ler` devolve `pandas.DataFrame` com colunas
`["codigo", "serie", "data", "valor", "atualizado_em"]`, dtypes
`int64`/`object`/`datetime64[ns]`/`float64`/`datetime64[ns]`;
`limpeza.limpar` devolve o mesmo formato, só sem `atualizado_em`). Este
módulo consome esse formato para calcular três análises de negócio.
Segue o mesmo formato das specs anteriores.

## Objetivo
Três análises, entregues como **funções Python puras que recebem um
DataFrame e devolvem outro DataFrame**, sem CLI e sem views SQL no
DuckDB (ambos fora de escopo, ver "Fora de escopo"):
1. **Mudanças da Selic meta** (código 432): quando e quanto o valor da
   meta mudou.
2. **IPCA acumulado em 12 meses** (código 433): a partir da série
   mensal, o acumulado móvel de 12 meses.
3. **PTAX venda mensal** (código 1): agregação mensal da série diária
   (média, fechamento, mínimo, máximo, dias com dado).

Nenhuma das três faz I/O — não abrem conexão, não conhecem
`duckdb`/`httpx`. O uso típico é:
```python
from indicadores.persistencia import abrir_conexao, ler
from indicadores.analise import mudancas_selic, ipca_acumulado_12m, ptax_mensal

conexao = abrir_conexao()
dados = ler(conexao)          # ou limpeza.limpar(...).dados
selic = mudancas_selic(dados)
ipca = ipca_acumulado_12m(dados)
ptax = ptax_mensal(dados)
conexao.close()
```

## Localização
```
src/indicadores/analise.py
```
Testes em `tests/test_analise.py`, todos com DataFrames montados à
mão (sem rede, sem arquivo), exceto um teste de integração leve com
`duckdb.connect(":memory:")` (ver "Casos de teste").

## Contrato de entrada
Cada função aceita `dados: pd.DataFrame` no formato de saída de
`persistencia.ler` **ou** de `limpeza.limpar` — ambos compatíveis,
porque as três colunas que estas funções realmente usam
(`codigo: int64`, `data: datetime64[ns]`, `valor: float64`) estão
presentes nos dois; `serie` e `atualizado_em` (quando presentes) são
ignoradas. Os códigos de série usados por cada função são constantes
nomeadas no módulo, não números mágicos:
```python
CODIGO_SELIC_META: int = 432
CODIGO_IPCA_MENSAL: int = 433
CODIGO_PTAX_VENDA: int = 1
```

### Exceção
```python
class ErroAnalise(Exception):
    """`dados` não tem as colunas mínimas exigidas."""
```
Levantada se `dados` não contiver **todas** as colunas
`{"codigo", "data", "valor"}` (por nome; `serie`/`atualizado_em` não são
exigidas). Não valida dtype das colunas — mesma decisão já tomada em
`persistencia._validar_dados`: os módulos upstream (`limpeza.limpar`,
`persistencia.ler`) são a fonte de verdade dos tipos; validar de novo
aqui seria redundante.

### Ordenação interna, sempre
Todas as três funções ordenam por `data` **internamente**
(`dados.sort_values("data")`), nunca dependem da ordem de entrada — um
DataFrame de entrada fora de ordem cronológica produz o mesmo resultado
que um já ordenado.

### Série ausente ou vazia
Se, depois de filtrar pelo código da função, não sobrar nenhuma linha
(código não presente em `dados`, ou presente só com 0 linhas), a função
devolve um DataFrame **vazio, com as colunas e dtypes corretos da
saída** (nunca lista/erro) — mesmo padrão de "DataFrame vazio com
dtypes explícitos" já usado em `limpeza.py`/`persistencia.py`.

## `mudancas_selic(dados: pd.DataFrame) -> pd.DataFrame`
```python
def mudancas_selic(dados: pd.DataFrame) -> pd.DataFrame:
    """Datas em que a Selic meta (código 432) mudou de valor.

    Retorna colunas `data` (datetime64[ns]), `valor_anterior` (float64),
    `valor_novo` (float64) e `variacao_pp` (float64, = valor_novo -
    valor_anterior, em pontos percentuais). Só inclui uma linha quando
    `valor_novo != valor_anterior` (comparação exata, ver decisão
    abaixo). A primeira observação da série nunca aparece (não há
    "anterior" para compará-la).
    """
```
Decisões registradas:
- **A primeira observação nunca entra** — decisão adotada (era a
  sugestão do pedido): não há valor anterior para compará-la, então não
  há "mudança" para reportar nela.
- **Comparação de igualdade exata** (`valor_novo != valor_anterior`,
  sem tolerância) — decisão adotada (era a sugestão do pedido): os
  valores de origem são strings decimais do SGS, convertidas por
  `limpeza._parse_valor` sempre pelo mesmo algoritmo determinístico;
  duas strings **iguais** (ex. duas ocorrências de `"13,75"`) sempre
  produzem o mesmo `float64` bit a bit, então comparar com `!=` nunca
  gera falso positivo por causa de arredondamento de ponto flutuante
  nesta série especificamente — o valor não é resultado de nenhuma
  soma/produto acumulado (diferente do IPCA acumulado, onde há
  multiplicação em cadeia). Tolerância (`math.isclose`) traria
  complexidade sem benefício aqui.
- Linhas com data duplicada para o mesmo código **não são tratadas
  especialmente** — assume-se que a entrada já vem deduplicada por
  `(codigo, data)` (garantido por `limpeza.limpar` e pela chave
  primária de `persistencia`); esta função não reimplementa essa
  deduplicação.

## `ipca_acumulado_12m(dados: pd.DataFrame) -> pd.DataFrame`
```python
def ipca_acumulado_12m(dados: pd.DataFrame) -> pd.DataFrame:
    """Acumulado móvel de 12 meses do IPCA mensal (código 433).

    Para cada mês com uma janela de 12 meses **consecutivos** (sem
    lacuna) terminando nele, calcula:
        acumulado_12m = (prod(1 + v/100 para os 12 valores mensais
                              da janela) - 1) * 100
    em pontos percentuais, sem arredondamento. Retorna colunas `data`
    (datetime64[ns], o mês final da janela), `ipca_mensal` (float64, o
    valor daquele mês) e `acumulado_12m` (float64).
    """
```
Decisões registradas:
- **Só aparecem meses com janela de 12 consecutivos disponível**
  (decisão adotada, era a sugestão do pedido): se a janela dos 12
  meses anteriores a um mês (inclusive) tiver uma lacuna (um mês
  faltando no meio) ou não tiver 12 meses de histórico ainda
  disponíveis, **aquele mês simplesmente não aparece no resultado** —
  não aparece com `NaN`, é omitido da saída inteiramente.
- **"Consecutivo" é checado por mês civil, não pelo dia exato da
  data:** cada data é convertida para `Period` mensal
  (`dados["data"].dt.to_period("M")`); dois meses são consecutivos se a
  diferença dos `Period` for exatamente `1`. Isso porque o dia do mês
  registrado pelo SGS para uma série mensal não é necessariamente o
  dia 1 (pode variar), então comparar por mês civil, não por
  quantidade de dias corridos, é o que corresponde à noção real de
  "mês seguinte".
- **Sem arredondamento** — decisão confirmada pelo pedido do usuário: o
  projeto não arredonda na camada de dados; os testes comparam com
  `pytest.approx`.
- **A janela desliza mês a mês, recalculada do zero a cada ponto** — a
  saída pode ter várias linhas (uma por mês final elegível), cada uma
  com sua própria janela de 12 meses; a janela do mês N+1 (meses
  2..13) não reaproveita nenhum estado da janela do mês N (meses
  1..12) — são dois produtos diferentes, sobre 12 valores parcialmente
  distintos. Isso é verificado explicitamente pelo caso de teste
  "janela desliza" (ver "Casos de teste"), com valores mensais
  variados o suficiente para que um erro de implementação (ex.
  reaproveitar a janela anterior, ou deslizar errado) mude o resultado
  numérico de forma detectável — ao contrário de um teste só com
  valores mensais idênticos, onde esse tipo de erro passaria
  despercebido porque qualquer janela de 12 iguais dá o mesmo produto.
- **Suposição registrada:** assume-se uma linha por mês por código
  (padrão observado nas séries mensais do SGS). Duas linhas no mesmo
  mês civil (datas diferentes, mesmo `Period` mensal) não é um caso
  validado nem tratado especialmente por esta função — não ocorre na
  prática com os dados reais do SGS, então não há teste dedicado a
  esse caso (fora de escopo).

### Exemplo calculado à mão (usado no caso de teste 4)
12 meses consecutivos, cada um com `ipca_mensal = 1.0` (1,00%):
```
acumulado_12m = (1.01**12 - 1) * 100
              = (1.12682503013196... - 1) * 100
              ≈ 12.682503 %
```
`1.01**12 ≈ 1.126825030132` é uma constante de composição de juros
bem conhecida (1% ao mês por 12 meses). O teste deve comparar o
resultado da função com `pytest.approx((1.01**12 - 1) * 100)`
**calculado no próprio teste com a mesma fórmula em Python**, não com
um literal decimal digitado à mão — o valor `≈ 12,682503%` acima é só
para conferência visual da ordem de grandeza nesta spec; um literal
truncado manualmente arriscaria não bater exatamente com o `float64`
que `pytest.approx` compara. O mesmo raciocínio (calcular o esperado
programaticamente no teste, nunca um literal manual) vale para os
casos de teste "valores variados" e "janela desliza" descritos abaixo,
que usam `math.prod` sobre uma lista de valores diferentes entre si.

## `ptax_mensal(dados: pd.DataFrame) -> pd.DataFrame`
```python
def ptax_mensal(dados: pd.DataFrame) -> pd.DataFrame:
    """Agregação mensal da PTAX venda diária (código 1).

    Agrupa por mês civil (`data.dt.to_period("M")`). Retorna colunas:
    - `mes` (datetime64[ns]): primeiro dia do mês (`Period.to_timestamp()`).
    - `media` (float64): média dos `valor` do mês.
    - `fechamento` (float64): `valor` do dia **com maior `data`
      disponível no mês** (o último dia com dado, não necessariamente o
      último dia civil do mês — ver decisão abaixo).
    - `minimo` (float64), `maximo` (float64): mínimo/máximo de `valor`
      no mês.
    - `dias_com_dado` (int64): quantidade de linhas (dias) daquele mês
      presentes em `dados`.
    """
```
Decisões registradas:
- **`fechamento` é o valor do último dia **com dado**, não do último
  dia civil do mês:** a PTAX não tem cotação em fins de semana/feriados,
  então o último dia civil do mês (ex. dia 31) frequentemente não tem
  linha nenhuma — usar o `valor` da linha com o maior `data` dentro do
  mês (que pode ser dia 29, 30, etc., dependendo de quando caiu o
  último dia útil) é o comportamento correto e o que o caso de teste
  "fechamento como último dia útil" verifica.
- **Indicador de mês incompleto — decisão confirmada pelo usuário:**
  **não** existe uma coluna `mes_completo: bool`; `dias_com_dado` é a
  informação que sinaliza um mês incompleto (ex. um número bem menor
  que o típico ~21 dias úteis) — quem consome a análise usa
  `dias_com_dado` para essa checagem, sem necessidade de coluna extra.
  Motivo da escolha (mais simples e defensável, conforme pedido): uma
  coluna `mes_completo` exigiria decidir quantos dias **deveriam** ter
  dado num mês civil qualquer (calendário de dias úteis com feriados
  bancários, algo fora do escopo já registrado de todos os módulos
  deste projeto) **ou** uma data de referência injetável só para saber
  se o mês corrente ainda está em andamento — ambas adicionam
  complexidade e uma dependência externa (calendário de feriados, ou
  parâmetro de "hoje") que esta função, sendo pura e sem qualquer I/O
  ou parâmetro de tempo nas outras duas análises, não tem hoje. Expor
  `dias_com_dado` cru é suficiente e não exige nenhuma dessas duas
  coisas. **Limitação aceita e registrada:** quem consome a análise
  precisa saber, por conta própria, se está olhando para o mês corrente
  (naturalmente parcial) ou para um mês passado com uma lacuna
  incomum — esta função não distingue os dois casos.

## Regras de negócio
1. As três funções são puras: mesma entrada sempre produz a mesma
   saída, sem efeito colateral, sem I/O.
2. Cada função filtra só o código de série que lhe compete
   (`CODIGO_SELIC_META`, `CODIGO_IPCA_MENSAL`, `CODIGO_PTAX_VENDA`);
   linhas de outros códigos em `dados` são ignoradas silenciosamente
   (não geram erro nem aparecem na saída).
3. `ErroAnalise` só é levantada por colunas mínimas faltando
   (`codigo`, `data` ou `valor`) — nunca por dado vazio/série ausente
   (esse caso vira DataFrame de saída vazio, não erro).
4. Toda saída tem dtypes explícitos, aplicados via `.astype(...)`,
   nunca por inferência do pandas — mesmo padrão de `limpeza.py` e
   `persistencia.py`, pensado para estabilidade entre versões do
   pandas (inclusive pandas 3).
5. Toda saída tem índice resetado (`reset_index(drop=True)`) e está
   ordenada pela coluna de data/mês correspondente, ascendente.
6. `ptax_mensal` não tem coluna `mes_completo`; `dias_com_dado` é a
   forma documentada e definitiva de sinalizar mês incompleto
   (decisão confirmada pelo usuário, não reaberta).

## Dependências
Nenhuma dependência nova: só `pandas` (já em `requirements.txt`).

## Fora de escopo
- CLI (nenhum comando de linha de comando expõe estas análises).
- Views SQL no DuckDB (as análises são só funções Python sobre
  DataFrame; materializá-las como view é uma decisão futura separada).
- Gráficos/visualização.
- Correlações entre séries (ex. Selic × IPCA).
- IPCA anualizado por outros métodos (ex. projeção) e deflacionamento
  de valores monetários usando o IPCA.
- Calendário de dias úteis/feriados bancários (usado, por exemplo, para
  saber quantos dias **deveriam** ter cotação PTAX num mês) — ver
  decisão em "Indicador de mês incompleto".

## Critérios de aceite
1. `mudancas_selic` retorna só as datas em que o valor mudou
   (comparação exata), nunca a primeira observação da série.
2. `ipca_acumulado_12m` só retorna meses com janela de 12 consecutivos
   sem lacuna; o valor bate com a fórmula
   `(prod(1 + v/100) - 1) * 100`, verificado via `pytest.approx` com
   casos calculados programaticamente (não hardcoded), incluindo pelo
   menos um caso com valores mensais diferentes entre si (não só um
   valor uniforme repetido) e um caso com janela deslizando entre dois
   meses finais consecutivos, com resultados diferentes entre si.
3. `ptax_mensal` agrega corretamente por mês civil, com `fechamento`
   igual ao último dia **com dado**, não ao último dia civil.
4. Série ausente ou vazia (para o código de cada função) devolve
   DataFrame vazio com as colunas e dtypes documentados, sem erro.
5. Colunas obrigatórias faltando (`codigo`, `data` ou `valor`) levantam
   `ErroAnalise` antes de qualquer cálculo.
6. Entrada fora de ordem cronológica produz o mesmo resultado que a
   mesma entrada ordenada.
7. Todas as saídas têm dtypes explícitos e índice resetado.
8. Nenhum teste faz I/O real, exceto o teste de integração leve
   (`duckdb.connect(":memory:")`).
9. Cobertura de testes do módulo ≥ 80%.

## Casos de teste (pytest)
1. `test_mudancas_selic_detecta_alta` — série com valores
   `[13,75; 13,75; 14,25]` em datas crescentes; assert 1 linha, com
   `valor_anterior=13.75`, `valor_novo=14.25`, `variacao_pp=0.5`.
2. `test_mudancas_selic_detecta_baixa` — mesma ideia, com queda (ex.
   `14,25 -> 13,75`); assert `variacao_pp` negativo.
3. `test_mudancas_selic_valores_repetidos_nao_geram_linha` — 3+ datas
   com o mesmo valor; assert DataFrame de saída vazio.
4. `test_mudancas_selic_primeira_observacao_nunca_aparece` — série de
   só 1 valor; assert saída vazia (não há "anterior"), e também com 2+
   valores, confirma que a 1ª data nunca aparece como `data` de saída,
   mesmo quando há mudança logo na 2ª observação.
5. `test_ipca_acumulado_12m_valor_calculado_a_mao` — 12 meses
   consecutivos com `ipca_mensal = 1.0`; assert 1 linha de saída, com
   `acumulado_12m == pytest.approx((1.01**12 - 1) * 100)`.
6. `test_ipca_acumulado_12m_janela_incompleta` — só 11 meses
   consecutivos disponíveis; assert saída vazia (nenhum mês com 12
   completos).
7. `test_ipca_acumulado_12m_mes_faltando_no_meio` — 13+ meses de dados,
   com um mês faltando no meio da série (lacuna); assert que os meses
   cuja janela de 12 meses **inclui** a lacuna não aparecem na saída, e
   que meses suficientemente depois da lacuna (cuja janela de 12 meses
   não toca mais o mês faltante) voltam a aparecer normalmente.
8. `test_ipca_acumulado_12m_valores_variados` — 12 meses consecutivos
   com valores diferentes entre si, ex.
   `[0.5, 0.3, -0.1, 0.8, 0.2, 0.4, 0.6, -0.2, 0.1, 0.9, 0.3, 0.7]`;
   o valor esperado é calculado no próprio teste a partir dessa lista,
   via `(math.prod(1 + v / 100 for v in valores) - 1) * 100`, comparado
   com `pytest.approx`. Motivo (registrado): com todos os valores
   iguais (como no teste 5), um erro na composição da janela (ex.
   somar em vez de multiplicar, ou aplicar a fórmula errada por
   elemento) poderia passar despercebido, porque o resultado "parece"
   plausível de qualquer forma quando os 12 valores são idênticos;
   valores variados tornam esse tipo de erro detectável.
9. `test_ipca_acumulado_12m_janela_desliza` — 13 meses consecutivos com
   valores diferentes entre si; assert que saem **2 linhas** (uma para
   o 12º mês, outra para o 13º); o acumulado do 13º mês é calculado no
   teste como o produto dos meses **2 a 13** (não 1 a 13, nem 1 a 12) e
   deve ser diferente do acumulado do 12º mês (produto dos meses 1 a
   12) — confirma que a janela desliza corretamente (descarta o mês
   mais antigo e inclui o novo), em vez de, por exemplo, sempre usar os
   12 primeiros meses do DataFrame de entrada.
10. `test_ptax_mensal_mes_completo` — um mês com dado em todos os dias
    úteis simulados (ex. 20 linhas); assert `dias_com_dado == 20`,
    `media`/`minimo`/`maximo` batem com os valores de entrada, e
    `fechamento` igual ao valor da última data do mês.
11. `test_ptax_mensal_mes_parcial` — um mês com poucas linhas (ex. só os
    5 primeiros dias úteis, simulando mês corrente incompleto); assert
    `dias_com_dado == 5` e que não há nenhuma coluna/sinalização extra
    além disso (confirma a decisão de não ter `mes_completo`).
12. `test_ptax_mensal_fechamento_ultimo_dia_util` — mês cujo último dia
    civil (ex. dia 31) **não** tem linha (fim de semana/feriado
    simulado), mas o dia anterior com dado (ex. dia 29) tem; assert
    `fechamento` igual ao valor do dia 29, não um erro nem o valor do
    dia 31 (que não existe na entrada).
13. `test_mudancas_selic_serie_ausente_retorna_vazio_com_dtypes` —
    `dados` sem nenhuma linha de código 432 (só outros códigos, ou
    DataFrame totalmente vazio com as colunas certas); assert saída
    vazia com as 4 colunas e dtypes documentados.
14. `test_ipca_acumulado_12m_serie_ausente_retorna_vazio_com_dtypes` —
    idem, para o código 433.
15. `test_ptax_mensal_serie_ausente_retorna_vazio_com_dtypes` — idem,
    para o código 1, com as 6 colunas documentadas.
16. `test_colunas_faltando_levanta_erro_analise` — `dados` sem a coluna
    `valor` (ou `codigo`, ou `data`); assert `ErroAnalise` para as três
    funções (parametrizado ou 3 testes separados).
17. `test_entrada_fora_de_ordem_produz_mesmo_resultado` — mesmos dados
    de um caso de sucesso (ex. o do teste 1), mas embaralhados antes de
    passar para a função; assert resultado idêntico ao da entrada já
    ordenada.
18. `test_dtypes_da_saida_das_tres_funcoes` — com dados reais (não
    vazios) para as três funções; assert os dtypes exatos de cada
    coluna de saída documentados acima.
19. `test_integracao_gravar_ler_e_analisar` — `duckdb.connect(":memory:")`
    + `persistencia.criar_tabela` + `persistencia.gravar` com um
    DataFrame sintético cobrindo os três códigos (432, 433, 1, com
    histórico suficiente para pelo menos uma mudança de Selic e uma
    janela de 12 meses de IPCA); depois `persistencia.ler(conexao)`;
    depois chama as três funções de análise sobre o resultado; assert
    que cada uma produz uma saída não vazia e com os dtypes corretos —
    prova de que o contrato real (`ler` → análise) funciona de ponta a
    ponta, sem rede e sem arquivo (`:memory:`).

## Convenções seguidas
- Nomes de função, variável e coluna em português, snake_case
  (`mudancas_selic`, `valor_anterior`, `dias_com_dado`), exceto os
  identificadores já em português usados por `persistencia`/`limpeza`
  (`codigo`, `data`, `valor`, `serie`).
- Dtypes sempre explícitos (`.astype(...)`), nunca por inferência —
  mesma convenção de `limpeza.py`/`persistencia.py`.
- Nenhum teste faz chamada de rede; só um teste de integração usa
  `duckdb.connect(":memory:")`, sem criar arquivo.
- Constantes de código de série nomeadas no módulo
  (`CODIGO_SELIC_META`, `CODIGO_IPCA_MENSAL`, `CODIGO_PTAX_VENDA`), sem
  números mágicos espalhados pelas funções.
