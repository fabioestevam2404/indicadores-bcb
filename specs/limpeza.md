# Especificação — Módulo de Limpeza

## Status
Segundo módulo do projeto. A extração (`src/indicadores/extracao.py`) já
está implementada; este módulo consome o tipo `ResultadoSerie` e o
registro `SERIES` de lá. Segue o mesmo formato de `specs/extracao.md`.

## Objetivo
Receber o resultado de `buscar_series` — um `dict[int, ResultadoSerie]` —
e devolver os dados **limpos e tipados**: datas convertidas para
`datetime64`, valores convertidos de string com vírgula para `float64`,
em um único DataFrame pandas em formato longo.

É uma **função pura**: não faz I/O, não acessa rede, não usa DuckDB. Não
recebe `client` nem `data_referencia`. Persistência (DuckDB) e qualquer
CLI ficam em módulos futuros, fora de escopo.

## Localização
```
src/indicadores/limpeza.py
```
Testes em `tests/test_limpeza.py`, montando `ResultadoSerie` à mão (sem
rede, sem mock HTTP — este módulo não faz chamadas).

## Contrato de entrada
`dict[int, ResultadoSerie]`, exatamente o tipo retornado por
`extracao.buscar_series`:

```python
@dataclass(frozen=True)
class ResultadoSerie:
    codigo: int
    dados: list[dict[str, str]] | None   # [{"data": "dd/mm/aaaa", "valor": "13,75"}, ...]
    erro: ErroExtracaoBCB | None
    # .sucesso -> bool (True se erro is None)
```

`SERIES: dict[int, str]` (código → nome) também é importado de
`extracao` e usado para nomear a coluna `serie`.

## API pública do módulo

### Estruturas de resultado
```python
@dataclass(frozen=True)
class Descarte:
    """Uma linha bruta que não pôde ser aproveitada."""
    codigo: int
    data_bruta: str
    valor_bruto: str
    motivo: str  # "data_invalida" | "valor_invalido" | "data_duplicada"

@dataclass(frozen=True)
class SerieIgnorada:
    """Uma série inteira ignorada por ter falhado na extração."""
    codigo: int
    motivo: str  # str(resultado.erro) — mensagem da exceção original

@dataclass(frozen=True)
class ResultadoLimpeza:
    dados: pd.DataFrame
    descartes: list[Descarte]
    series_ignoradas: list[SerieIgnorada]
```

Decisão registrada: em vez de só um DataFrame, ou uma tupla posicional
`(df, descartes)`, optou-se por um dataclass nomeado (`ResultadoLimpeza`)
com três campos explícitos. Motivo: a tupla exigiria lembrar a ordem; o
dataclass documenta o contrato no próprio tipo e permite adicionar campos
no futuro (ex.: contagem de linhas processadas) sem quebrar quem já usa
`resultado.dados`.

### Função principal
```python
def limpar(resultados: dict[int, ResultadoSerie]) -> ResultadoLimpeza:
    """Limpa e tipa os dados brutos de todas as séries.

    Para cada `ResultadoSerie`:
    - se `sucesso` é False: a série inteira é ignorada (registrada em
      `series_ignoradas`), sem interromper as demais;
    - se `sucesso` é True: cada item de `dados` é validado e convertido
      (ver "Regras de conversão"); itens inválidos são descartados
      (registrados em `descartes`), sem descartar a série inteira;
      duplicatas de data entre linhas totalmente válidas dentro da mesma
      série também viram descarte.

    Retorna `ResultadoLimpeza` com um único DataFrame contendo as linhas
    válidas de todas as séries bem-sucedidas, mais as listas de
    descartes e séries ignoradas. Nunca levanta exceção por causa de
    dado malformado — dado malformado sempre vira descarte, não erro.
    """
```

### Logging
```python
logger = logging.getLogger("indicadores.limpeza")
```
- Cada descarte gera `logger.warning(...)` incluindo `codigo`, o motivo e
  os valores brutos (data/valor) que causaram o descarte.
- Cada série ignorada gera `logger.warning(...)` incluindo `codigo` e o
  motivo (mensagem da exceção original).
- O logging é complementar, não substitui `descartes`/`series_ignoradas`
  como forma de verificação em teste: os testes devem checar as listas
  retornadas (estruturado, estável) e, em pelo menos um teste cada,
  também confirmar que o log foi emitido (via `caplog`).

## Regras de conversão

### Data
```python
def _parse_data(bruto: str) -> datetime | None:
    texto = bruto.strip()
    try:
        return datetime.strptime(texto, "%d/%m/%Y")
    except ValueError:
        return None
```
- Formato **sempre explícito** `"%d/%m/%Y"`, nunca inferido — evita
  qualquer ambiguidade dia/mês.
- Espaços em branco nas pontas são removidos antes do parse
  (`" 01/01/2020 "` → válido).
- Data vazia, mal formada ou impossível (ex.: `"31/02/2020"`,
  `"00/01/2020"`) → `None` → linha descartada com
  `motivo="data_invalida"`.

### Valor
A conversão de `valor` passa por **validação explícita de formato via
regex**, antes de qualquer chamada a `float()`. Isso evita tanto números
não finitos (`float()` nativo aceita `"nan"`, `"inf"`, `"-inf"`,
`"Infinity"`) quanto ambiguidade entre ponto decimal e separador de
milhar.

```python
PADRAO_MILHAR = re.compile(r"^-?\d{1,3}(\.\d{3})+(,\d+)?$", re.ASCII)
PADRAO_VIRGULA_DECIMAL = re.compile(r"^-?\d+,\d+$", re.ASCII)
PADRAO_INTEIRO = re.compile(r"^-?\d+$", re.ASCII)
PADRAO_PONTO_DECIMAL = re.compile(r"^-?\d+\.\d+$", re.ASCII)

def _parse_valor(bruto: str) -> float | None:
    texto = bruto.strip()

    if PADRAO_MILHAR.fullmatch(texto):
        # ponto(s) = separador de milhar; vírgula (se houver) = decimal.
        return float(texto.replace(".", "").replace(",", "."))
    if PADRAO_VIRGULA_DECIMAL.fullmatch(texto):
        return float(texto.replace(",", "."))
    if PADRAO_INTEIRO.fullmatch(texto):
        return float(texto)
    if PADRAO_PONTO_DECIMAL.fullmatch(texto):
        # só chega aqui se NÃO casou com o padrão de milhar acima —
        # ou seja, o(s) grupo(s) após o ponto não têm exatamente 3
        # dígitos, então o ponto é decimal, não milhar.
        return float(texto)

    return None
```

Todos os padrões usam `re.fullmatch` (a string inteira precisa casar,
não só um prefixo) e a flag `re.ASCII`, que restringe `\d` a apenas os
dígitos ASCII `0-9`. Sem essa flag, `\d` no Python casaria também com
dígitos de outros alfabetos Unicode (ex.: dígitos arábico-indianos como
`"١٢٣"`). Com `re.ASCII`, qualquer string contendo esses dígitos não
ASCII não casa com nenhum dos quatro padrões e é descartada como
`motivo="valor_invalido"`, em vez de ser aceita e potencialmente causar
comportamento surpreendente em `float()` mais adiante.

Ordem de checagem importa: `PADRAO_MILHAR` é testado **antes** de
`PADRAO_PONTO_DECIMAL`, porque os dois podem, à primeira vista, parecer
aplicáveis à mesma string com um único ponto e sem vírgula (ex.:
`"1.234"`). A diferença é o número de dígitos após o ponto: o padrão de
milhar exige grupos de **exatamente 3 dígitos** (`\d{3}`); o padrão de
ponto-decimal (fallback) só é alcançado quando o milhar não bateu.

Decisões registradas (ambiguidade ponto único, sem vírgula):
- **`"1.234"`** (um ponto, exatamente 3 dígitos depois) → casa com
  `PADRAO_MILHAR` → tratado como separador de milhar → **`1234.0`**.
  Decisão: seguir a convenção brasileira (ponto = milhar) quando a
  string é compatível com esse formato, mesmo sem vírgula decimal
  presente, conforme sugerido e aprovado pelo usuário.
- **`"5.25"`** (um ponto, 2 dígitos depois — não bate com `\d{3}`) → não
  casa com `PADRAO_MILHAR` → cai no fallback `PADRAO_PONTO_DECIMAL` →
  tratado como ponto decimal → **`5.25`**, não `525.0`.
- Strings que não casam com **nenhum** dos quatro padrões são
  descartadas com `motivo="valor_invalido"`. Isso inclui, entre outros:
  - `""`, `"-"` (sem dígitos);
  - `"nan"`, `"inf"`, `"-inf"`, `"Infinity"` (não são compostos só por
    dígitos/ponto/vírgula/sinal, então nenhuma regex casa — não chegam
    a ser passados para `float()`, então nunca viram valores não
    finitos na saída);
  - `"1,234.56"` (formato americano — vírgula de milhar seguida de
    ponto decimal, ordem inversa da convenção BR; não casa com
    `PADRAO_VIRGULA_DECIMAL` porque há um `.` depois da vírgula);
  - `"13, 75"` (espaço depois da vírgula quebra `PADRAO_VIRGULA_DECIMAL`,
    que exige dígito imediatamente após a vírgula);
  - `"1.23.4"` (dois pontos; não casa com `PADRAO_MILHAR` porque o
    primeiro grupo após o ponto tem 2 dígitos, não 3; não casa com
    `PADRAO_PONTO_DECIMAL` porque esse padrão permite só um ponto);
  - strings com dígitos não ASCII (ex.: `"١٢٣,45"`, dígitos
    arábico-indianos), por causa da flag `re.ASCII`.

### Prioridade quando data e valor estão inválidos ao mesmo tempo
Decisão confirmada pelo usuário: a validação de **data** é feita antes da
de **valor**. Se ambas falharem na mesma linha, o descarte é registrado
com `motivo="data_invalida"` (só um motivo por linha, não uma lista).

### Datas repetidas na mesma série
Decisão confirmada pelo usuário: **mantém a primeira ocorrência** de cada
par `(codigo, data)` — mas a deduplicação só é aplicada **entre linhas
totalmente válidas**, isto é, linhas cuja data **e** valor foram
convertidos com sucesso. Se a primeira ocorrência de uma data foi
descartada por `data_invalida` ou `valor_invalido`, ela nunca entra no
conjunto de candidatas à deduplicação; nesse caso, a **próxima**
ocorrência da mesma data que for totalmente válida é mantida
normalmente (não é tratada como duplicata, pois não há uma primeira
ocorrência válida concorrendo com ela).

Entre ocorrências válidas, a decisão continua sendo manter a primeira
(respeitando a ordem em que a API devolveu os itens) e descartar as
seguintes com `motivo="data_duplicada"`, independentemente de o `valor`
bruto ser igual ou diferente entre elas. Justificativa: o SGS devolve as
séries em ordem cronológica ascendente; em caso de anomalia com data
repetida, a primeira aparição válida é a mais conservadora para manter
como valor "oficial" da série.

## Regras de negócio (saída)
1. **Formato:** um único `pandas.DataFrame` em formato longo, colunas
   exatamente `["codigo", "serie", "data", "valor"]`, nessa ordem —
   ordem aprovada pelo usuário.
2. **Dtypes** (sempre, inclusive em DataFrame vazio, e sempre aplicados
   via conversão **explícita** na construção do DataFrame — não
   depender do dtype inferido por padrão do pandas, já que o pandas 3
   muda o dtype padrão de string de `object` para `str`/`StringDtype` e
   pode inferir datas com resolução diferente de nanossegundos):
   - `codigo` → `.astype("int64")`
   - `serie` → `.astype(object)` (explícito, para não herdar o novo
     dtype de string padrão do pandas 3 e manter `object` estável entre
     versões)
   - `data` → o parse de `"dd/mm/aaaa"` já aconteceu em `_parse_data`
     (via `datetime.strptime` com formato explícito), então a coluna já
     chega na construção do DataFrame como objetos `datetime` do Python.
     Não se usa `pd.to_datetime` nesse ponto — basta
     `.astype("datetime64[ns]")` explícito sobre a coluna já convertida,
     só para fixar a resolução em nanossegundos, independente da
     resolução que a versão do pandas instalada infira por padrão ao
     montar a coluna.
   - `valor` → `.astype("float64")`
   Isso vale tanto para o DataFrame com dados quanto para o DataFrame
   vazio retornado nos casos de entrada vazia/todas as séries com falha
   — ambos construídos com os mesmos quatro `astype` explícitos, nunca
   deixando o pandas inferir o dtype sozinho.
3. **Ordenação:** por `codigo` (ascendente) e depois por `data`
   (ascendente), com `reset_index(drop=True)` — sem manter o índice
   original.
4. **Nome da série (`serie`):** vem de `SERIES[codigo]` quando o código
   está no registro. Se o código **não** estiver em `SERIES` (ex.:
   alguém chamou `buscar_series(codigos=[999])` com um código ainda não
   cadastrado), usa o nome de fallback `f"serie_{codigo}"` e loga um
   `warning` avisando que o código está fora do registro. Isso nunca é
   tratado como erro — o dado ainda é limpo e incluído no DataFrame.
   Decisão confirmada pelo usuário.
5. **Entrada vazia ou todas as séries com falha:** retorna
   `ResultadoLimpeza(dados=<DataFrame vazio com as 4 colunas e os dtypes
   explícitos do item 2>, descartes=[], series_ignoradas=[...])`.
   "Vazio" nunca significa colunas ausentes ou dtypes genéricos
   (`object`/`str` em tudo por inferência) — o DataFrame vazio é
   construído com os mesmos `astype` explícitos usados no caso com
   dados.
6. **Série com falha na extração (`sucesso is False`):** inteira
   ignorada; motivo registrado é `str(resultado.erro)`. As demais séries
   seguem normalmente.
7. **Linha inválida dentro de uma série bem-sucedida:** só aquela linha
   é descartada; o resto da série continua no DataFrame.
8. Espaços em branco nas pontas de `data` e `valor` brutos são sempre
   removidos antes de qualquer validação (`strip()`); espaço interno
   inesperado (ex.: `"13, 75"`) não é tratado especialmente e resulta em
   falha da regex/`strptime` → descarte (mesmo caminho de erro, sem
   tratamento extra).

## Dependências
`requirements.txt` hoje contém `httpx`, `pytest`, `pytest-cov`, `ruff`.
Este módulo depende de `pandas`, que **ainda não está listado** — o
implementer precisa adicionar a linha `pandas` a `requirements.txt` como
parte da implementação (versão mais recente disponível — pandas 3, cujas
mudanças de dtype padrão já estão consideradas na seção de Dtypes acima).

## Fora de escopo
- Persistência em DuckDB (módulo futuro).
- CLI / orquestração de execução.
- Agregações e reamostragem de frequência (ex.: mensal → trimestral,
  forward-fill de meses sem dado). Este módulo devolve os dados no grão
  em que a API os devolveu, sem reamostrar nem preencher lacunas.
- Enriquecimento com metadados adicionais além de `codigo`/`serie`.
- Validação de plausibilidade de valor (ex.: Selic negativa é
  suspeita, mas não é rejeitada aqui — só formato inválido é rejeitado).
- Deduplicação entre séries diferentes que apontem para o mesmo
  indicador (não existe esse caso hoje; cada código é tratado de forma
  independente).

## Critérios de aceite
1. `limpar` é função pura: nenhuma chamada de rede, nenhum acesso a
   arquivo/DuckDB, resultado determinístico para a mesma entrada.
2. Saída sempre tem colunas `["codigo", "serie", "data", "valor"]`, nessa
   ordem, com os dtypes `int64`, `object`, `datetime64[ns]`, `float64`
   respectivamente, obtidos por conversão explícita — inclusive quando o
   DataFrame está vazio.
3. `valor` só é convertido para `float64` quando a string bruta (após
   `strip()`) casa (via `fullmatch`, com `re.ASCII`) com um dos quatro
   padrões regex documentados; strings fora desses padrões (incluindo
   `"nan"`, `"inf"`, `"-inf"`, `"Infinity"`, formato americano,
   pontuação malformada, dígitos não ASCII) são sempre descartadas,
   nunca convertidas.
4. A ambiguidade ponto-único-sem-vírgula é resolvida pela contagem de
   dígitos após o ponto: exatamente 3 dígitos → separador de milhar
   (`"1.234"` → `1234.0`); qualquer outra quantidade → ponto decimal
   (`"5.25"` → `5.25`).
5. `data` é convertida com formato explícito `%d/%m/%Y` em `_parse_data`
   (`datetime.strptime`), nunca inferido; a coluna do DataFrame só passa
   por `.astype("datetime64[ns]")` para fixar a resolução, sem segundo
   parse via `pd.to_datetime`.
6. Linha com data ou valor inválido é descartada (não presente em
   `dados`) e aparece em `descartes` com o `motivo` correto; a série
   continua com as demais linhas válidas.
7. Data duplicada entre duas linhas **totalmente válidas** na mesma
   série: só a primeira ocorrência válida fica em `dados`; a(s)
   seguinte(s) aparece(m) em `descartes` com `motivo="data_duplicada"`.
   Se a primeira ocorrência de uma data foi descartada por
   `valor_invalido`/`data_invalida`, a próxima ocorrência válida da
   mesma data é mantida, não tratada como duplicata.
8. Série com `sucesso is False`: não aparece em `dados`, aparece em
   `series_ignoradas`; as demais séries do mesmo `resultados` continuam
   normalmente.
9. Código fora de `SERIES`: dado é limpo normalmente, `serie` recebe o
   fallback `f"serie_{codigo}"`.
10. Entrada vazia (`{}`) ou todas as séries com falha: `dados` é
    DataFrame vazio com as colunas/dtypes explícitos corretos,
    `descartes == []`.
11. Saída ordenada por `codigo` e depois `data`, com índice resetado
    (`0, 1, 2, ...`) independentemente da ordem de iteração do dict de
    entrada.
12. Cada descarte e cada série ignorada gera pelo menos um registro de
    log (`logging`, nível `warning`), verificável via `caplog`.
13. Nenhum teste faz chamada de rede ou usa `httpx`/mocks HTTP — as
    entradas são `ResultadoSerie` construídos manualmente.
14. Cobertura de testes do módulo ≥ 80% (linha de base do projeto; a
    extração ficou em 100%, mas o mínimo combinado é 80%).

## Casos de teste (pytest)
1. `test_limpar_converte_data_e_valor_basicos` — uma série com
   `[{"data": "01/01/2020", "valor": "13,75"}]`; assert `data ==
   Timestamp("2020-01-01")` e `valor == 13.75` (`float64`).
2. `test_limpar_usa_formato_explicito_nao_infere_dia_mes` — item com
   `"data": "13/02/2020"` (dia 13, impossível como mês); assert que o
   resultado é `Timestamp("2020-02-13")` (confirma uso de `%d/%m/%Y`
   fixo, não inferência que poderia rejeitar ou trocar dia/mês).
3. `test_parse_valor_virgula_simples` — `"13,75"` → `13.75`.
4. `test_parse_valor_milhar_com_ponto_e_virgula` — `"1.234,56"` →
   `1234.56`.
5. `test_parse_valor_milhar_multiplo` — `"1.234.567,89"` →
   `1234567.89`.
6. `test_parse_valor_milhar_sem_virgula` — `"1.234"` (um ponto, 3
   dígitos depois, sem vírgula) → `1234.0` (decisão: tratado como
   milhar, não como decimal).
7. `test_parse_valor_ponto_decimal_isolado_nao_e_milhar` — `"5.25"` (um
   ponto, 2 dígitos depois) → `5.25`, **não** `525.0`.
8. `test_parse_valor_negativo` — `"-1,5"` → `-1.5`.
9. `test_parse_valor_vazio_e_traco_invalidos` — `""` e `"-"` →
   descartados com `motivo="valor_invalido"`.
10. `test_parse_valor_texto_nao_numerico_invalido` — `"N/D"`, `"abc"` →
    descartados com `motivo="valor_invalido"`.
11. `test_parse_valor_nao_finito_invalido` — `"nan"`, `"inf"`,
    `"-inf"`, `"Infinity"` → todos descartados com
    `motivo="valor_invalido"` (nenhum chega a virar `float` não finito
    na saída).
12. `test_parse_valor_formato_americano_invalido` — `"1,234.56"` →
    descartado com `motivo="valor_invalido"` (vírgula de milhar seguida
    de ponto decimal não é um padrão aceito).
13. `test_parse_valor_pontuacao_malformada_invalida` — `"1.23.4"` (dois
    pontos, grupos fora do padrão de milhar) → descartado com
    `motivo="valor_invalido"`.
14. `test_parse_valor_espaco_apos_virgula_invalido` — `"13, 75"` →
    descartado com `motivo="valor_invalido"`.
15. `test_parse_valor_digito_nao_ascii_invalido` — string com dígitos
    não ASCII (ex.: `"١٢٣,45"`, dígitos arábico-indianos) → descartado
    com `motivo="valor_invalido"` (confirma o efeito de `re.ASCII`).
16. `test_parse_data_invalida_ou_impossivel` — `""`, `"31/02/2020"` e
    `"00/01/2020"` (dia zero, impossível) → descartados com
    `motivo="data_invalida"`.
17. `test_limpar_espacos_em_branco_sao_removidos` — `" 01/01/2020 "` e
    `" 13,75 "` → convertidos normalmente (sem descarte).
18. `test_limpar_prioriza_motivo_data_invalida_quando_ambos_invalidos` —
    item com `data` e `valor` inválidos ao mesmo tempo; assert
    `motivo == "data_invalida"` (não uma lista, um só motivo).
19. `test_limpar_linha_invalida_nao_descarta_serie_inteira` — série com
    3 itens, 1 inválido; assert que `dados` tem as 2 linhas válidas e
    `descartes` tem 1 entrada.
20. `test_limpar_data_duplicada_entre_linhas_validas_mantem_primeira` —
    série com dois itens de mesma `data`, ambos com `valor` válido
    (iguais ou diferentes); assert que `dados` tem só a primeira
    ocorrência e `descartes` tem a segunda com `motivo="data_duplicada"`.
21. `test_limpar_data_duplicada_quando_primeira_invalida_mantem_segunda_valida`
    — série com dois itens de mesma `data`: o primeiro com `valor`
    inválido (ex.: `"abc"`), o segundo com `valor` válido; assert que
    `dados` contém a linha da **segunda** ocorrência (não é tratada como
    duplicata) e `descartes` tem só 1 entrada, com
    `motivo="valor_invalido"` referente à primeira ocorrência.
22. `test_limpar_serie_com_falha_e_ignorada_sem_interromper_outras` —
    `resultados` com 2 séries: uma `sucesso=True` com dados válidos,
    outra com `erro` preenchido; assert que `dados` só tem linhas da
    série bem-sucedida e `series_ignoradas` tem 1 item com o `codigo` e
    `motivo` da série que falhou.
23. `test_limpar_entrada_vazia_retorna_dataframe_vazio_com_dtypes` —
    `limpar({})`; assert `dados.empty`, colunas
    `["codigo", "serie", "data", "valor"]` e dtypes
    `int64`/`object`/`datetime64[ns]`/`float64` corretos,
    `descartes == []`, `series_ignoradas == []`.
24. `test_limpar_todas_series_falharam_retorna_dataframe_vazio` —
    `resultados` só com séries `sucesso=False`; assert `dados` vazio com
    dtypes corretos e `series_ignoradas` com todos os códigos.
25. `test_limpar_codigo_fora_do_registro_usa_nome_fallback` — `codigo`
    não presente em `SERIES` (ex.: `999`); assert
    `serie == "serie_999"`.
26. `test_limpar_ordena_por_codigo_e_data_com_indice_resetado` —
    `resultados` com 2 códigos em ordem "invertida" no dict e datas fora
    de ordem; assert ordenação final por `codigo` e `data`, colunas na
    ordem `["codigo", "serie", "data", "valor"]`, e `index` igual a
    `range(len(dados))`.
27. `test_limpar_loga_descarte_com_caplog` — item inválido; com
    `caplog.at_level(logging.WARNING)`, assert que existe um registro
    de log mencionando o `codigo` e o motivo do descarte.
28. `test_limpar_loga_serie_ignorada_com_caplog` — série com
    `sucesso=False`; com `caplog`, assert que existe um registro de log
    mencionando o `codigo` da série ignorada.
29. `test_limpar_dtypes_da_saida` — com dados reais (não vazios), assert
    `dados.dtypes` bate exatamente com
    `{"codigo": "int64", "serie": "object", "data": "datetime64[ns]",
    "valor": "float64"}`.

## Convenções seguidas
- Nomes de função, variável e coluna em português, snake_case, conforme
  convenção do repositório (`data`, `valor`, `codigo`, `serie`).
- Nenhuma chamada de rede nos testes (não se aplica neste módulo — ele
  não usa `httpx`); entradas são sempre `ResultadoSerie` construídos à
  mão.
- Datas em `datetime64[ns]` na saída deste módulo (não mais string
  `dd/mm/aaaa` como na extração); conversão para outro formato de
  serialização (ex.: ISO em texto) é responsabilidade de quem consome o
  DataFrame, se necessário.
- Dtypes sempre aplicados via conversão explícita (`.astype(...)`),
  nunca deixados para a inferência padrão do pandas, para manter
  compatibilidade estável entre versões (em particular pandas 3).
