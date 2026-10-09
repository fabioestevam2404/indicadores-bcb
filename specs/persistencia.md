# Especificação — Módulo de Persistência (DuckDB)

## Status
Terceiro módulo do projeto. `extracao.py` e `limpeza.py` já estão
implementados. Este módulo consome `ResultadoLimpeza.dados` (o
`pandas.DataFrame` produzido por `limpeza.limpar`), com colunas
`["codigo", "serie", "data", "valor"]` e dtypes `int64`, `object`,
`datetime64[ns]`, `float64` (conferido lendo `src/indicadores/limpeza.py`).
Segue o mesmo formato de `specs/extracao.md` e `specs/limpeza.md`.

Atualização (09/10/2026): `abrir_conexao` ganhou o parâmetro opcional
`somente_leitura` (decisão 12 de `specs/relatorio.md`), usado por
`--so-relatorio`. É a única mudança; o restante do módulo não muda.

## Objetivo
Gravar os dados limpos em DuckDB, em uma tabela única, com upsert por
`(codigo, data)` — cada execução busca de novo os últimos 5 anos, e o
BCB pode revisar valores publicados anteriormente, então a mesma
combinação `(codigo, data)` pode chegar de novo com um `valor` diferente
e precisa **atualizar**, não duplicar.

Este módulo não faz limpeza (isso já aconteceu em `limpeza.limpar`) e
não orquestra o pipeline de ponta a ponta (extração → limpeza →
persistência) — isso é uma etapa separada, fora de escopo aqui.

## Localização
```
src/indicadores/persistencia.py
```
Testes em `tests/test_persistencia.py`, todos usando
`duckdb.connect(":memory:")` (exceto os testes específicos de
`abrir_conexao`, que usam `tmp_path` do pytest para um arquivo real em
diretório temporário). Nenhum teste cria arquivo `.duckdb` fora de
`tmp_path`.

## Contrato de entrada
`pandas.DataFrame` com exatamente as colunas `codigo` (`int64`), `serie`
(`object`/`str`), `data` (`datetime64[ns]`), `valor` (`float64`) — o
mesmo formato de `ResultadoLimpeza.dados`. `descartes` e
`series_ignoradas` da limpeza **não** são entrada deste módulo e não são
persistidos (decisão aprovada pelo usuário — descartes ficam fora de
escopo).

## Esquema da tabela (decisão aprovada pelo usuário)
```sql
CREATE TABLE IF NOT EXISTS indicadores (
    codigo         INTEGER   NOT NULL,
    serie          VARCHAR   NOT NULL,
    data           DATE      NOT NULL,
    valor          DOUBLE    NOT NULL,
    atualizado_em  TIMESTAMP NOT NULL,
    PRIMARY KEY (codigo, data)
)
```
Tabela única `indicadores`, chave primária composta `(codigo, data)`.
`atualizado_em` é `TIMESTAMP` **sem fuso** (naive) — ver seção
"Origem de `atualizado_em`".

## API pública do módulo

### Constantes
```python
CAMINHO_PADRAO: Path = Path("dados/indicadores.duckdb")
```
Caminho padrão do arquivo DuckDB (o `.gitignore` já ignora `*.duckdb`,
então este caminho pode ser usado sem risco de commitar o arquivo).

### Exceção
```python
class ErroPersistencia(Exception):
    """Erro de uso deste módulo, detectável antes de tocar o banco."""
```
Levantada apenas pelas validações que este módulo consegue checar
**proativamente**, antes de qualquer instrução SQL de escrita (ver
"Validação da entrada"). Erros que o próprio DuckDB relata durante a
execução (ex.: violação de `NOT NULL`, conexão fechada, disco cheio, ou
`BEGIN` em uma transação já aberta) não são envolvidos em
`ErroPersistencia` — são deixados propagar com o tipo de exceção
original do driver `duckdb`, depois do `ROLLBACK`. Decisão: envolver
esses erros mascararia a causa raiz reportada pelo DuckDB sem agregar
informação útil.

### Logging
```python
logger = logging.getLogger("indicadores.persistencia")
```
Uso mínimo e focado: o único evento logado por este módulo é uma
eventual falha do próprio `ROLLBACK` dentro do tratamento de erro de
`gravar` (via `logger.exception`, nível `ERROR` com traceback) — ver
seção "Transação". Não há log de sucesso nem de outras operações;
decisão de manter o logging restrito ao único cenário em que uma
informação (a falha do rollback) seria perdida silenciosamente se não
fosse registrada, já que a exceção que propaga para quem chamou
`gravar` é sempre a original, não a do rollback.

### Estrutura de resultado
```python
@dataclass(frozen=True)
class ResultadoGravacao:
    inseridos: int
    atualizados: int
    total: int  # == inseridos + atualizados == len(dados)
```
Decisão registrada: **é viável e barato** medir `inseridos` vs.
`atualizados` de forma confiável, sem depender de contagem de linhas
afetadas por um `INSERT ... ON CONFLICT` (que o driver `duckdb` não
expõe de forma granular). A estratégia é contar, **antes** do upsert,
quantas das chaves `(codigo, data)` recebidas já existem na tabela (via
`JOIN` entre a tabela e o DataFrame registrado) — esse número vira
`atualizados`; o restante (`total - atualizados`) vira `inseridos`. Por
isso o resultado não é só um `int`, e sim `ResultadoGravacao`. Ver
"Limitações conhecidas" quanto à atomicidade dessa contagem.

### Abertura de conexão
```python
def abrir_conexao(
    caminho: str | Path = CAMINHO_PADRAO,
    *,
    somente_leitura: bool = False,
) -> duckdb.DuckDBPyConnection:
    """Abre (ou cria) o arquivo DuckDB em `caminho`.

    Padrão (`somente_leitura=False`): cria o diretório pai de `caminho`
    se não existir (`Path(caminho).parent.mkdir(parents=True,
    exist_ok=True)`), exceto quando `caminho == ":memory:"` (sem
    diretório pai a criar), e abre em leitura/escrita
    (`duckdb.connect(str(caminho))`). Não chama `criar_tabela`
    automaticamente — isso é responsabilidade de quem orquestra (chamar
    `criar_tabela` uma vez por conexão antes de `gravar`).

    `somente_leitura=True`: abre com `duckdb.connect(str(caminho),
    read_only=True)`. **Não** cria o diretório pai nem o arquivo: se o
    arquivo não existir, o erro nativo do DuckDB propaga. Qualquer escrita
    nessa conexão (`INSERT`, `CREATE TABLE`, `gravar`, `criar_tabela`)
    levanta o erro nativo do DuckDB. Não suportado com `":memory:"`
    (erro nativo do DuckDB; sem tratamento nem teste).
    """
```
Decisão registrada (decisão 12 de `specs/relatorio.md`): o parâmetro é
**keyword-only** e o default `False` preserva integralmente o
comportamento anterior, então nenhum chamador existente muda. O único
usuário de `somente_leitura=True` é `--so-relatorio`, para que "nunca
escreve no banco" seja garantido pelo próprio DuckDB (em leitura/escrita
ele pode reaplicar o WAL e fazer checkpoint, alterando o arquivo mesmo
sem nenhum `INSERT`). Não verificado: o comportamento de `read_only` com
um `.wal` pendente (execução anterior encerrada à força); se a abertura
falhar, quem chama trata como erro de abertura.

### Criação de tabela (idempotente)
```python
def criar_tabela(conexao: duckdb.DuckDBPyConnection) -> None:
    """Executa `CREATE TABLE IF NOT EXISTS indicadores (...)`.

    Idempotente: chamar múltiplas vezes na mesma conexão não recria nem
    apaga dados existentes.
    """
```

### Gravação (upsert)
```python
def gravar(
    conexao: duckdb.DuckDBPyConnection,
    dados: pd.DataFrame,
    *,
    atualizado_em: datetime | None = None,
) -> ResultadoGravacao:
    """Grava `dados` na tabela `indicadores`, upsert por (codigo, data).

    - `atualizado_em`: valor único aplicado a **todas** as linhas desta
      chamada. Se `None`, usa o horário atual de Brasília (naive, sem
      tzinfo) — ver "Origem de `atualizado_em`". Injetável para testes
      determinísticos, no mesmo espírito de `data_referencia` em
      `extracao.py`.
    - Valida as colunas de `dados` antes de tocar o banco (ver
      "Validação da entrada"); levanta `ErroPersistencia` se inválidas.
    - Se `dados` estiver vazio (`dados.empty`): não abre transação, não
      toca o banco, retorna `ResultadoGravacao(0, 0, 0)` imediatamente.
    - Caso contrário, **abre sua própria transação** (`BEGIN`) e a
      fecha (`COMMIT` ou `ROLLBACK`) antes de retornar — ver
      "Transação" para a restrição de uso decorrente disso (não chamar
      `gravar` com uma transação já aberta na mesma conexão) e para o
      passo a passo completo, incluindo o controle por flag do
      `unregister` e a supressão de falha no `ROLLBACK`.
    - Não chama `criar_tabela` implicitamente. Se a tabela não existir,
      o `INSERT` falha com o erro nativo do DuckDB (tabela não
      encontrada), sem `ErroPersistencia` — decisão confirmada pelo
      usuário: `gravar` tem uma única responsabilidade (escrever), a
      criação da tabela é uma etapa explícita e separada de quem
      orquestra.
    """
```

### Leitura (helper de conveniência)
```python
def ler(
    conexao: duckdb.DuckDBPyConnection,
    codigos: Iterable[int] | None = None,
) -> pd.DataFrame:
    """Lê a tabela `indicadores`, opcionalmente filtrando por `codigos`.

    Retorna DataFrame com colunas
    `["codigo", "serie", "data", "valor", "atualizado_em"]` — decisão
    confirmada pelo usuário: `ler` expõe `atualizado_em` também, não só
    as 4 colunas de negócio, porque é metadado útil tanto para depurar
    quanto para os testes verificarem revisão de valor. Ordenado por
    `codigo` e `data`, com dtypes explícitos aplicados via
    `.astype(...)` (mesmo raciocínio de `limpeza.py`): `codigo` →
    `int64`, `serie` → `object`, `data` → `datetime64[ns]`, `valor` →
    `float64`, `atualizado_em` → `datetime64[ns]`. Decisão: não confiar
    no dtype que o driver `duckdb` decide devolver por padrão ao
    converter para pandas (ex.: uma coluna `INTEGER` do DuckDB pode virar
    `int32` ou `int64` dependendo da versão do pacote `duckdb`
    instalada — suposição não confirmada, por isso a conversão explícita
    aqui reforça um contrato estável independente da versão). Isso vale
    inclusive para tabela vazia: `ler` sobre uma tabela sem linhas
    devolve DataFrame vazio com as mesmas 5 colunas e os mesmos dtypes
    explícitos, nunca colunas ausentes ou dtypes genéricos.
    """
```
Incluído na API pública porque é útil tanto para os testes (verificar o
que foi persistido) quanto para consumo futuro (ex.: uma API ou
dashboard lendo o DuckDB diretamente).

## Origem de `atualizado_em`
```python
from indicadores.extracao import FUSO_BRASILIA

def _agora_brasilia() -> datetime:
    """Horário atual de Brasília, naive (sem tzinfo)."""
    return datetime.now(FUSO_BRASILIA).replace(tzinfo=None)
```
Decisões registradas:
- Reaproveita `FUSO_BRASILIA` (já definido em `extracao.py`, UTC-3
  fixo, sem horário de verão) — não duplica a constante.
- O valor é **naive** (sem `tzinfo`), não `TIMESTAMPTZ`: a coluna da
  tabela é `TIMESTAMP` (decisão já aprovada pelo item 1), e misturar um
  `datetime` com tzinfo em uma coluna naive seria uma fonte de confusão
  silenciosa (a hora gravada seria a "hora de parede" de Brasília, sem
  registrar de qual fuso ela veio — mas como todo o projeto assume
  Brasília fixo, isso é aceitável e consistente com `_hoje()` da
  extração, que também devolve um valor "ingênuo" quanto a fuso depois
  de calculado).
- É **um único valor por chamada** de `gravar` (calculado uma vez antes
  da transação, não recalculado linha a linha), garantindo que todas as
  linhas gravadas na mesma chamada tenham o mesmo `atualizado_em`.
- É **injetável** via parâmetro `atualizado_em: datetime | None = None`
  de `gravar`, no mesmo espírito de `data_referencia` em
  `buscar_serie`/`buscar_series` — isso é o que torna os testes de
  "revisão de valor atualizando `atualizado_em`" determinísticos.

## Estratégia de inserção
- Usa `conexao.register(nome, dataframe)` para expor o DataFrame como
  uma relação consultável via SQL, e faz `INSERT ... SELECT ... FROM
  <relação> ON CONFLICT ... DO UPDATE`, em vez de iterar linha a linha
  — mais rápido e é o padrão idiomático da API Python do `duckdb` para
  trabalhar com DataFrames pandas.
- `CAST(data AS DATE)` explícito no `SELECT`: mesmo que a coluna
  `data` do DataFrame já seja `datetime64[ns]` com hora sempre
  `00:00:00` (vinda de `_parse_data` em `limpeza.py`, que só lida com
  `dd/mm/aaaa`), o `CAST` garante que o valor gravado na tabela seja do
  tipo `DATE` do DuckDB, independentemente de como o driver decidir
  mapear `datetime64[ns]` por padrão (suposição não confirmada sobre a
  versão exata instalada do pacote `duckdb` — o `CAST` explícito remove
  essa dependência).
- A relação temporária registrada via `conexao.register(...)` só é
  removida com `conexao.unregister(...)` se o próprio `register` tiver
  dado certo (controlado por uma flag, ex. `registrado = True` logo
  após o `register` bem-sucedido) — ver "Transação" para o porquê.
- Suposição registrada, não confirmada por falta de acesso à versão
  exata do `duckdb` que será instalada: a sintaxe `BEGIN`/`COMMIT`/
  `ROLLBACK` via `conexao.execute("BEGIN TRANSACTION")` etc. funciona
  na versão instalada. Se a versão instalada não suportar essa sintaxe
  (improvável — é suportada desde as primeiras versões públicas do
  DuckDB), o implementer deve adaptar para os métodos equivalentes da
  API Python (`conexao.begin()` / `.commit()` / `.rollback()`, se
  existirem) sem mudar o comportamento nem a assinatura de `gravar`.

## Transação
`gravar` **abre e fecha a própria transação**: emite `BEGIN` logo no
início (depois de validar a entrada e confirmar que `dados` não está
vazio) e, no caminho de sucesso, `COMMIT` no final. Isso é uma escolha
deliberada e traz uma restrição de uso, **documentada aqui, não um
bug**: chamar `gravar` em uma conexão que **já está com uma transação
aberta** (por exemplo, se quem orquestra já tiver executado `BEGIN`
manualmente antes de chamar `gravar`) faz o `BEGIN` do próprio `gravar`
falhar com o erro nativo do DuckDB (ex.: "already in a transaction"),
**sem nenhum efeito colateral** — a transação externa pré-existente
permanece como estava, e nada da chamada de `gravar` chega a ser
executado. Essa restrição é coberta por teste (ver casos de teste) e
não é tratada como erro deste módulo nem envolvida em
`ErroPersistencia`.

Qualquer exceção depois do `BEGIN` (erro do DuckDB, ex.: violação de
restrição `NOT NULL` que a validação deste módulo não pega — ver
"Validação da entrada") dispara `ROLLBACK` antes de a exceção ser
relançada, garantindo que nenhuma linha da chamada fique parcialmente
persistida.

Estrutura de controle (registrada explicitamente, para não deixar
ambíguo onde cada passo entra):
1. `BEGIN`.
2. `try`: `conexao.register(...)` (se der certo, marca uma flag interna
   `registrado = True`); conta as chaves `(codigo, data)` já existentes
   (`atualizados`); executa o `INSERT ... ON CONFLICT`; `COMMIT`.
3. `except`: tenta `ROLLBACK`. **Se o próprio `ROLLBACK` falhar**, essa
   falha é registrada via `logger.exception` no logger
   `indicadores.persistencia` e **suprimida** — ela nunca substitui nem
   se sobrepõe à exceção original. Em seguida, a exceção **original**
   (a que motivou o `except`) é relançada, sempre.
4. `finally`: `conexao.unregister(...)` da relação temporária, mas
   **somente se `registrado` for `True`** — isto é, somente se o
   `conexao.register(...)` do passo 2 tiver dado certo. Se o próprio
   `register` falhar, não existe relação para desregistrar, e chamar
   `unregister` nesse caso geraria um segundo erro (mascarando o
   primeiro), então ele simplesmente não é chamado.

Esse desenho evita duas coisas: (1) tentar desregistrar uma relação que
nunca chegou a ser registrada; e (2) deixar uma falha do `ROLLBACK`
(rara, mas possível — ex. conexão fechada por fora no meio do processo)
se sobrepor à exceção original que motivou o rollback. A exceção que
sempre propaga para quem chamou `gravar`, nesses cenários de erro, é a
**original**.

## Limitações conhecidas
A contagem de `atualizados` (quantas chaves `(codigo, data)` do
DataFrame já existem na tabela, feita por um `SELECT`/`JOIN` **antes**
do `INSERT ... ON CONFLICT` — ver "Estrutura de resultado") **não é
atômica** em relação a outra conexão gravando no mesmo arquivo DuckDB
ao mesmo tempo: entre o momento da contagem e o momento do upsert,
outra conexão concorrente poderia inserir ou remover uma das mesmas
chaves, tornando `inseridos`/`atualizados` imprecisos nesse cenário
concorrente (o upsert em si — a integridade dos dados gravados — não é
afetado, só a decomposição do número reportado em `ResultadoGravacao`
poderia ficar levemente incorreta). Decisão: **risco aceito**, porque
este pipeline roda como um processo local, um de cada vez (não há
múltiplos processos gravando concorrentemente no mesmo arquivo
`.duckdb` no uso previsto do projeto); resolver isso exigiria bloqueio
explícito ou uma abordagem transacional mais sofisticada, desproporcional
ao caso de uso atual.

## Validação da entrada
`gravar` valida, **antes de abrir a transação**:
1. **Colunas:** `dados` precisa conter exatamente as colunas
   `{"codigo", "serie", "data", "valor"}` (por nome; a ordem das colunas
   não importa aqui — diferente do contrato de saída de `limpeza.py`,
   que fixa ordem). Faltando alguma ou havendo colunas extras
   inesperadas → `ErroPersistencia` com mensagem listando o que falta
   e/ou sobra.
2. **Duplicidade de `(codigo, data)` dentro do próprio DataFrame:**
   verificada com `dados.duplicated(subset=["codigo", "data"]).any()`.
   Se houver → `ErroPersistencia`, **sem** tentar gravar nada. Decisão:
   `limpeza.limpar` já deduplica por série, então isso não deveria
   ocorrer em uso normal; mas um único `INSERT ... ON CONFLICT` com
   múltiplas linhas de origem colidindo na mesma chave tem
   comportamento não claramente definido/testado no DuckDB para esse
   caso (mais de uma linha de origem por conflito), então esta spec
   prefere falhar cedo e de forma explícita a depender desse
   comportamento.

O que **não** é validado por `gravar` (decisão confirmada pelo usuário,
para manter a validação simples e não redundante com o contrato de
`limpeza.py`):
- Dtypes exatos das colunas (`limpeza.limpar` é a fonte de verdade dos
  tipos; validar de novo aqui seria redundante e frágil a variações
  equivalentes, ex. `int32` vs. `int64`).
- Presença de nulos (`None`/`NaN`) nos valores — se `dados` tiver um
  nulo em uma coluna `NOT NULL` da tabela, o próprio DuckDB rejeita a
  linha no `INSERT` com um erro de violação de restrição, que aciona o
  `ROLLBACK` descrito acima. Isso é usado propositalmente como cenário
  de teste do rollback (ver casos de teste), usando `serie=None`, não
  `valor=None` — ver justificativa abaixo.
- Especificamente, um `NaN` em `valor` (coluna `float64`) **não** é
  tratado por este módulo: `limpeza.limpar` nunca produz `NaN` em
  `valor` (linhas com valor não conversível viram `Descarte`, nunca
  chegam ao DataFrame de saída), então este módulo não precisa se
  proteger contra esse caso na fronteira de entrada normal. Além disso,
  mesmo que um `NaN` chegasse aqui, não haveria garantia de que o
  DuckDB o rejeitaria: `NaN` é um `DOUBLE` válido (não é `NULL`), então
  o comportamento seria gravar o `NaN` normalmente, não disparar erro
  — por isso o cenário de teste de rollback usa `serie=None` (coluna
  `VARCHAR`), não `valor=None`/`NaN` (ver próxima seção).
- Se já existe uma transação aberta na conexão recebida (ver
  "Transação"): não é uma validação deste módulo, é uma restrição de
  uso que o próprio `BEGIN` do DuckDB reforça nativamente.

## Regras de negócio
1. Upsert por `(codigo, data)`: revisão do BCB para uma data já gravada
   atualiza `serie`, `valor` e `atualizado_em`; nunca duplica a linha.
2. `criar_tabela` é idempotente e não é chamada implicitamente por
   `gravar` (decisão confirmada pelo usuário) — precisa ser chamada
   explicitamente por quem orquestra, uma vez por conexão/arquivo.
3. `dados` vazio nunca é erro: retorna `ResultadoGravacao(0, 0, 0)` sem
   tocar o banco.
4. Descartes e séries ignoradas da limpeza nunca chegam a este módulo
   nem são persistidos.
5. Conexão é sempre injetada por quem chama (`duckdb.DuckDBPyConnection`);
   este módulo nunca abre uma conexão global implícita — `abrir_conexao`
   existe só como helper opcional para quem quiser usar o caminho
   padrão em arquivo.
6. `gravar` nunca deixa a tabela em estado parcialmente escrito: ou tudo
   da chamada é commitado, ou nada é (rollback), e a relação temporária
   registrada é sempre removida quando chegou a ser registrada
   (`finally` + flag), em qualquer um dos dois caminhos.
7. `gravar` sempre abre sua própria transação; não é compatível com ser
   chamada dentro de uma transação já aberta pelo chamador na mesma
   conexão (restrição de uso documentada, ver "Transação").
8. Falha do próprio `ROLLBACK` (cenário raro) nunca é a exceção que
   chega a quem chamou `gravar` — é logada e suprimida; a exceção
   original sempre prevalece.
9. `abrir_conexao(..., somente_leitura=True)` nunca cria diretório nem
   arquivo e nunca permite escrita; sem o parâmetro, o comportamento é o
   de sempre (decisão 12 de `specs/relatorio.md`).

## Dependências
`requirements.txt` hoje contém `httpx`, `pytest`, `pytest-cov`, `ruff`,
`pandas`. Este módulo depende também de `duckdb`, que **ainda não está
listado** — o implementer precisa adicionar a linha `duckdb` a
`requirements.txt`.

## Fora de escopo
- Script/CLI que orquestra extração → limpeza → persistência de ponta a
  ponta (etapa separada, futura).
- Persistir `descartes` ou `series_ignoradas` da limpeza.
- Migrações de schema além de `CREATE TABLE IF NOT EXISTS` (ex.: `ALTER
  TABLE` para evoluir o schema no futuro).
- Particionamento, índices adicionais além da chave primária, ou
  otimizações de performance para volumes muito maiores que "5 anos ×
  algumas séries".
- Retenção/expurgo de dados antigos.
- Qualquer leitura/agregação além do `ler(...)` simples descrito acima
  (agregações ficam para um módulo de análise futuro, fora do escopo
  deste projeto de ingestão, se vier a existir).
- Suporte a múltiplos processos gravando concorrentemente no mesmo
  arquivo DuckDB (ver "Limitações conhecidas").

## Critérios de aceite
1. `criar_tabela` é idempotente: chamável várias vezes na mesma conexão
   sem erro e sem apagar dados já gravados.
2. `gravar` insere corretamente linhas novas: após a chamada, `ler(...)`
   (ou uma query direta) mostra os dados com os valores esperados.
3. Reexecutar `gravar` com o **mesmo** DataFrame não duplica linhas
   (contagem de linhas da tabela permanece igual); `ResultadoGravacao`
   reporta `inseridos == 0` e `atualizados == total` na segunda chamada.
4. Revisão de valor: gravar novamente uma linha com o mesmo
   `(codigo, data)` mas `valor` diferente atualiza `valor` **e**
   `atualizado_em` na tabela (não cria segunda linha).
5. `dados` vazio: `gravar` retorna `ResultadoGravacao(0, 0, 0)` e não
   altera a tabela (nem sequer abre transação, verificável indiretamente
   por não haver efeito colateral).
6. `dados` com colunas faltando ou inesperadas levanta `ErroPersistencia`
   antes de qualquer escrita (tabela permanece inalterada).
7. `dados` com `(codigo, data)` duplicado internamente levanta
   `ErroPersistencia` antes de qualquer escrita.
8. Erro durante a transação (ex.: violação de `NOT NULL` não coberta
   pela validação proativa) resulta em `ROLLBACK`: nenhuma linha daquela
   chamada fica persistida, a relação temporária é removida mesmo assim
   quando chegou a ser registrada (`finally` + flag), e a exceção
   original do `duckdb` propaga (não é envolvida em `ErroPersistencia`).
9. Tipos gravados no banco: `data` é `DATE` e `valor` é `DOUBLE`
   (verificável via `typeof(...)` em SQL ou inspecionando o schema da
   tabela).
10. `abrir_conexao` com um caminho cujo diretório pai não existe cria
    esse diretório (verificável com `tmp_path` do pytest) e devolve uma
    conexão utilizável.
11. `ler` devolve DataFrame com as colunas e dtypes explícitos
    documentados (incluindo `atualizado_em`), ordenado por `codigo` e
    `data`, inclusive quando a tabela está vazia.
12. Todos os testes usam `duckdb.connect(":memory:")`, exceto os de
    `abrir_conexao`, que usam um arquivo real dentro de `tmp_path`.
13. Se o próprio `ROLLBACK` falhar dentro do tratamento de erro de
    `gravar`, essa falha é logada (`logger.exception`,
    `indicadores.persistencia`) e suprimida; a exceção que propaga para
    quem chamou `gravar` é sempre a original, nunca a do `ROLLBACK`.
14. `conexao.unregister(...)` só é chamado quando o `conexao.register(...)`
    correspondente tiver dado certo antes (controle por flag) — nunca
    numa relação que nunca chegou a ser registrada.
15. Chamar `gravar` em uma conexão com uma transação já aberta levanta o
    erro nativo do DuckDB no `BEGIN`, sem alterar a tabela.
16. Cobertura de testes do módulo ≥ 80%.
17. `abrir_conexao(caminho, somente_leitura=True)` abre em modo
    `read_only` (escrita levanta o erro nativo do `duckdb`), não cria
    diretório nem arquivo inexistente, e o default (`False`) preserva o
    comportamento do critério 10.

## Casos de teste (pytest)
1. `test_criar_tabela_e_idempotente` — chama `criar_tabela` duas vezes
   na mesma conexão `:memory:`; sem erro; grava uma linha entre as duas
   chamadas e confirma que a segunda chamada não apaga a linha gravada.
2. `test_gravar_insere_linhas_novas` — `criar_tabela` + `gravar` com um
   DataFrame de 2 linhas (`atualizado_em` injetado, fixo); assert
   `ResultadoGravacao(inseridos=2, atualizados=0, total=2)` e que
   `ler(conexao)` devolve as 2 linhas, conferindo **linha a linha** que
   `valor`, `data` e `serie` batem exatamente com os dados de entrada
   (não só a contagem de linhas).
3. `test_gravar_reexecucao_nao_duplica` — grava o mesmo DataFrame duas
   vezes seguidas; assert que a contagem de linhas da tabela não muda
   entre a 1ª e a 2ª chamada, e que a 2ª chamada retorna
   `inseridos == 0` e `atualizados == total`.
4. `test_gravar_revisao_atualiza_valor_e_atualizado_em` — grava uma
   linha com `atualizado_em=t1`; grava de novo o mesmo `(codigo, data)`
   com `valor` diferente e `atualizado_em=t2` (`t2 > t1`, ambos
   injetados); assert que `ler(conexao)` mostra o novo `valor` e
   `atualizado_em == t2` (não `t1`), e que a tabela continua com 1 linha
   só para aquela chave.
5. `test_gravar_dataframe_vazio_nao_faz_nada` — DataFrame vazio com as
   colunas certas (0 linhas); assert `ResultadoGravacao(0, 0, 0)` e que
   a tabela continua com a contagem de linhas de antes da chamada.
6. `test_gravar_colunas_faltando_levanta_erro_persistencia` — DataFrame
   sem a coluna `valor`; assert `ErroPersistencia` e que a tabela
   permanece com a contagem de linhas de antes (nada foi escrito).
7. `test_gravar_colunas_inesperadas_levanta_erro_persistencia` —
   DataFrame com uma coluna extra além das 4 esperadas; assert
   `ErroPersistencia`.
8. `test_gravar_duplicata_interna_levanta_erro_persistencia` — DataFrame
   com duas linhas de mesmo `(codigo, data)` (mesmo dentro da mesma
   chamada); assert `ErroPersistencia`, tabela inalterada.
9. `test_gravar_rollback_em_erro_do_banco` — DataFrame com 2 linhas
   válidas e 1 linha com `serie=None` (nulo em coluna `VARCHAR NOT
   NULL`, não pego pela validação proativa) misturadas; assert que a
   chamada levanta a exceção nativa do `duckdb` (não `ErroPersistencia`)
   e que, depois do erro, a tabela **não** contém nenhuma das linhas da
   chamada (nem as que seriam válidas) — confirma `ROLLBACK` efetivo,
   não escrita parcial. Usa `serie=None`, não `valor=None`: em uma
   coluna `float64`, o pandas converte `None` em `NaN`, e `NaN` é um
   `DOUBLE` válido para o DuckDB (não é `NULL`), então esse cenário
   poderia não disparar erro nenhum; `serie` é uma coluna `object`, onde
   `None` vira `NULL` de forma garantida e viola o `NOT NULL` da tabela.
10. `test_gravar_tipos_gravados_sao_date_e_double` — depois de um
    `gravar` bem-sucedido, `conexao.execute("SELECT typeof(data),
    typeof(valor) FROM indicadores LIMIT 1").fetchone()` retorna
    `("DATE", "DOUBLE")`.
11. `test_abrir_conexao_cria_diretorio_pai` — usa `tmp_path / "sub" /
    "indicadores.duckdb"` (diretório `sub` não existe ainda); chama
    `abrir_conexao(caminho)`; assert que o diretório foi criado e que a
    conexão devolvida é utilizável (ex.: `criar_tabela` funciona nela).
12. `test_ler_filtra_por_codigos` — grava linhas de 2 códigos
    diferentes; `ler(conexao, codigos=[<um dos códigos>])` devolve só as
    linhas daquele código.
13. `test_ler_retorna_dtypes_explicitos` — depois de gravar, `ler(...)`
    tem dtypes `int64`/`object`/`datetime64[ns]`/`float64`/
    `datetime64[ns]` nas 5 colunas, mesmo que a versão do `duckdb`
    instalada devolvesse algo diferente por padrão.
14. `test_ler_ordenado_por_codigo_e_data` — grava fora de ordem; assert
    que `ler(...)` devolve ordenado por `codigo`, `data`.
15. `test_gravar_atualizado_em_default_usa_hora_de_brasilia` — chama
    `gravar` sem passar `atualizado_em` (mockando/monkeypatching
    `_agora_brasilia` ou `datetime.now` para um instante conhecido);
    assert que a linha gravada tem `atualizado_em` igual ao valor
    esperado em horário de Brasília, naive (sem tzinfo).
16. `test_gravar_remove_relacao_temporaria_no_sucesso_e_no_erro` —
    verifica, tanto após uma chamada de `gravar` bem-sucedida quanto
    após uma que aciona `ROLLBACK` (cenário do teste 9), que a relação
    temporária registrada não continua acessível na conexão depois da
    chamada (ex.: uma nova chamada de `gravar` reaproveitando o mesmo
    nome de relação não levanta erro de "já registrada").
17. `test_ler_tabela_vazia_retorna_dataframe_vazio_com_dtypes` —
    `criar_tabela` sem nenhum `gravar`; `ler(conexao)` devolve DataFrame
    vazio (`.empty`) com as 5 colunas
    (`codigo, serie, data, valor, atualizado_em`) e os dtypes explícitos
    documentados.
18. `test_gravar_falha_no_rollback_preserva_erro_original` — usa uma
    conexão fake/dublê (ex.: um wrapper em torno da conexão real do
    `duckdb`, ou um objeto `Mock` com os métodos necessários) cujo
    método correspondente a `ROLLBACK` levanta uma exceção própria
    quando chamado; provoca um erro real dentro da transação (ex.: o
    mesmo cenário de `serie=None` do teste 9); assert que a exceção que
    propaga para quem chamou `gravar` é a **original** (a de violação de
    `NOT NULL`), não a da falha do `ROLLBACK`, e que, via `caplog`,
    existe um registro de log (nível `ERROR`, de `logger.exception`)
    documentando a falha do `ROLLBACK`.
19. `test_gravar_falha_no_register_nao_chama_unregister` — usa uma
    conexão fake/dublê cujo `register(...)` levanta uma exceção; assert
    que a chamada de `gravar` propaga essa exceção e que `unregister(...)`
    **nunca é chamado** (verificável com um espião/`Mock` sobre o método
    `unregister` da conexão fake, confirmando 0 chamadas).
20. `test_gravar_com_transacao_ja_aberta_levanta_erro_sem_alterar_tabela`
    — em uma conexão `:memory:` com `criar_tabela` já executado, chama
    `conexao.execute("BEGIN TRANSACTION")` manualmente antes de chamar
    `gravar`; assert que `gravar` levanta o erro nativo do `duckdb` (não
    `ErroPersistencia`) e que a tabela `indicadores` permanece sem
    nenhuma linha da chamada depois do erro.
21. `test_abrir_conexao_somente_leitura` — arquivo real em `tmp_path`;
    definido por inteiro como caso 58 de `specs/relatorio.md` (conexão
    somente leitura lê mas rejeita escrita; caminho inexistente não cria
    arquivo nem diretório; default preserva o comportamento do caso 11).

## Convenções seguidas
- Nomes de função, variável e coluna em português, snake_case
  (`gravar`, `criar_tabela`, `abrir_conexao`, `atualizado_em`), exceto
  identificadores da API do driver (`duckdb.DuckDBPyConnection`) e da
  biblioteca padrão.
- Nenhum teste cria arquivo `.duckdb` fora de `tmp_path` (todos usam
  `:memory:`, exceto os de `abrir_conexao`).
- Dtypes sempre aplicados de forma explícita nas fronteiras de
  entrada/saída deste módulo (mesma convenção de `limpeza.py`), nunca
  deixados para a inferência padrão do driver `duckdb` ou do pandas.
- Suposições sobre a versão do `duckdb` que será instalada (sintaxe de
  transação, dtype de conversão para pandas) estão registradas
  explicitamente nas seções acima, já que não há forma de confirmá-las
  sem a versão exata instalada no ambiente do implementer.
- Logging mínimo e focado (só a falha do `ROLLBACK`), no logger
  `indicadores.persistencia`, seguindo o padrão de `logging.getLogger`
  já usado em `limpeza.py`.
