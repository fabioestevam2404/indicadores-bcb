# Especificação — Módulo de Extração (SGS/BCB)

## Status
Primeiro módulo do projeto. Não há código em `src/` além do pacote vazio
`src/indicadores/__init__.py` e não há `specs/` prévias — este documento
define o formato que os próximos specs devem seguir.

## Objetivo
Buscar, na API do SGS (Banco Central), os dados **brutos** das séries
configuradas, para os últimos 5 anos a partir de uma data de referência, e
devolvê-los em memória exatamente como a API respondeu.

Este módulo **não limpa dado nenhum**: não converte vírgula decimal para
ponto, não converte datas `dd/mm/aaaa` para ISO, não altera tipos (tudo
continua `str`) e não persiste nada em DuckDB. Essas responsabilidades
pertencem a um módulo de limpeza/transformação e a um módulo de
persistência, especificados separadamente. A extração é responsável apenas
por: montar a URL/parâmetros corretos, chamar a API, validar minimamente a
forma da resposta (é JSON? é uma lista? cada item tem as chaves
esperadas? não está vazia?) e propagar erros de forma tipada.

## Localização e decisão de pacote
O repositório já contém `src/indicadores/__init__.py` (pacote `indicadores`,
não `indicadores_bcb`). Para manter consistência com o que já existe, o
módulo fica em:

```
src/indicadores/extracao.py
```

(Divergência registrada: o pedido sugeriu `indicadores_bcb`; optou-se por
seguir a convenção de pacote já presente no repositório.)

Os testes ficam em `tests/test_extracao.py`, seguindo `tests/__init__.py`
já existente.

## Endpoint e restrições conhecidas da API
```
GET https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados
    ?formato=json&dataInicial=dd/mm/aaaa&dataFinal=dd/mm/aaaa
```

- `dataInicial` e `dataFinal` são **obrigatórios** nesta chamada (não se usa
  o endpoint "últimos N valores").
- O SGS limita consultas de séries diárias a uma janela de até **10 anos**
  por requisição. A janela usada aqui é de **5 anos**, portanto está dentro
  do limite com folga. Caso um dia a janela solicitada precise ultrapassar
  10 anos, será necessário paginar em múltiplas requisições — isso está
  **fora do escopo** deste módulo/spec e deve ser tratado quando (e se)
  surgir essa necessidade.
- A resposta de sucesso é uma lista JSON de objetos
  `{"data": "dd/mm/aaaa", "valor": "1234,56"}`. Às vezes a API responde com
  corpo HTML (ex.: erro 5xx de infraestrutura, manutenção) em vez de JSON,
  ou (mais raro) com um objeto JSON de erro em vez de uma lista.

## Registro de séries
Constante de módulo, para permitir adicionar séries no futuro sem alterar
a lógica de busca:

```python
SERIES: dict[int, str] = {
    432: "selic_meta",
    433: "ipca_mensal",
    1: "dolar_ptax_venda",
}
```

Chave = código SGS, valor = nome curto em snake_case da série. Adicionar
uma série nova é adicionar uma entrada neste dict.

## API pública do módulo

### Constantes
```python
SGS_BASE_URL: str = "https://api.bcb.gov.br"
SGS_TIMEOUT: httpx.Timeout = httpx.Timeout(10.0)  # connect/read/write/pool = 10s
JANELA_ANOS: int = 5
MAX_TENTATIVAS: int = 3  # 1 tentativa original + 2 retries
```

### Exceções
```python
class ErroExtracaoBCB(Exception):
    """Base para erros de extração. Sempre carrega `codigo` da série."""
    def __init__(self, codigo: int, mensagem: str): ...

class ErroHTTP(ErroExtracaoBCB):
    """Status HTTP fora de 2xx (3xx, 4xx ou 5xx), após esgotar retries
    quando aplicável. Quando levantado após esgotar `MAX_TENTATIVAS`
    (casos de 5xx), a mensagem inclui explicitamente o texto
    "após N tentativas" (N = `MAX_TENTATIVAS`), para diferenciar de um
    3xx/4xx que falhou já na 1ª tentativa."""
    def __init__(self, codigo: int, status_code: int, corpo: str): ...

class ErroRespostaInvalida(ErroExtracaoBCB):
    """Corpo da resposta não tem a forma esperada: não é JSON (ex.: HTML
    de erro/manutenção), ou é JSON mas não é uma lista (ex.: objeto de
    erro `{"erro": "..."}`), ou é uma lista cujos itens não são dicts com
    as chaves "data" e "valor"."""

class ErroSerieVazia(ErroExtracaoBCB):
    """A API respondeu 2xx com JSON válido (lista bem formada), mas
    lista vazia."""

class ErroConexao(ErroExtracaoBCB):
    """Timeout ou erro de rede, após esgotar retries."""
```

### Estrutura de resultado por série
```python
@dataclass(frozen=True)
class ResultadoSerie:
    codigo: int
    dados: list[dict[str, str]] | None   # bruto, sem alteração
    erro: ErroExtracaoBCB | None         # None se sucesso

    @property
    def sucesso(self) -> bool:
        return self.erro is None
```

### Data de referência padrão (`_hoje`)
```python
def _hoje() -> date:
    """Retorna a data atual em UTC-3 fixo (horário de Brasília, sem
    horário de verão — extinto no Brasil desde 2019).

    Implementação: `datetime.now(timezone(timedelta(hours=-3))).date()`.

    Decisão registrada: não usar `zoneinfo`/"America/Sao_Paulo" porque no
    Windows o `zoneinfo` da stdlib exige o pacote adicional `tzdata`
    instalado separadamente para ter os bancos de fuso horário; como o
    Brasil não tem mais horário de verão, um offset fixo de -3h é
    equivalente na prática e evita essa dependência extra.
    """
```

`_hoje()` é uma função privada (prefixo `_`, não faz parte da API pública)
usada como valor de `data_referencia` sempre que `buscar_serie` ou
`buscar_series` recebem `data_referencia=None`.

### Cálculo de intervalo (função pura, sem I/O)
```python
def calcular_intervalo(
    data_referencia: date,
    janela_anos: int = JANELA_ANOS,
) -> tuple[str, str]:
    """Retorna (data_inicial, data_final) no formato dd/mm/aaaa.

    data_final = data_referencia.
    data_inicial = data_referencia com o ano subtraído de `janela_anos`.
    Caso data_referencia seja 29/02 e (ano - janela_anos) não seja
    bissexto, data_inicial cai para 28/02 daquele ano (não avança para
    01/03). Retorna strings já formatadas dd/mm/aaaa, prontas para uso
    como query params.
    """
```

`data_referencia` é sempre passada explicitamente por quem chama
`calcular_intervalo`. Nas funções de busca (abaixo), `data_referencia` é um
parâmetro opcional que, se `None`, assume `_hoje()` **no momento da
chamada** — isso é o que torna a data injetável e os testes determinísticos
(basta passar uma data fixa nos testes).

### Criação do client HTTP padrão
```python
def criar_client() -> httpx.Client:
    """Monta o client HTTP usado em produção (fora de testes).

    Retorna `httpx.Client(base_url=SGS_BASE_URL, timeout=SGS_TIMEOUT,
    headers={"Accept": "application/json"})`. Função pública justamente
    para poder ser testada isoladamente (inspecionando `base_url`,
    `timeout` e `headers` do client retornado) sem precisar de
    monkeypatch em `httpx.Client.__init__`.
    """
```

### Busca de uma série
```python
def buscar_serie(
    codigo: int,
    *,
    client: httpx.Client,
    data_referencia: date | None = None,
    esperar: Callable[[float], None] = time.sleep,
) -> list[dict[str, str]]:
    """Busca os dados brutos de uma série no SGS.

    - `client` precisa ter `base_url` configurado (ex.: `SGS_BASE_URL`),
      pois a URL usada aqui é relativa:
      `/dados/serie/bcdata.sgs.{codigo}/dados`. Em teste, o client deve
      ser criado como
      `httpx.Client(base_url=SGS_BASE_URL, transport=httpx.MockTransport(...))`.
    - Envia params `formato=json`, `dataInicial`, `dataFinal` (calculados
      via `calcular_intervalo`, usando `data_referencia` ou `_hoje()` se
      `None`).
    - Envia header `Accept: application/json`.
    - Retorna a lista de dicts exatamente como veio no JSON (chaves
      "data" e "valor", ambos str, sem nenhuma conversão).
    - `client` é obrigatório aqui (sem client default), para forçar quem
      chama a decidir a instância. `buscar_serie` **nunca fecha** o
      client recebido — fechar é responsabilidade de quem o criou.
    - `esperar` é a função usada para aguardar entre tentativas de retry
      (default `time.sleep`). É keyword-only e injetável para que os
      testes passem um no-op (ex.: `lambda segundos: None`) e não fiquem
      lentos.
    - Levanta `ErroHTTP`, `ErroRespostaInvalida`, `ErroSerieVazia` ou
      `ErroConexao` conforme a seção de tratamento de erros abaixo.
    """
```

### Busca de várias séries (falha isolada por série)
```python
def buscar_series(
    codigos: Iterable[int] | None = None,
    *,
    client: httpx.Client | None = None,
    data_referencia: date | None = None,
    esperar: Callable[[float], None] = time.sleep,
) -> dict[int, ResultadoSerie]:
    """Busca várias séries, uma por vez, isolando falhas.

    - `codigos` default = `list(SERIES.keys())`.
    - Se `client` for None, cria internamente `criar_client()` como
      context manager e **fecha ao final da função**.
    - Se `client` for fornecido por quem chama (injetado), `buscar_series`
      **não fecha** esse client — o ciclo de vida continua sendo de
      responsabilidade de quem o criou. Só o client criado internamente
      via `criar_client()` é fechado.
    - `esperar` é repassado diretamente para cada chamada de
      `buscar_serie` (mesmo default `time.sleep`, mesma razão: permitir
      no-op em teste).
    - Para cada código, chama `buscar_serie` dentro de um try/except
      `ErroExtracaoBCB`. Se der certo, guarda
      `ResultadoSerie(codigo, dados=..., erro=None)`. Se falhar, guarda
      `ResultadoSerie(codigo, dados=None, erro=<exceção>)` e **segue para
      o próximo código** — a falha de uma série nunca interrompe as
      demais.
    - Retorna `dict[int, ResultadoSerie]`, uma entrada por código
      solicitado, na ordem de `codigos`.
    """
```

Decisão explícita: `buscar_serie` (singular) propaga a exceção normalmente
(quem quiser tratar uma série isolada decide o que fazer). `buscar_series`
(plural) nunca propaga — sempre devolve o resultado, com erro encapsulado
em `ResultadoSerie.erro`, porque o caso de uso principal é "buscar as 3
séries e ver depois o que deu certo".

## Tratamento de erros e retry

| Situação | Exceção | Retry? |
|---|---|---|
| Status HTTP 3xx (redirect) | `ErroHTTP` | Não (httpx não segue redirecionamentos por padrão; um redirect inesperado do SGS precisa ser investigado, não seguido às cegas) |
| Status HTTP 4xx | `ErroHTTP` | Não (erro determinístico: parâmetro/URL errados) |
| Status HTTP 5xx | `ErroHTTP` | Sim, até `MAX_TENTATIVAS` |
| Corpo não é JSON (ex.: HTML) | `ErroRespostaInvalida` | Não (não é transiente — se vier HTML de manutenção com 2xx, repetir na mesma hora tende a repetir o problema; tratar isso como falha a ser investigada, não a ser mascarada por retry) |
| JSON válido, mas não é uma lista (ex.: objeto `{"erro": "..."}`) ou algum item não é dict com as chaves "data" e "valor" | `ErroRespostaInvalida` | Não (mesmo raciocínio: forma inesperada da resposta não se resolve repetindo a mesma chamada) |
| JSON válido, lista vazia | `ErroSerieVazia` | Não (não é erro de transporte; repetir não muda o resultado) |
| Timeout (`httpx.TimeoutException`) | `ErroConexao` | Sim, até `MAX_TENTATIVAS` |
| Erro de conexão (`httpx.ConnectError` e subclasses de `httpx.TransportError`) | `ErroConexao` | Sim, até `MAX_TENTATIVAS` |

Validação de forma da resposta (decisão registrada): além de checar que o
corpo é JSON e que é uma lista, `buscar_serie` valida que **cada item da
lista é um dict contendo as chaves `"data"` e `"valor"`** (só a presença
das chaves — não valida formato de data nem se `valor` parece número).
Qualquer violação dessa forma cai em `ErroRespostaInvalida`. Validação de
**conteúdo** (ex.: `data` é uma data válida, `valor` é um número com
vírgula bem formado) continua fora de escopo deste módulo.

Regras de retry:
- `MAX_TENTATIVAS = 3` (1 tentativa original + 2 repetições).
- A lista de esperas entre tentativas é **derivada de `MAX_TENTATIVAS`**,
  não hardcoded: `[0.5 * 2**i for i in range(MAX_TENTATIVAS - 1)]`, i.e.,
  para `i` de `0` a `MAX_TENTATIVAS - 2`. Com `MAX_TENTATIVAS = 3` isso dá
  `[0.5, 1.0]` (0,5s após a 1ª falha, 1s após a 2ª falha), aplicado
  chamando `esperar(0.5)` e depois `esperar(1.0)`. Se `MAX_TENTATIVAS`
  mudar no futuro, a lista de esperas se ajusta automaticamente.
- Nos testes, `esperar` é substituído por um no-op, então nenhum teste
  deste módulo depende de tempo real de espera.
- Após esgotar `MAX_TENTATIVAS` sem sucesso, a exceção correspondente
  (`ErroHTTP` para 5xx, `ErroConexao` para timeout/rede) é levantada com
  a última causa observada (`raise ... from erro_original`) e com
  mensagem incluindo o texto **"após N tentativas"** (N =
  `MAX_TENTATIVAS`).
- O retry é local a `buscar_serie`; `buscar_series` não adiciona uma
  camada extra de retry, apenas isola falhas por série e repassa
  `esperar` adiante.

## Regras de negócio
1. Nenhuma transformação de dado: `valor` permanece string com vírgula
   decimal (`"13,75"`), `data` permanece string `dd/mm/aaaa`. Isso é
   verificado byte a byte nos testes (string de entrada == string de
   saída).
2. O intervalo de datas é sempre de `JANELA_ANOS` (5) anos, calculado a
   partir de uma `data_referencia` injetável — nunca `_hoje()` chamado
   diretamente dentro de `calcular_intervalo`.
3. O registro `SERIES` é a única fonte de verdade de quais séries existem
   por padrão; nenhuma outra função hardcoda os códigos 432/433/1.
4. Nenhuma credencial é usada ou necessária (API pública do BCB, sem
   chave). Nenhuma env var de autenticação é lida por este módulo.
5. O client HTTP é sempre injetável; `buscar_serie` exige o client
   explicitamente, `buscar_series` aceita client opcional e usa
   `criar_client()` (timeout e header explícitos) quando não fornecido.
6. A função de espera do retry (`esperar`) é sempre injetável em
   `buscar_serie` e `buscar_series`, com default `time.sleep` — isso é o
   que permite testar retry sem esperas reais.
7. Client injetado nunca é fechado pelo módulo (`buscar_serie` e
   `buscar_series`, quando recebem `client` de quem chama, não chamam
   `client.close()`); só o client criado internamente por `buscar_series`
   via `criar_client()` é fechado ao final.
8. A data padrão (`data_referencia=None`) é calculada em UTC-3 fixo via
   `_hoje()`, sem `zoneinfo`, pelos motivos registrados na seção
   "Data de referência padrão".

## Fora de escopo (para specs futuras)
- Limpeza/normalização dos dados (vírgula → float, data → ISO).
- Validação de conteúdo dos campos (ex.: `data` é data válida, `valor` é
  número bem formado) — só a forma (chaves presentes) é validada aqui.
- Persistência em DuckDB.
- CLI / agendamento de execução.
- Paginação para janelas de consulta acima do limite de 10 anos do SGS.
- Registro de log/observabilidade estruturada (pode ser adicionado depois
  sem mudar a API pública descrita aqui).
- Seguir redirecionamentos HTTP (3xx é tratado como erro, não seguido).

## Critérios de aceite
1. `calcular_intervalo` é função pura, testável sem rede, e trata
   corretamente 29/02 em ano bissexto quando o ano de destino não é
   bissexto.
2. `buscar_serie` monta URL, query params (`formato`, `dataInicial`,
   `dataFinal`) e header `Accept` corretamente, verificável inspecionando
   a `request` recebida por um `httpx.MockTransport`.
3. `buscar_serie` devolve a lista de dicts sem qualquer alteração de tipo
   ou conteúdo em relação ao JSON recebido.
4. Cada um dos tipos de erro (`ErroHTTP` — nas variantes 3xx, 4xx e 5xx —,
   `ErroRespostaInvalida` — nas suas três variantes: não-JSON, JSON
   não-lista, item sem chaves esperadas —, `ErroSerieVazia`,
   `ErroConexao`) é coberto por pelo menos um teste que simula a condição
   via mock, sem acessar a internet.
5. Retry em 5xx e em erro de conexão/timeout é coberto por teste que
   comprova o número de tentativas (via contador no mock) e não depende de
   tempo real de espera (`esperar` passado como no-op nesses testes). 3xx
   e 4xx são cobertos por teste que comprova que **não** há retry (1
   única chamada).
6. `buscar_series` isola falha: com 3 séries mockadas, uma delas falhando,
   o teste comprova que as outras duas retornam `ResultadoSerie` de
   sucesso e a terceira retorna `ResultadoSerie` com `erro` preenchido.
7. `criar_client()` é testável diretamente (sem mock de rede), expondo
   `base_url == SGS_BASE_URL`, `timeout == SGS_TIMEOUT` e header
   `Accept: application/json`.
8. `buscar_series` fecha o client apenas quando o cria internamente; um
   client injetado por quem chama continua aberto (`is_closed is False`)
   após a chamada.
9. `_hoje()` retorna a data atual em UTC-3 fixo, verificável mockando
   `datetime.now` (ou função equivalente) e comparando com o offset
   esperado.
10. Nenhum teste faz chamada de rede real (uso exclusivo de
    `httpx.MockTransport` ou equivalente) nem espera real de retry (uso
    de `esperar` como no-op).
11. Cobertura de testes do módulo ≥ 80% (linha de base do projeto).

## Casos de teste (pytest, com mocks)
1. `test_calcular_intervalo_janela_padrao_de_5_anos` — data de referência
   fixa, confirma `data_final == data_referencia` e
   `data_inicial == data_referencia - 5 anos`, ambos formatados
   `dd/mm/aaaa`.
2. `test_calcular_intervalo_29_fevereiro_ano_bissexto_ajusta_para_28` —
   `data_referencia = date(2024, 2, 29)`, `janela_anos = 5` → ano destino
   2019 (não bissexto) → `data_inicial == "28/02/2019"`.
3. `test_calcular_intervalo_formato_dd_mm_aaaa` — garante zero-padding
   (ex.: dia/mês de um dígito).
4. `test_buscar_serie_monta_url_e_parametros_corretos` — `MockTransport`
   captura a request; assert em `request.url.path` e nos `params`
   (`formato=json`, `dataInicial`, `dataFinal`) e no header `Accept`.
   Client criado com `base_url=SGS_BASE_URL`.
5. `test_buscar_serie_retorna_dados_brutos_sem_alteracao` — mock responde
   `[{"data": "01/01/2020", "valor": "13,75"}]`; assert que o retorno é
   idêntico (mesma string, sem `float`, sem data ISO).
6. `test_buscar_serie_erro_http_3xx_nao_tenta_novamente` — mock responde
   301 com header `Location`; assert `ErroHTTP` levantado e que só houve
   1 chamada (sem retry, sem seguir o redirect).
7. `test_buscar_serie_erro_http_4xx_nao_tenta_novamente` — mock responde
   404 uma vez; assert `ErroHTTP` levantado e que só houve 1 chamada
   (sem retry).
8. `test_buscar_serie_erro_http_5xx_retenta_e_leva_ate_max_tentativas` —
   mock sempre responde 500; chama `buscar_serie` com
   `esperar=lambda segundos: None`; assert `ErroHTTP` levantado após
   `MAX_TENTATIVAS` chamadas, sem espera real, com mensagem contendo
   "após 3 tentativas".
9. `test_buscar_serie_erro_http_5xx_sucesso_apos_retry` — mock responde
   500 na 1ª chamada e 200 com JSON válido na 2ª; chama `buscar_serie`
   com `esperar=lambda segundos: None`; assert que o retorno é o da 2ª
   chamada, sem espera real.
10. `test_buscar_serie_resposta_nao_json_levanta_erro_resposta_invalida`
    — mock responde 200 com corpo HTML; assert `ErroRespostaInvalida`,
    sem retry.
11. `test_buscar_serie_json_nao_e_lista_levanta_erro_resposta_invalida` —
    mock responde 200 com corpo `{"erro": "parametro invalido"}`; assert
    `ErroRespostaInvalida`, sem retry.
12. `test_buscar_serie_item_sem_chaves_esperadas_levanta_erro_resposta_invalida`
    — mock responde 200 com `[{"campo_errado": "x"}]`; assert
    `ErroRespostaInvalida`, sem retry.
13. `test_buscar_serie_lista_vazia_levanta_erro_serie_vazia` — mock
    responde 200 com `[]`; assert `ErroSerieVazia`, sem retry.
14. `test_buscar_serie_timeout_retenta_e_levanta_erro_conexao` — mock
    levanta `httpx.TimeoutException` em todas as tentativas; chama
    `buscar_serie` com `esperar=lambda segundos: None`; assert
    `ErroConexao` após `MAX_TENTATIVAS` chamadas, sem espera real.
15. `test_buscar_serie_erro_conexao_sucesso_apos_retry` — mock levanta
    `httpx.ConnectError` na 1ª chamada e responde 200 na 2ª; chama
    `buscar_serie` com `esperar=lambda segundos: None`; assert sucesso,
    sem espera real.
16. `test_buscar_serie_sem_data_referencia_usa_hoje` — chama
    `buscar_serie` sem `data_referencia`; assert que os params
    `dataInicial`/`dataFinal` enviados correspondem a `calcular_intervalo`
    aplicado sobre `_hoje()` (mockado/monkeypatch para data fixa).
17. `test_hoje_usa_fuso_utc_menos_3` — chama `_hoje()` (com `datetime.now`
    mockado/monkeypatch para um instante UTC conhecido); assert que o
    resultado corresponde à data em UTC-3, inclusive em casos de virada
    de dia perto da meia-noite.
18. `test_buscar_series_usa_registro_series_por_padrao` — chama
    `buscar_series()` sem `codigos`; assert que as chaves do resultado
    são exatamente `SERIES.keys()`.
19. `test_buscar_series_isola_falha_de_uma_serie` — mock com 3 códigos,
    um deles sempre 500 (falha após retries) e dois com sucesso; chama
    `buscar_series` com `esperar=lambda segundos: None`; assert que os
    dois têm `resultado.sucesso is True` e o terceiro tem
    `resultado.erro` do tipo `ErroHTTP`.
20. `test_buscar_series_nao_fecha_client_injetado` — cria um
    `httpx.Client(base_url=SGS_BASE_URL, transport=MockTransport(...))`
    e chama `buscar_series(client=meu_client)`; depois da chamada, assert
    `meu_client.is_closed is False`; só fecha manualmente ao final do
    teste.
21. `test_buscar_series_sem_client_usa_criar_client_internamente` — chama
    `buscar_series()` sem `client` (com transporte mockado via
    monkeypatch em `criar_client`, ou equivalente); assert que o client
    interno foi usado e fechado ao final (`is_closed is True` após o
    retorno da função, verificado capturando a instância criada).
22. `test_criar_client_usa_base_url_timeout_e_header_esperados` — chama
    `criar_client()` diretamente (sem mock, sem I/O — só instancia o
    client); assert `client.base_url == httpx.URL(SGS_BASE_URL)`,
    `client.timeout == SGS_TIMEOUT` e
    `client.headers["accept"] == "application/json"`. Fecha o client ao
    final do teste.

## Convenções seguidas
- Datas de query em `dd/mm/aaaa` (exigência da API SGS); dentro do
  módulo isso é o único lugar em que essa formatação aparece — módulos
  posteriores convertem para ISO.
- Nomes de função e variável em português, snake_case, conforme convenção
  do repositório.
- Sem acesso à internet em teste (uso de `httpx.MockTransport`) e sem
  espera real em teste (uso de `esperar` como no-op nos casos de retry).
- Data padrão calculada em UTC-3 fixo via `_hoje()`, sem `zoneinfo`
  (evita dependência de `tzdata` no Windows; Brasil não tem mais horário
  de verão desde 2019).
