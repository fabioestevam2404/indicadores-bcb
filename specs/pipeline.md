# Especificação — Pipeline de Ponta a Ponta (`python -m indicadores`)

## Status
Quarto módulo do projeto. `extracao.py`, `limpeza.py` e `persistencia.py`
já estão implementados e testados (lidos diretamente de
`src/indicadores/` para confirmar as assinaturas reais usadas abaixo).
Este é o primeiro módulo que **orquestra** os outros três; até aqui cada
um era isolado e sem dependência dos demais.

Atualização (09/10/2026): a CLI ganhou a geração do relatório HTML
(`--relatorio`, `--sem-relatorio`, `--so-relatorio`) e o exit code `4`.
O comportamento do relatório (conteúdo, escrita, log, precedência dos exit
codes) está **definido em `specs/relatorio.md`**, seção "Integração na
CLI"; esta spec registra apenas os pontos em que a CLI do pipeline muda e
aponta para lá em vez de duplicar os detalhes.

Atualização (09/10/2026, revisão de código): `--so-relatorio` passa a abrir
o banco em **somente leitura** e uma falha apenas ao imprimir a mensagem de
sucesso do relatório não altera o exit code. Decisões 12 e 14 de
`specs/relatorio.md`; os pontos afetados abaixo (Argumentos, passos 2a e
7.3, exit code `4`) apontam para lá.

Assinaturas reais confirmadas (resumo, não reproduzido por inteiro):
- `extracao.SERIES: dict[int, str]`, `extracao.criar_client() -> httpx.Client`,
  `extracao.FUSO_BRASILIA`,
  `extracao.buscar_series(codigos=None, *, client=None, data_referencia=None,
  esperar=time.sleep) -> dict[int, ResultadoSerie]`.
- `limpeza.limpar(resultados: dict[int, ResultadoSerie]) -> ResultadoLimpeza`,
  com `ResultadoLimpeza(dados: pd.DataFrame, descartes: list[Descarte],
  series_ignoradas: list[SerieIgnorada])`, colunas de `dados` =
  `["codigo", "serie", "data", "valor"]`.
- `persistencia.CAMINHO_PADRAO`, `persistencia.abrir_conexao(caminho=CAMINHO_PADRAO)`
  (a partir da decisão 12 de `specs/relatorio.md` aceita também
  `*, somente_leitura: bool = False`; ver `specs/persistencia.md`),
  `persistencia.criar_tabela(conexao)`,
  `persistencia.gravar(conexao, dados, *, atualizado_em=None) -> ResultadoGravacao`,
  com `ResultadoGravacao(inseridos, atualizados, total)`.

## Objetivo
Encadear `buscar_series` → `limpar` → `criar_tabela` + `gravar` num único
comando, `python -m indicadores`, e imprimir um resumo legível em
português: séries gravadas (com inseridos/atualizados), descartes por
motivo, e séries ignoradas (com o erro).

Este módulo não implementa nenhuma regra nova de extração, limpeza ou
persistência — só orquestra, na ordem certa, com as dependências
(conexão, client HTTP) sempre injetadas para permitir testes sem rede e
sem arquivo real.

Depois do resumo, a CLI gera o relatório HTML a partir do banco (a menos
que `--sem-relatorio`), ou só o relatório com `--so-relatorio` — ver
`specs/relatorio.md`. `executar` e `formatar_resumo` não mudam.

## Localização
```
src/indicadores/pipeline.py     # orquestração pura, testável
src/indicadores/__main__.py     # CLI fina (argparse, logging, I/O)
```
Testes em `tests/test_pipeline.py` (função `executar`, com
`httpx.MockTransport` e `duckdb.connect(":memory:")`) e
`tests/test_main.py` (função `main`, com `tmp_path` para o banco e um
client mockado injetado via parâmetro só de teste — ver "CLI").

## `pipeline.py`: orquestração pura

### Estrutura de resultado
```python
@dataclass(frozen=True)
class ResumoPipeline:
    gravacoes: dict[int, ResultadoGravacao]
    nomes: dict[int, str]        # codigo -> nome da série, só para os códigos em `gravacoes`
    descartes: list[Descarte]
    series_ignoradas: list[SerieIgnorada]
```
Decisão registrada: `nomes` guarda o nome de série já resolvido por
`limpeza.limpar` (`SERIES[codigo]` ou o fallback `f"serie_{codigo}"`),
lido diretamente da coluna `serie` do DataFrame limpo — em vez de
recalcular a mesma lógica de fallback aqui, o que arriscaria os dois
lugares divergirem no futuro. Só cobre os códigos presentes em
`gravacoes` (isto é, que tiveram ao menos uma linha válida após a
limpeza); para séries ignoradas (que nunca chegam à limpeza), quem
formata o resumo resolve o nome separadamente (ver "Formatação do
resumo").

### Função de orquestração
```python
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

    1. `criar_tabela(conexao)` — sempre chamada aqui (idempotente); ver
       decisão abaixo sobre por que `executar`, e não `gravar`, chama
       `criar_tabela`.
    2. `resultados = buscar_series(codigos, client=client,
       data_referencia=data_referencia, esperar=esperar)`.
    3. `resultado_limpeza = limpar(resultados)`.
    4. Um único `atualizado_em` para toda a chamada: se não informado,
       `datetime.now(FUSO_BRASILIA).replace(tzinfo=None)` (reaproveita a
       constante `FUSO_BRASILIA` de `extracao.py`, mesmo padrão já usado
       em `persistencia.py` — não importa a função privada
       `persistencia._agora_brasilia`, calcula localmente com a mesma
       constante pública).
    5. Para cada `codigo` presente em `resultado_limpeza.dados`
       (ordenados, `sorted(dados["codigo"].unique())`): filtra as linhas
       daquele código (`dados[dados["codigo"] == codigo]`) e chama
       `gravar(conexao, subconjunto, atualizado_em=<valor do passo 4>)`,
       guardando o `ResultadoGravacao` em `gravacoes[codigo]` e o nome
       (`subconjunto["serie"].iloc[0]`) em `nomes[codigo]`.
    6. Retorna `ResumoPipeline(gravacoes, nomes, resultado_limpeza.descartes,
       resultado_limpeza.series_ignoradas)`.

    Não abre nem fecha `conexao` nem `client` — ambos são sempre
    injetados por quem chama (produção: `__main__.main`; testes: o
    próprio teste). Se `client` for `None`, `buscar_series` cria e fecha
    seu próprio client internamente (comportamento já existente em
    `extracao.py`, reaproveitado sem mudança).
    """
```

Decisão registrada (confirmada pelo usuário) — **gravação por série, uma
transação por código, não uma chamada única para todo o DataFrame**:
`persistencia.gravar` aceita qualquer DataFrame no formato certo e faria
upsert de todas as séries em uma única transação se chamado uma única
vez com `resultado_limpeza.dados` inteiro. Mas o objetivo pede o resumo
**por série** (inseridos/atualizados de cada uma), e `ResultadoGravacao`
é uma contagem agregada de uma chamada só — não haveria como decompô-la
de volta por série depois de uma chamada única. Por isso `executar`
chama `gravar` **uma vez por código**, filtrando o subconjunto de linhas
daquele código. Efeito colateral aceito e registrado: cada série vira
sua própria transação (não há mais uma transação única para o batch
inteiro); isso é consistente com o espírito de "falha de uma série não
devia automaticamente contaminar as demais" já usado em
`extracao.buscar_series` e `limpeza.limpar`, e o volume de dados deste
projeto (poucas séries, até milhares de linhas cada) não torna isso um
problema de performance.

Consequência explícita dessa decisão, registrada para não ficar
implícita: se `gravar` levanta uma exceção de banco na série **N**
(dentro do laço do passo 5), as séries **anteriores** ao código N nesse
laço já foram commitadas com sucesso (cada uma em sua própria
transação, já concluída antes de chegar em N) — elas **permanecem
gravadas**. A exceção de N propaga por `executar` até `main`, que não
chega a montar nem imprimir `ResumoPipeline`/`formatar_resumo` (ver
"Passo a passo de `main`", passo 6), e o processo termina com exit code
`3`. Isso é aceitável porque o upsert é idempotente: reexecutar o
pipeline depois de corrigido o problema apenas atualiza de novo as
séries já commitadas (sem duplicar, `atualizados == total` para elas) e
tenta gravar de novo as que faltaram — não há necessidade de desfazer
manualmente o que já foi persistido antes do erro.

Decisão registrada — **`executar` chama `criar_tabela`, `gravar` não
chama** (papéis mantidos como já definidos em `specs/persistencia.md`):
`executar` é o orquestrador de mais alto nível disponível antes da CLI,
então é o lugar natural para garantir que a tabela exista antes do
primeiro `gravar`, sem exigir que quem chama `executar` se lembre de
chamar `criar_tabela` à parte. `gravar` continua sem chamar
`criar_tabela` implicitamente (regra já registrada em
`specs/persistencia.md`, não reaberta aqui).

Decisão registrada — **séries sem nenhuma linha válida após a limpeza**:
se uma série teve `sucesso=True` na extração mas **todas** as suas
linhas foram descartadas na limpeza (ex.: período inteiro com valores
malformados), esse código não aparece em `dados["codigo"].unique()`,
então não aparece em `gravacoes` nem em `nomes`, e também não aparece em
`series_ignoradas` (a extração não falhou). Esse caso fica visível
apenas indiretamente, através de `descartes` (que lista cada linha
descartada com seu motivo). Não há uma terceira lista dedicada a esse
cenário — decisão: manter só as duas listas já produzidas por
`limpeza.limpar`, sem inventar uma categoria nova só para a orquestração.

### Formatação do resumo
```python
def formatar_resumo(resumo: ResumoPipeline) -> str:
    """Formata `resumo` como texto simples em português, para stdout.

    Função pura (sem I/O), testável por comparação de string. Usa
    `extracao.SERIES` para resolver o nome de séries ignoradas (que não
    passam por `limpeza.limpar` e por isso não têm nome em
    `resumo.nomes`), com o mesmo fallback `f"serie_{codigo}"` usado em
    `limpeza.py`, para consistência visual entre as duas listas.
    """
```
Formato (exemplo ilustrativo, com as três séries reais deste projeto —
432 Selic meta, 433 IPCA mensal, 1 dólar PTAX venda; números plausíveis,
não um contrato de bytes exato — os testes comparam por conteúdo
relevante, ex. presença de substrings ou comparação linha a linha
combinada com dados de entrada controlados, não um "golden file"
frágil):
```
Séries gravadas:
  432 (selic_meta): 12 inseridos, 1808 atualizados
  433 (ipca_mensal): 3 inseridos, 57 atualizados

Descartes por motivo:
  data_invalida: 0
  valor_invalido: 2
  data_duplicada: 0

Séries ignoradas:
  1 (dolar_ptax_venda): série 1: status HTTP 500 após 3 tentativas
```
Decisões de formato:
- "Séries gravadas" lista `codigo (nome): X inseridos, Y atualizados`,
  ordenada por `codigo`; se `gravacoes` estiver vazio, imprime uma linha
  `(nenhuma)`.
- "Descartes por motivo" sempre lista os **três motivos conhecidos**
  (`data_invalida`, `valor_invalido`, `data_duplicada`), nessa ordem
  fixa, com a contagem (inclusive `0`) — decisão: saída previsível e
  fácil de comparar entre execuções, em vez de omitir motivos com
  contagem zero.
- "Séries ignoradas" lista `codigo (nome): <motivo>` (o `motivo` de
  `SerieIgnorada`, que já é `str(erro)`); se vazio, imprime uma linha
  `(nenhuma)`.
- Vai inteiramente para **stdout** (via `print` em `__main__.main`).
  Mensagens de log (`logging`, dos módulos `indicadores.limpeza`,
  `indicadores.persistencia`, e deste módulo) vão para **stderr**
  (configurado por `__main__._configurar_logging`), nunca para stdout —
  separação decidida para permitir redirecionar/capturar o resumo
  (stdout) separadamente dos logs (stderr) em uso real de linha de
  comando.
- A linha `Relatório gravado em: <caminho>` (quando o relatório é gerado)
  vem **depois** do resumo, separada por uma linha em branco; seu formato
  é definido em `specs/relatorio.md` ("Log e saída padrão") e não faz
  parte de `formatar_resumo`. Uma falha de I/O ao imprimi-la (com o
  arquivo já gravado) não escapa nem muda o exit code: vira `WARNING` no
  stderr (decisão 14 de `specs/relatorio.md`).

## `__main__.py`: CLI fina

### Argumentos
```
python -m indicadores [--banco CAMINHO] [--series COD [COD ...]]
                       [--data-referencia AAAA-MM-DD] [-v | -vv]
                       [--relatorio CAMINHO]
                       [--sem-relatorio | --so-relatorio]
```
- `--banco CAMINHO` (`type=Path`, `metavar="CAMINHO"`, default
  `persistencia.CAMINHO_PADRAO` = `dados/indicadores.duckdb`) — o
  `metavar` explícito é só para a mensagem de ajuda/uso do `argparse`
  mostrar `--banco CAMINHO` em vez do nome da variável interna
  (`--banco BANCO`, que o `argparse` geraria por padrão a partir do
  `dest`).
- `--series COD [COD ...]` (`type=int`, `nargs="+"`, default `None` →
  `executar(codigos=None)` → todas as séries de `SERIES`). Não restringe
  os códigos aceitos ao registro `SERIES` (o mesmo comportamento
  permissivo de `buscar_series`/`limpar`, que já lidam com código
  desconhecido via fallback de nome); só exige que sejam inteiros —
  entrada não numérica (`--series abc`) faz o `argparse` recusar com
  código de saída **2**, mensagem em stderr, antes de qualquer execução.
- `--data-referencia AAAA-MM-DD` (opcional; `type=` uma função que faz
  `date.fromisoformat(texto)` e relança como
  `argparse.ArgumentTypeError` em caso de formato ou data inválidos —
  ex.: `"2024-13-01"`, `"01/01/2024"` — fazendo o `argparse` recusar com
  código de saída **2**, sem executar nada).
- `-v`/`--verbose` (`action="count"`, default `0`): `0` → `WARNING`,
  `1` (`-v`) → `INFO`, `2+` (`-vv`) → `DEBUG`, aplicado por
  `_configurar_logging` — ver subseção dedicada abaixo (não usa
  `logging.basicConfig`).
- `--relatorio CAMINHO` (`type=Path`, `metavar="CAMINHO"`, default
  `relatorio.html`, relativo ao diretório de trabalho; mesma semântica de
  `--banco`): onde gravar o relatório HTML. Ver `specs/relatorio.md`,
  "Argumentos novos".
- `--sem-relatorio` (`store_true`): não gera o relatório. `--relatorio`
  junto com ele é aceito e ignorado.
- `--so-relatorio` (`store_true`): só regenera o relatório a partir do
  banco existente — sem client HTTP, sem `executar`, sem rede, sem
  escrever no banco, que é aberto em **somente leitura**
  (`abrir_conexao(..., somente_leitura=True)`, decisão 12 de
  `specs/relatorio.md`; comportamento completo em `specs/relatorio.md`,
  "Passo a passo").
- Regras de exclusão e erro de uso (todas com exit `2`, antes de qualquer
  recurso ser aberto):
  - `--sem-relatorio` e `--so-relatorio` são **mutuamente exclusivos**
    (`add_mutually_exclusive_group`; recusado pelo `argparse`).
  - `--so-relatorio` com `--series` ou `--data-referencia` →
    `parser.error`: esses argumentos só fazem sentido para a extração.

### Configuração de logging (decisão registrada)
```python
def _configurar_logging(verbose: int) -> None:
    """Configura o logger do pacote `indicadores` para esta execução."""
```
**Não usa `logging.basicConfig`**: `basicConfig` só tem efeito se o
*root logger* ainda não tem nenhum handler, e no ambiente de testes o
próprio `pytest` já registra handlers no root logger antes de qualquer
teste rodar — nesse cenário `basicConfig` simplesmente não faz nada, e
o nível de log não mudaria em teste algum (`-v`/`-vv` pareceriam não ter
efeito). Por isso, `_configurar_logging`:
1. Obtém `logger = logging.getLogger("indicadores")` (o logger do
   pacote — `indicadores.limpeza`, `indicadores.persistencia` e
   `indicadores.pipeline` propagam para ele por herança de nome).
2. Define `logger.setLevel(...)` conforme `verbose`
   (`0`→`WARNING`, `1`→`INFO`, `≥2`→`DEBUG`).
3. Remove um handler anterior **marcado por este próprio módulo** (ex.:
   guardando uma referência/atributo identificável no handler
   adicionado, como `handler.name = "indicadores.cli"`, e procurando por
   esse nome antes de adicionar um novo) — decisão: evita acumular
   handlers duplicados (e, portanto, linhas de log repetidas) se `main`
   for chamado mais de uma vez no mesmo processo (cenário comum em
   testes, que chamam `main(...)` várias vezes na mesma sessão do
   `pytest`).
4. Cria um novo `handler = logging.StreamHandler(sys.stderr)`, aplica
   `handler.setFormatter(logging.Formatter("%(levelname)s %(name)s:
   %(message)s"))` (formato fixo, decisão registrada — não configurável
   pela CLI), marca `handler.name = "indicadores.cli"` e adiciona esse
   handler ao logger do pacote.
5. **Não** define `logger.propagate = False` — `propagate` continua
   `True` (o default), para que o `caplog` do pytest (que captura via um
   handler anexado ao root/aos loggers, dependendo da configuração)
   continue enxergando os registros normalmente nos testes. Ver
   "Limitações conhecidas" quanto ao efeito colateral disso fora do uso
   normal de linha de comando.

Decisão adicional registrada: `sys.stderr` é resolvido **no momento da
chamada** de `_configurar_logging` (dentro de `main`), não capturado uma
vez em tempo de importação do módulo — isso é necessário para o
`capsys` do pytest conseguir capturar a saída, já que `capsys` substitui
`sys.stderr` antes de cada teste, e um handler criado antes dessa
substituição (ex. se `sys.stderr` fosse lido no nível do módulo, fora de
qualquer função) apontaria para o stream errado.

### Ponto de entrada do módulo
```python
if __name__ == "__main__":
    sys.exit(main())
```
No final de `__main__.py`, sem argumentos explícitos passados a `main()`
(usa `sys.argv` implicitamente, via `argparse` com `argv=None`) —
padrão usual de ponto de entrada Python, que permite `python -m
indicadores ...` funcionar e também permite importar `main` para testes
sem executar essa linha (protegida pelo `if __name__ == "__main__"`).

### Injeção de client para testes (decisão registrada)
```python
def main(argv: list[str] | None = None, *, client: httpx.Client | None = None) -> int:
    ...
```
`client` é um parâmetro **keyword-only, não exposto por nenhuma flag da
CLI** — existe só para os testes chamarem `main(["--banco", str(caminho)],
client=client_mockado)` com um `httpx.Client(transport=MockTransport(...))`,
sem precisar de monkeypatch em `extracao.criar_client`. Decisão: preferir
um parâmetro explícito de teste a monkeypatch, porque é mais direto de
ler no teste e não exige conhecer o caminho interno de importação de
`criar_client`. Se `client` não for informado (uso real via linha de
comando), `main` chama `extracao.criar_client()` para criar um.

**Em qualquer um dos dois casos, `main` é responsável por fechar esse
client** (o que criou ou o que recebeu) — decisão confirmada pelo
usuário: um único caminho de código, sem ramificação "só fecho o que eu
criei", para manter a lógica de encerramento simples e sempre correta.
Um teste que passa `client` para `main` deve considerá-lo consumido
após a chamada (não reaproveitar esse client depois).

### Passo a passo de `main`
```python
def main(argv: list[str] | None = None, *, client: httpx.Client | None = None) -> int:
    """Ponto de entrada de `python -m indicadores`. Retorna o exit code."""
```
1. `args = _parse_args(argv)` — pode levantar `SystemExit(2)` (via
   `argparse`, entrada inválida, inclusive as combinações inválidas das
   flags de relatório) antes de qualquer recurso ser aberto.
2. `_configurar_logging(args.verbose)`.
2a. Se `args.so_relatorio`: delega a `_so_relatorio(args)` e retorna o
   exit code dele (`0` ou `4`), sem criar client nem chamar `executar` —
   passo a passo completo em `specs/relatorio.md` ("Passo a passo"). O
   banco é aberto em **somente leitura**
   (`abrir_conexao(args.banco, somente_leitura=True)`, decisão 12), depois
   de checar que o arquivo existe. Os passos 3 a 7 abaixo valem para o
   fluxo normal, que continua abrindo o banco em leitura/escrita.
3. `meu_client = None` e `conexao = None`, **antes** do `try` — para o
   `finally` sempre saber se há algo para fechar, mesmo que a própria
   criação do client falhe.
4. `try`: `meu_client = client if client is not None else criar_client()`
   (**decisão registrada**: a criação do client, quando não injetado,
   acontece **dentro** do `try`, não antes — assim, se `criar_client()`
   levantar uma exceção, ela cai no mesmo `except` do passo 6, sem
   deixar nada aberto para o `finally` tentar fechar incorretamente);
   em seguida `conexao = abrir_conexao(args.banco)`; chama
   `executar(...)` com os argumentos repassados de `args`; guarda o
   `ResumoPipeline`.
5. `except Exception`: loga o erro (`logger.exception`, logger
   `indicadores.pipeline`) e define o retorno como `3` — cobre falha em
   `criar_client()` (client não informado e não foi possível criar um),
   falha em `abrir_conexao` (ex.: caminho inválido) e qualquer exceção
   não tratada dentro de `executar` (ex.: erro de banco durante
   `gravar`, exceção nativa do `duckdb`). Sem resumo impresso nesse
   caminho (não há `ResumoPipeline` válido) e **sem tentar o relatório**
   — ver também a consequência registrada na seção sobre gravação por
   série e `specs/relatorio.md` ("Integração na CLI", decisão 5).
6. `finally`: fecha `meu_client` **se não for `None`** (foi criado ou
   recebido com sucesso) e fecha `conexao` **se não for `None`** (isto
   é, se `abrir_conexao` chegou a suceder). Roda tanto no caminho de
   sucesso quanto no de erro; nunca tenta fechar algo que nunca chegou a
   existir (ex.: se `criar_client()` falhou, `meu_client` continua
   `None` e não é fechado).
7. Caminho de sucesso (sem exceção no passo 4), executado na cláusula
   `else` do `try` — portanto **antes** do `finally` do passo 6, com a
   conexão ainda aberta, que o relatório reaproveita:
   1. imprime `formatar_resumo(resumo)` via `print(...)` (stdout);
   2. calcula o exit code: `0` se `resumo.series_ignoradas` estiver
      vazio, `1` caso contrário;
   3. **se não for `--sem-relatorio`**, gera o relatório a partir do banco
      com a mesma conexão (`_gerar_relatorio(conexao, args.relatorio)`,
      definido em `specs/relatorio.md`); se a geração falhar (a função
      devolve `False`) e o exit code calculado for `0`, ele vira `4`; se
      for `1`, continua `1` (a falha do relatório fica só no log, nível
      `ERROR` no stderr). Se o relatório foi gravado e só a impressão da
      linha `Relatório gravado em: ...` falhou (`OSError`/`UnicodeError`),
      `_gerar_relatorio` devolve `True` com um `WARNING` no stderr e o
      exit code **não muda** (decisão 14 de `specs/relatorio.md`);
   4. retorna o exit code.

### Exit codes (decisão registrada)
| Código | Situação |
|---|---|
| `0` | Execução completa, nenhuma série ignorada (todas as séries solicitadas foram extraídas com sucesso; descartes de linhas individuais **não** afetam o código). Se o relatório foi pedido, ele também foi gravado. |
| `1` | Execução completa, mas **pelo menos uma** série foi ignorada (falhou na extração). As demais séries, se houver, foram gravadas normalmente. **Prevalece** sobre uma eventual falha do relatório (que fica só no log). |
| `2` | Erro de uso da CLI: argumento inválido, detectado pelo `argparse` (`SystemExit(2)` nativo, repassado sem modificação), inclusive as combinações inválidas das flags de relatório. |
| `3` | Erro inesperado durante a execução: falha ao criar o client HTTP, falha ao abrir a conexão, ou qualquer falha dentro de `executar` (incluindo erro de banco em `gravar`). O relatório **não é tentado**. |
| `4` | Os dados foram gravados normalmente (a execução seria `0`), mas o relatório **não pôde ser gerado ou gravado**; ou, em `--so-relatorio`, o relatório não pôde ser gerado (inclusive banco inexistente). Falha só ao **imprimir** a mensagem de sucesso, com o arquivo já gravado, não conta (decisão 14 de `specs/relatorio.md`). Definição, motivos e precedência (`3` > `1` > `4` > `0`) em `specs/relatorio.md`, "Integração na CLI". |

Decisão explícita, **confirmada pelo usuário**, sobre "e se **todas** as
séries falharem na extração?": mesmo código `1` usado para "algumas
falharam" — não existe um quarto código para "todas falharam". Motivo:
do ponto de vista de quem chama o comando (ex.: um agendador externo), a
distinção relevante é "sucesso total" (`0`) vs. "sucesso parcial ou
total, precisa checar o resumo/log" (`1`); differenciar "algumas" de
"todas" no exit code não muda a ação que quem chama precisa tomar
(investigar o resumo), então não se registrou um código a mais só para
essa distinção. (O código `4`, acrescentado depois, é para outra situação:
falha do relatório, não da extração.)

Decisão explícita: **descartes nunca mudam o exit code**. Uma linha
descartada é um evento esperado e tratado (dado malformado pontual),
não uma falha de execução — só `series_ignoradas` (falha da extração de
uma série inteira), erros inesperados (`3`) e a falha do relatório (`4`,
quando o resto daria `0`) afetam o código de saída.

## Regras de negócio
1. `executar` nunca abre nem fecha `conexao`; `client`, se `None`, é
   criado e fechado internamente por `buscar_series` (comportamento já
   existente de `extracao.py`, não duplicado aqui).
2. `executar` sempre chama `criar_tabela(conexao)` antes de qualquer
   `gravar` (idempotente, seguro chamar toda execução).
3. `main` sempre fecha o client e a conexão que efetivamente chegou a
   existir, inclusive quando `executar` levanta uma exceção (via
   `finally`), inclusive quando o client foi injetado por quem chamou
   `main` (decisão confirmada pelo usuário — ver "Injeção de client
   para testes"), e sem tentar fechar o que nunca chegou a ser criado
   (ex.: falha do próprio `criar_client()`).
4. O resumo (stdout) e os logs (stderr) são sempre canais separados; a
   configuração de logging usa o logger do pacote (`"indicadores"`), não
   `logging.basicConfig`, para funcionar de forma confiável também sob
   `pytest`.
5. Exit code reflete só falhas de série inteira (`series_ignoradas`),
   erros inesperados e a falha do relatório (`4`, só quando o resto
   daria `0`) — nunca descartes de linhas individuais; e "todas as séries
   falharam" usa o mesmo código `1` de "algumas falharam" (decisão
   confirmada pelo usuário). Falha do relatório nunca impede nem desfaz a
   gravação dos dados (ver `specs/relatorio.md`).
6. Nenhuma opção de linha de comando configura retry/timeout/backoff da
   extração — esses continuam fixos nas constantes de `extracao.py`
   (fora de escopo mudar isso aqui).
7. A gravação é feita em uma transação por série (decisão confirmada
   pelo usuário) — um erro de banco numa série não desfaz séries
   anteriores já commitadas na mesma execução (ver consequência
   registrada acima).

## Limitações conhecidas
- **Logs duplicados em uso embutido:** como `propagate` continua `True`
  em `_configurar_logging` (necessário para o `caplog` do pytest
  enxergar os registros — ver "Configuração de logging"), se `main` for
  chamado de dentro de uma aplicação maior que **já tem seus próprios
  handlers configurados no root logger**, os registros de
  `indicadores.*` podem aparecer **duplicados**: uma vez pelo handler
  que `_configurar_logging` adiciona ao logger `"indicadores"`, e de
  novo pelo(s) handler(s) do root, para o qual o registro também
  propaga. Decisão: risco aceito, não corrigido nesta spec — não
  acontece no uso normal via linha de comando (`python -m indicadores`,
  onde não há nenhuma aplicação maior por cima) nem para quem usa só
  `executar` diretamente (que nunca chama `_configurar_logging`, então
  nunca adiciona handler nenhum).
- **Duas execuções simultâneas apontando para o mesmo `--banco`:** o
  DuckDB é single-writer por arquivo; se duas instâncias da CLI forem
  executadas ao mesmo tempo com o mesmo caminho de arquivo, a segunda
  provavelmente falha ao tentar abrir o arquivo (já bloqueado pela
  primeira) e termina com exit code `3` (ou `4`, se for `--so-relatorio`).
  Decisão: risco aceito, não tratado com retry nem lock próprio — rodar a
  CLI de novo depois (após a primeira execução terminar) é seguro, graças
  ao upsert idempotente de `persistencia.gravar`.
- **`print` do resumo sem proteção de I/O:** a decisão 14 de
  `specs/relatorio.md` protege só a mensagem de sucesso do relatório; uma
  falha de I/O ao imprimir o resumo (passo 7.1) continua escapando como
  antes. Fora do pedido aprovado.

## Dependências
Nenhuma dependência nova: reaproveita `httpx`, `pandas`, `duckdb` já
listados em `requirements.txt` pelos módulos anteriores. `argparse` e
`logging` são da biblioteca padrão.

## Fora de escopo
- Agendamento de execução (cron, `systemd timer`, etc.) — quem orquestra
  a periodicidade é externo a este projeto.
- Exportação dos **dados** para outros formatos (CSV, Parquet, etc.) — só
  grava em DuckDB, como já decidido em `specs/persistencia.md`. O
  relatório HTML é a exceção, descrito em `specs/relatorio.md`.
- Flags de configuração de retry, timeout ou backoff da extração —
  permanecem como constantes fixas de `extracao.py`.
- Qualquer nova regra de negócio de extração, limpeza ou persistência —
  este módulo só encadeia o que já existe.
- Paralelismo entre séries (a gravação por série, descrita acima, é
  sequencial, não concorrente).
- Coordenação entre múltiplas execuções simultâneas da CLI no mesmo
  arquivo `--banco` (ver "Limitações conhecidas").

## Critérios de aceite
1. `executar` encadeia `buscar_series` → `limpar` → `criar_tabela` +
   `gravar` (uma vez por código com dados válidos) e retorna
   `ResumoPipeline` consistente com o que foi extraído/limpo/gravado.
2. `criar_tabela` é sempre chamada por `executar`, mesmo em uma conexão
   nova sem a tabela `indicadores` ainda criada.
3. Séries que falham na extração aparecem em
   `resumo.series_ignoradas`, nunca em `resumo.gravacoes`.
4. Séries bem-sucedidas na extração, com ao menos uma linha válida após
   a limpeza, aparecem em `resumo.gravacoes` com a contagem correta de
   `inseridos`/`atualizados` (via `ResultadoGravacao` real de
   `persistencia.gravar`) e em `resumo.nomes` com o nome certo.
5. `resumo.descartes` reflete exatamente `resultado_limpeza.descartes`
   (sem transformação).
6. `formatar_resumo` produz texto determinístico e legível a partir de
   um `ResumoPipeline` conhecido, incluindo os três motivos de descarte
   sempre presentes (mesmo com contagem `0`).
7. `main` retorna `0` só quando não há série ignorada e, se o relatório
   foi pedido, ele foi gravado; `1` quando há pelo menos uma série
   ignorada (inclusive quando **todas** falharam, e mesmo que o relatório
   também falhe); `2` para entrada de CLI inválida (antes de abrir
   qualquer recurso), inclusive `--so-relatorio` combinado com
   `--sem-relatorio`, `--series` ou `--data-referencia`; `3` para erro
   inesperado (ex.: falha em `criar_client`, erro de banco), sem tentar o
   relatório; `4` quando os dados foram gravados (o resto daria `0`) mas o
   relatório não pôde ser gerado ou gravado, ou quando `--so-relatorio`
   não consegue gerá-lo. Uma falha apenas ao imprimir a mensagem de
   sucesso, com o relatório gravado, não altera o exit code. Detalhes e
   critérios do relatório em `specs/relatorio.md` (critérios de aceite 9 a
   11 e 16).
8. `main` sempre fecha o client e a conexão que chegaram a existir, em
   qualquer um dos caminhos de saída (`0`, `1`, `3`, `4`; `2` nem
   chega a abrir recursos), inclusive quando o client foi injetado pelo
   chamador, e sem tentar fechar algo que nunca foi criado.
9. O resumo vai para stdout; qualquer log vai para stderr; nenhum teste
   depende de mensagens de log aparecerem em stdout.
10. `_configurar_logging` usa `logging.getLogger("indicadores")`
    diretamente (não `logging.basicConfig`), aplica
    `logging.Formatter("%(levelname)s %(name)s: %(message)s")` ao
    handler, mantém `propagate=True`, e chamar `main` várias vezes no
    mesmo processo não acumula handlers duplicados nesse logger.
11. `--banco` exibe `metavar="CAMINHO"` na mensagem de ajuda/uso do
    `argparse`.
12. Nenhum teste faz chamada de rede real ou cria arquivo `.duckdb` fora
    de `tmp_path`.
13. Cobertura de testes do módulo (`pipeline.py` + `__main__.py`) ≥ 80%.
14. `--relatorio CAMINHO`, `--sem-relatorio` e `--so-relatorio` existem,
    com os defaults e a exclusão mútua descritos em "Argumentos"; o fluxo
    de geração do relatório em `main` segue o passo 7 e
    `specs/relatorio.md`; `--so-relatorio` abre o banco em somente
    leitura (passo 2a).

## Casos de teste (pytest)
1. `test_executar_grava_series_bem_sucedidas` — `MockTransport` com 2
   séries respondendo dados válidos; `duckdb.connect(":memory:")`;
   assert `resumo.gravacoes` tem as 2 entradas com `ResultadoGravacao`
   corretos (`inseridos` igual à quantidade de linhas válidas,
   `atualizados=0` na 1ª execução) e `resumo.nomes` com os nomes certos.
2. `test_executar_cria_tabela_automaticamente` — conexão `:memory:`
   nova, sem `criar_tabela` chamada antes; `executar(...)` funciona sem
   erro (confirma que `executar` chama `criar_tabela` internamente).
3. `test_executar_isola_serie_com_falha_de_extracao` — `MockTransport`
   com 1 série OK e 1 série sempre 500 (falha após retries, com
   `esperar=lambda s: None` para não esperar de verdade); assert que a
   série OK aparece em `gravacoes` e a outra aparece em
   `series_ignoradas`, com o `motivo` sendo a mensagem de `ErroHTTP`.
4. `test_executar_propaga_descartes_da_limpeza` — `MockTransport`
   retornando uma linha com valor inválido misturada a linhas válidas;
   assert que `resumo.descartes` tem a entrada esperada com o `motivo`
   correto.
5. `test_executar_reexecucao_atualiza_em_vez_de_duplicar` — chama
   `executar` duas vezes seguidas com a mesma conexão e os mesmos dados
   mockados; assert que a 2ª chamada retorna `atualizados == total` e
   `inseridos == 0` para a série repetida.
6. `test_executar_usa_atualizado_em_unico_por_chamada` — mockando/
   monkeypatching a hora atual; roda `executar` com 2 séries; assert que
   ambas as linhas gravadas em `gravacoes` têm exatamente o mesmo
   `atualizado_em` (verificável lendo a tabela via `persistencia.ler`).
7. `test_executar_serie_sem_linha_valida_nao_aparece_em_gravacoes` —
   `MockTransport` cuja série retorna só linhas inválidas (todas
   descartadas); assert que o código não aparece em `gravacoes` nem em
   `series_ignoradas`, mas os descartes aparecem em `resumo.descartes`.
8. `test_formatar_resumo_lista_series_gravadas_e_nomes` — `ResumoPipeline`
   construído à mão (sem rede/banco); assert que a string produzida
   contém as linhas esperadas para `gravacoes`/`nomes`.
9. `test_formatar_resumo_lista_tres_motivos_de_descarte_mesmo_com_zero`
   — `ResumoPipeline` com `descartes=[]`; assert que a string ainda
   contém as três linhas de motivo, todas com contagem `0`.
10. `test_formatar_resumo_lista_series_ignoradas_com_nome_via_fallback`
    — `SerieIgnorada` com código fora de `SERIES` (ex.: `999`); assert
    que a string usa `serie_999` (mesmo fallback de `limpeza.py`).
11. `test_formatar_resumo_vazio_mostra_nenhuma` — `ResumoPipeline` com
    `gravacoes={}` e `series_ignoradas=[]`; assert que aparecem as
    linhas `(nenhuma)` correspondentes.
12. `test_main_sucesso_retorna_zero_e_imprime_resumo` — `tmp_path` para
    `--banco`; `client` mockado com todas as séries respondendo com
    sucesso; via `capsys`, assert código de retorno `0` e que o stdout
    contém o resumo esperado (e não contém linhas de log).
13. `test_main_serie_ignorada_retorna_um` — igual ao anterior, mas 1
    série sempre responde **404** (não 500) e as demais com sucesso;
    assert retorno `1`. Decisão registrada: usa 404, não 500, porque
    404 não tem retry em `extracao.py` — `main` não expõe o parâmetro
    `esperar` de `buscar_series`/`buscar_serie`, então um teste de
    `main` não tem como injetar um `esperar` no-op; usando 500 o teste
    de fato esperaria os ~1,5s reais de backoff (`0.5s + 1s`) antes de
    falhar, deixando a suíte lenta sem necessidade — 404 falha na
    primeira tentativa, sem espera, e já é suficiente para exercitar o
    caminho de série ignorada.
14. `test_main_entrada_invalida_retorna_dois` — chama `main(["--series",
    "abc"])`; assert `SystemExit`/retorno `2` e que nada foi executado
    (nenhuma tentativa de abrir conexão/client) — verificável, por
    exemplo, checando que nenhum arquivo `.duckdb` foi criado em
    `tmp_path`.
15. `test_main_data_referencia_invalida_retorna_dois` — chama
    `main(["--data-referencia", "2024-13-40"])`; assert código `2`.
16. `test_main_erro_de_banco_retorna_tres` — `--banco` apontando para um
    caminho que faz `abrir_conexao` falhar (ex.: caminho que colide com
    um arquivo comum no lugar de diretório, criado antes pelo teste);
    assert retorno `3` e que existe um registro de log (`caplog`) do
    erro.
17. `test_main_fecha_client_e_conexao_mesmo_em_erro` — força `executar`
    a levantar uma exceção (ex.: monkeypatch em `pipeline.executar`
    para levantar `RuntimeError`); assert que o `client` passado (um
    espião/`Mock` com método `close`) teve `close()` chamado, e que a
    conexão (real, `:memory:` ou `tmp_path`) também foi fechada
    (`conexao.close()` chamado, verificável via espião ou via
    `duckdb`, que rejeita uso após `close()`).
18. `test_main_falha_ao_criar_client_retorna_tres` — sem `client`
    injetado; monkeypatch em `indicadores.__main__.criar_client` para
    levantar uma exceção (ex.: `RuntimeError("falha ao criar client")`);
    assert retorno `3`, registro de log do erro via `caplog`, e que
    nenhuma conexão/arquivo `.duckdb` chega a ser criado em `tmp_path`
    (confirma que a falha acontece antes de `abrir_conexao`, dentro do
    mesmo `try`, sem deixar nada pendurado para o `finally` tentar
    fechar incorretamente).
19. `test_main_verbose_ajusta_nivel_de_log` — chama `main` com `-v` e
    sem `-v`; assert, checando diretamente
    `logging.getLogger("indicadores").level`, que o nível muda de
    `logging.WARNING` (sem `-v`) para `logging.INFO` (com `-v`).
20. `test_main_log_inclui_nivel_e_nome_do_logger` — provoca ao menos um
    registro de log (ex.: cenário com série ignorada); via `capsys`,
    assert que uma linha em `capsys.readouterr().err` contém o nível
    (ex. `"WARNING"`) e o nome do logger (ex. `"indicadores.limpeza"`
    ou `"indicadores.persistencia"`, conforme o registro emitido),
    confirmando o formato `"%(levelname)s %(name)s: %(message)s"`
    aplicado pelo `logging.Formatter` de `_configurar_logging`.
21. `test_main_logs_vao_para_stderr_nao_stdout` — cenário com pelo menos
    um descarte/série ignorada (gera log); via `capsys`, assert que as
    mensagens de log aparecem em `capsys.readouterr().err`, não em
    `.out`.
22. `test_main_chamado_duas_vezes_nao_duplica_handlers` — chama `main`
    duas vezes seguidas (mesmo processo, cenário típico de suíte de
    testes); assert que
    `logging.getLogger("indicadores").handlers` continua com **um só**
    handler adicionado por `_configurar_logging` (não dois), e que uma
    mensagem de log não aparece duplicada em `capsys`/`caplog`.

Os casos de teste das flags de relatório e do exit `4` (itens 44 a 55 de
`specs/relatorio.md`) ficam definidos naquela spec e não são duplicados
aqui; o mesmo vale para os casos da revisão de código de 09/10/2026 que
tocam a CLI (itens 59, 60, 64 e 65 de `specs/relatorio.md`: banco aberto
em somente leitura, banco inalterado byte a byte e falha só do `print` de
sucesso sem efeito no exit code). Os testes `test_main_*` acima passam a
usar `--sem-relatorio` ou `--relatorio <tmp_path>/r.html` (ver "Mudanças
em outros documentos e testes existentes" em `specs/relatorio.md`).

## Convenções seguidas
- Nomes de função e variável em português, snake_case
  (`executar`, `formatar_resumo`, `_configurar_logging`), exceto
  identificadores de bibliotecas (`argparse.ArgumentParser`, `main`,
  convenção universal de ponto de entrada Python, incluindo
  `if __name__ == "__main__": sys.exit(main())`).
- Nenhum teste faz chamada de rede real ou cria `.duckdb` fora de
  `tmp_path`/`:memory:`.
- `client` de teste nunca é exposto como flag de CLI — só como parâmetro
  keyword-only de `main`, documentado como uso exclusivo de teste.
- Reaproveita `FUSO_BRASILIA` (constante pública de `extracao.py`) para
  o `atualizado_em` único da chamada, sem importar função privada de
  outro módulo (`persistencia._agora_brasilia` permanece privada).
- Logging configurado no logger nomeado do pacote (`"indicadores"`), não
  no root logger via `logging.basicConfig`, para ter efeito confiável
  mesmo quando o `pytest` já configurou handlers no root; formato fixo
  via `logging.Formatter("%(levelname)s %(name)s: %(message)s")`.
