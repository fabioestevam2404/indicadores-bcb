# indicadores-bcb

Pipeline local que coleta séries do SGS/Banco Central, limpa e grava em DuckDB. Implementa **extração**, **limpeza** e **persistência em DuckDB**.

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
.venv\Scripts\python -m pip install -r requirements-dev.txt
```

### Linux / macOS

```bash
python -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt
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

Testes usam `httpx.MockTransport` e não acessam a internet. Nenhuma espera real é feita nos testes de retry (a função `esperar` é substituída por um no-op).

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
│       └── pipeline.py              # Orquestração do pipeline
├── tests/
│   ├── __init__.py
│   ├── test_extracao.py             # Testes de extração
│   ├── test_limpeza.py              # Testes de limpeza
│   ├── test_persistencia.py         # Testes de persistência
│   ├── test_pipeline.py             # Testes de orquestração
│   └── test_main.py                 # Testes da CLI
├── specs/
│   ├── extracao.md                  # Especificação de extração
│   ├── limpeza.md                   # Especificação de limpeza
│   ├── persistencia.md              # Especificação de persistência
│   └── pipeline.md                  # Especificação do pipeline
├── dados/                           # Diretório criado automaticamente
│   └── indicadores.duckdb           # Arquivo de banco (ignorado pelo git)
├── requirements.txt                 # Dependências de execução (versões fixas)
├── requirements-dev.txt             # + pytest, pytest-cov, ruff
├── pyproject.toml
└── README.md
```

## Estado do projeto

- [x] Extração de dados brutos (`buscar_serie`, `buscar_series`)
- [x] Limpeza e tipagem (conversão de valores, datas datetime64[ns])
- [x] Persistência em DuckDB (`abrir_conexao`, `criar_tabela`, `gravar`, `ler`)
- [x] Script de execução de ponta a ponta (`python -m indicadores`)
