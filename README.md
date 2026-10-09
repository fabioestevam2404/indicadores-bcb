# indicadores-bcb

Pipeline local que coleta séries do SGS/Banco Central, limpa os dados, grava em DuckDB e oferece análises de negócio. Implementa **extração**, **limpeza**, **persistência em DuckDB** e **análises**.

## Stack

Python 3.11+, httpx, pandas, duckdb, pytest, ruff.

## Séries coletadas

| Código SGS | Nome do registro | Descrição | Periodicidade |
|-----------|------------------|-----------|---------------|
| 432 | `selic_meta` | Selic meta | Mensal |
| 433 | `ipca_mensal` | IPCA | Mensal |
| 1 | `dolar_ptax_venda` | Dólar PTAX (venda) | Diário |

Período: últimos 5 anos a partir da data de referência (data atual no fuso de Brasília, UTC-3).

## Instalação

### Windows

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

### Linux / macOS

```bash
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
```

As versões são fixas. `requirements.txt` tem só o necessário para rodar o pipeline (httpx, pandas, duckdb); `requirements-dev.txt` inclui esse arquivo e acrescenta pytest, pytest-cov e ruff. Para só executar o pipeline, sem testes, basta `requirements.txt`.

## Execução

A forma principal de uso é a linha de comando:

```bash
python -m indicadores
```

Configure o `PYTHONPATH` e ative o venv conforme a seção anterior.

### Argumentos

- `--banco CAMINHO` (padrão: `dados/indicadores.duckdb`) — arquivo DuckDB
- `--series COD [COD ...]` (padrão: todas as séries) — códigos SGS a buscar
- `--data-referencia AAAA-MM-DD` (padrão: hoje em Brasília) — data de referência da janela de 5 anos
- `-v` — nível INFO
- `-vv` — nível DEBUG

### Exemplos

Execução padrão (todas as séries, hoje, arquivo padrão):

```bash
python -m indicadores
```

Só a Selic:

```bash
python -m indicadores --series 432
```

Reprocessar com data de referência específica:

```bash
python -m indicadores --data-referencia 2024-06-30
```

Modo verbose:

```bash
python -m indicadores -vv
```

### Saída

O resumo é impresso em stdout:

```
Séries gravadas:
  1 (dolar_ptax_venda): 8 inseridos, 1252 atualizados
  432 (selic_meta): 12 inseridos, 1808 atualizados
  433 (ipca_mensal): 3 inseridos, 57 atualizados

Descartes por motivo:
  data_invalida: 0
  valor_invalido: 2
  data_duplicada: 0

Séries ignoradas:
  (nenhuma)
```

Os logs são emitidos em stderr no formato `NIVEL logger: mensagem`.

### Exit codes

| Código | Situação |
|--------|----------|
| 0 | Sucesso; nenhuma série ignorada |
| 1 | Execução concluída, mas pelo menos uma série falhou na extração; demais foram gravadas |
| 2 | Argumento inválido (detectado antes de executar) |
| 3 | Erro inesperado (falha ao criar client, abrir banco ou durante a execução) |

**Nota**: descartes de linhas individuais não afetam o exit code.

### Limitações conhecidas

- **Execuções simultâneas**: não rode duas instâncias apontando para o mesmo `--banco` ao mesmo tempo — a segunda falha ao abrir o arquivo (bloqueado) com exit code 3. Reexecução sequencial é segura, graças ao upsert.
- **Transação por série**: a gravação faz uma transação para cada série, não uma única transação para o batch inteiro; um erro numa série não desfaz as anteriores já commitadas.

## Agendamento diário (Windows)

A tarefa agendada `indicadores-bcb diario` executa automaticamente de segunda a sexta às 16:00, com o usuário logado.

### O que a tarefa faz

- **Acorda o PC**: usa `-WakeToRun` para despertar da suspensão (só funciona com o notebook ligado à tomada; PC desligado ou hibernado não acorda).
- **Espera a rede**: não inicia até a rede estar disponível (`-RunOnlyIfNetworkAvailable`).
- **Nova tentativa automática**: se a execução terminar com exit 1 (série falhada), espera 5 minutos (padrão, configurável via `INDICADORES_ESPERA_RETRY_SEGUNDOS`) e tenta uma única vez de novo. Seguro por upsert.
- **Limite de execução**: 60 minutos por tentativa.

O log mostra cada tentativa com o rótulo `(tentativa N)`.

### Registrar a tarefa

A partir da raiz do repositório:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\agendar_tarefa.ps1
```

Não requer privilégios de administrador. Rodar de novo só atualiza a tarefa existente (é idempotente). Tarefa registrada em 07/10/2026.

### Remover a tarefa

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\agendar_tarefa.ps1 -Remover
```

### Rodar manualmente o fluxo da tarefa

Para testar o wrapper (com log):

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\executar_diario.ps1
```

Argumentos extras são repassados para `python -m indicadores`, ex.:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\executar_diario.ps1 --series 432 -v
```

### Logs

As execuções são registradas em `logs/execucao_AAAA-MM.log` (um arquivo por mês, em UTF-8 sem BOM). Cada execução grava um cabeçalho de início, o resumo (stdout), os logs (stderr) e um rodapé com o exit code. O diretório `logs/` é criado automaticamente e é ignorado pelo git (sem rotação automática).

Exemplo de conteúdo com nova tentativa:

```
===== 2026-10-07 16:00:15 - início =====

--- stdout (tentativa 1) ---
Séries gravadas:
  432 (selic_meta): 0 inseridos, 20 atualizados
...

--- stderr (tentativa 1) ---
INFO indicadores.pipeline: executar iniciado
...

AVISO: exit 1; nova tentativa em 300 s

--- stdout (tentativa 2) ---
Séries gravadas:
  432 (selic_meta): 0 inseridos, 20 atualizados
...

--- stderr (tentativa 2) ---
INFO indicadores.pipeline: executar iniciado
...

===== 2026-10-07 16:05:35 - fim (exit code: 0) =====
```

### Exit codes do wrapper

| Código | Situação |
|--------|----------|
| 0–3 | Repassado da CLI (mesmos códigos de `python -m indicadores`) — da última tentativa se houver retry |
| 10 | venv não encontrado (`.venv\Scripts\python.exe` ausente) |
| 11 | Falha ao iniciar o Python |
| 12 | Falha ao preparar o log (criar diretório ou gravar cabeçalho); mensagem vai para stderr |
| 13 | Erro inesperado no wrapper depois que o log foi preparado |

### Conferir status

Abra o Agendador de Tarefas do Windows ou execute:

```powershell
Get-ScheduledTaskInfo -TaskName "indicadores-bcb diario"
```

Mostra a última execução, o resultado e a próxima execução.

#### Ativar histórico do Agendador

Para ver detalhes de cada execução, ative o histórico (requer administrador):

```powershell
wevtutil set-log Microsoft-Windows-TaskScheduler/Operational /enabled:true
```

Se uma tarefa for encerrada à força (ex.: logoff, desligamento), o `LastTaskResult` mostra `3221225786` (0xC000013A). O log no disco pode ficar sem rodapé, indicando interrupção.

### Observação

Uma janela do PowerShell pode piscar por um instante no horário da execução — é comportamento normal do Windows quando a tarefa roda com a sessão do usuário logado.

## Uso programático

Para usar os módulos diretamente em Python:

### Buscar todas as séries

```python
from indicadores.extracao import buscar_series

resultados = buscar_series()
for codigo, resultado in resultados.items():
    if resultado.sucesso:
        print(f"Série {codigo}: {len(resultado.dados)} pontos")
    else:
        print(f"Série {codigo} falhou: {resultado.erro.mensagem}")
```

O `ResultadoSerie` expõe:
- `sucesso`: `bool` indicando se a busca foi bem-sucedida
- `dados`: lista de dicts `{"data": "dd/mm/aaaa", "valor": "..."}` (strings brutas, sem conversão)
- `erro`: instância de `ErroExtracaoBCB` ou `None` se sucesso

### Buscar uma série isolada

```python
from indicadores.extracao import buscar_serie, criar_client, ErroExtracaoBCB

try:
    with criar_client() as client:
        dados = buscar_serie(432, client=client)
        print(f"Selic: {dados[0]}")
except ErroExtracaoBCB as e:
    print(f"Erro na série {e.codigo}: {e.mensagem}")
```

**Dados brutos**: os valores vêm exatamente como a API respondeu — datas em `dd/mm/aaaa`, valores em string com vírgula decimal (`"13,75"`). Nenhuma transformação é feita neste módulo.

Para executar exemplos fora do pytest, configure `PYTHONPATH`:
- Linux/macOS: `export PYTHONPATH=src`
- PowerShell: `$env:PYTHONPATH="src"`

(Em testes, `pyproject.toml` já configura o pythonpath automaticamente.)

### Limpeza e tipagem

```python
from indicadores.extracao import buscar_series
from indicadores.limpeza import limpar

resultados = buscar_series()
resultado_limpeza = limpar(resultados)

# DataFrame limpo e tipado
df = resultado_limpeza.dados
print(df.head())
```

O `ResultadoLimpeza` expõe:
- `dados`: `pandas.DataFrame` com colunas `["codigo", "serie", "data", "valor"]`, ordenado por código e depois data
- `descartes`: lista de linhas rejeitadas (data/valor inválida, data duplicada)
- `series_ignoradas`: lista de séries que falharam na extração

**DataFrame**: dtypes garantidos — `codigo` (int64), `serie` (object), `data` (datetime64[ns]), `valor` (float64).

#### Inspeção de descartes

```python
for descarte in resultado_limpeza.descartes:
    print(f"Série {descarte.codigo}: {descarte.motivo}")
    print(f"  data_bruta={descarte.data_bruta}, valor_bruto={descarte.valor_bruto}")
```

#### Regras de conversão de valor

| Entrada | Saída | Exemplo |
|---------|-------|---------|
| Vírgula decimal | float | `"13,75"` → 13.75 |
| Ponto de milhar + vírgula | float | `"1.234,56"` → 1234.56 |
| Múltiplos pontos de milhar + vírgula | float | `"1.234.567,89"` → 1234567.89 |
| Ponto (3 dígitos depois) | float (milhar) | `"1.234"` → 1234.0 |
| Ponto (< 3 dígitos depois) | float (decimal) | `"5.25"` → 5.25 |
| Inválida ou sem padrão | descarte | `""`, `"-"`, `"nan"`, `"inf"`, `"1,234.56"`, dígitos não ASCII |

**Outras regras:**
- Data: formato fixo `dd/mm/aaaa` (sem inferência); espaços nas pontas são removidos.
- Data duplicada: mantém primeira ocorrência válida; duplicatas viram descarte.
- Série com falha na extração: inteira ignorada (vai para `series_ignoradas`).
- Descartes: registrados no logger `indicadores.limpeza` (nível `warning`).

### Gravação em DuckDB

```python
from indicadores.extracao import buscar_series
from indicadores.limpeza import limpar
from indicadores.persistencia import abrir_conexao, criar_tabela, gravar, ler

# Pipeline completo
resultados = buscar_series()
resultado_limpeza = limpar(resultados)

con = abrir_conexao()
criar_tabela(con)
resultado_gravacao = gravar(con, resultado_limpeza.dados)

print(f"Inseridos: {resultado_gravacao.inseridos}, Atualizados: {resultado_gravacao.atualizados}")

# Ler de volta
df = ler(con)
con.close()
```

O `ResultadoGravacao` expõe:
- `inseridos`: número de linhas novas inseridas
- `atualizados`: número de linhas atualizadas (revisão de valor)
- `total`: total de linhas processadas

**Arquivo DuckDB**: padrão `dados/indicadores.duckdb` (o diretório é criado automaticamente; o arquivo é ignorado pelo git).

#### Esquema da tabela

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

#### Comportamento

- **Upsert**: executar de novo atualiza valores revisados pelo BCB sem duplicar linhas (chave primária `(codigo, data)`).
- **Atomicidade**: a gravação faz rollback completo em caso de erro; nada fica parcialmente escrito.
- **Ordem de chamadas**: `criar_tabela` deve ser chamado antes de `gravar`.
- **Transação**: `gravar` não pode ser chamado se a conexão já tem uma transação aberta.
- **Horário**: `atualizado_em` fica no horário de Brasília (UTC-3, naive).

#### Consulta direta

```python
# Exemplo: última data de cada série
resultado = con.execute("""
    SELECT serie, max(data) as ultima_atualizacao
    FROM indicadores
    GROUP BY serie
    ORDER BY serie
""").df()
print(resultado)
```

### Análises

Três análises de negócio sobre os dados persistidos:

```python
from indicadores.persistencia import abrir_conexao, ler
from indicadores.analise import mudancas_selic, ipca_acumulado_12m, ptax_mensal

con = duckdb.connect("dados/indicadores.duckdb", read_only=True)
dados = ler(con)

selic = mudancas_selic(dados)        # datas e magnitude das mudanças
ipca = ipca_acumulado_12m(dados)     # IPCA acumulado em 12 meses
ptax = ptax_mensal(dados)            # PTAX agregada por mês

con.close()
```

As funções aceitam o DataFrame de `ler` ou de `limpar.dados` e devolvem DataFrames com `dtypes` explícitos. Série ausente devolve DataFrame vazio com as colunas corretas. Colunas mínimas (`codigo`, `data`, `valor`) faltando levantam `ErroAnalise`.

| Análise | Colunas de saída | Regra de cálculo |
|---------|-----------------|-----------------|
| **Mudanças Selic** | `data`, `valor_anterior`, `valor_novo`, `variacao_pp` | Datas em que a meta (432) mudou; primeira observação não entra; `variacao_pp = valor_novo - valor_anterior` |
| **IPCA 12m** | `data`, `ipca_mensal`, `acumulado_12m` | Acumulado móvel de 12 meses consecutivos (sem lacuna, verificado por mês civil); fórmula: `(prod(1 + v/100) - 1) * 100`; sem arredondamento |
| **PTAX Mensal** | `mes`, `media`, `fechamento`, `minimo`, `maximo`, `dias_com_dado` | Agregação diária da PTAX (1) por mês civil; `fechamento` é do último dia com dado do mês; `dias_com_dado` sinaliza mês incompleto |

**Observações:**
- Funções são puras: mesma entrada sempre produz mesma saída.
- Trabalham com dados já limpos (sem duplicação por `(codigo, data)`); não reimplementam essa deduplicação.
- Ordenadas internamente por data/período, nunca dependem da ordem de entrada.

## Erros e retry (extração)

| Exceção | Causa | Retry? | Detalhes |
|---------|-------|--------|----------|
| `ErroHTTP` | Status HTTP fora de 2xx (3xx, 4xx ou 5xx) | Só 5xx | 3 tentativas, espera de 0,5s e 1s entre elas |
| `ErroRespostaInvalida` | Resposta não é JSON válido, não é lista, ou item sem chaves "data"/"valor" | Não | Indica problema estrutural, não transiente |
| `ErroSerieVazia` | API respondeu 2xx com JSON válido, mas lista vazia | Não | Nenhum dado no intervalo |
| `ErroConexao` | Timeout ou erro de conexão/rede | Sim | 3 tentativas, espera de 0,5s e 1s entre elas |

**Retry**: `MAX_TENTATIVAS = 3` (1 original + 2 retries). Erros 5xx e problemas de rede/timeout são retentados com espera exponencial de 0,5s e 1s. Em 3xx, 4xx e erros não-transientes, não há retry.

## Testes e lint

Pressupõe o venv ativado:
- Windows (PowerShell): `.venv\Scripts\activate`
- Linux/macOS: `source .venv/bin/activate`

```bash
# Executar testes
pytest -v

# Cobertura de testes
pytest --cov=indicadores --cov-report=term-missing

# Lint
ruff check .
```

Testes usam `httpx.MockTransport` e não acessam a internet. Nenhuma espera real é feita nos testes de retry (a função `esperar` é substituída por um no-op). O arquivo `test_scripts.py` só roda no Windows (PowerShell/Agendador) e é pulado no CI Linux.

### CI

O GitHub Actions ([.github/workflows/ci.yml](.github/workflows/ci.yml)) roda a cada push e pull request na `main`, em Ubuntu e Windows com Python 3.11: `ruff check .`, `pytest` com cobertura mínima de 80% (`--cov-fail-under=80`) e `python -m indicadores --help`. O CI nunca acessa a API do BCB.

## Estrutura do repositório

```
.
├── src/
│   └── indicadores/
│       ├── __init__.py
│       ├── __main__.py              # CLI (python -m indicadores)
│       ├── extracao.py              # Módulo de extração
│       ├── limpeza.py               # Módulo de limpeza e tipagem
│       ├── persistencia.py          # Módulo de persistência em DuckDB
│       ├── pipeline.py              # Orquestração do pipeline
│       └── analise.py               # Análises de negócio
├── scripts/
│   ├── executar_diario.ps1          # Wrapper da tarefa agendada
│   └── agendar_tarefa.ps1           # Registra/remove a tarefa no Agendador
├── tests/
│   ├── __init__.py
│   ├── test_extracao.py             # Testes de extração
│   ├── test_limpeza.py              # Testes de limpeza
│   ├── test_persistencia.py         # Testes de persistência
│   ├── test_pipeline.py             # Testes de orquestração
│   ├── test_main.py                 # Testes da CLI
│   ├── test_scripts.py              # Testes dos scripts PS (só Windows)
│   └── test_analise.py              # Testes de análises
├── specs/
│   ├── extracao.md                  # Especificação de extração
│   ├── limpeza.md                   # Especificação de limpeza
│   ├── persistencia.md              # Especificação de persistência
│   ├── pipeline.md                  # Especificação do pipeline
│   ├── agendamento.md               # Especificação do agendamento
│   └── analise.md                   # Especificação de análises
├── dados/                           # Diretório criado automaticamente
│   └── indicadores.duckdb           # Arquivo de banco (ignorado pelo git)
├── logs/                            # Diretório de logs (ignorado pelo git)
│   └── execucao_YYYY-MM.log         # Logs mensais da tarefa agendada
├── requirements.txt                 # Dependências de execução (versões fixas)
├── pyproject.toml
└── README.md
```

## Estado do projeto

- [x] Extração de dados brutos (`buscar_serie`, `buscar_series`)
- [x] Limpeza e tipagem (conversão de valores, datas datetime64[ns])
- [x] Persistência em DuckDB (`abrir_conexao`, `criar_tabela`, `gravar`, `ler`)
- [x] Orquestração de ponta a ponta (`pipeline.executar`)
- [x] Agendamento no Agendador do Windows (despertar, nova tentativa em exit 1, 60 min limit)
- [x] Análises de negócio (Mudanças Selic, IPCA 12m, PTAX mensal)
