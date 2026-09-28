# indicadores-bcb
Pipeline local que coleta séries do SGS/Banco Central, limpa e grava em DuckDB.

## Stack
Python 3.11+, httpx, pandas, DuckDB, pytest, ruff.

## Orquestração
- Você (sessão principal) é o orquestrador: planeja, delega, confere e reporta.
- Você NUNCA edita arquivos de código diretamente. Toda escrita ou alteração
  em src/ é delegada ao subagente implementer.
- Fluxo padrão: spec-writer → implementer → test-writer → code-reviewer.
- Tarefas independentes (ex.: test-writer e doc-writer) podem ser delegadas
  juntas, para rodar em paralelo.
- Ao final de cada etapa, reporte o resultado antes de seguir para a próxima.
- Use apenas os subagentes do projeto (spec-writer, implementer, test-writer,
  code-reviewer, doc-writer). Não delegue a agentes globais como bcb-ingestion
  ou etl-transformer: eles são de outro projeto e usam outra stack.

## Regras
- Toda função nova tem teste em pytest. Testes não acessam a internet (usar mocks).
- Datas no formato ISO (AAAA-MM-DD) depois da transformação.
- Valores numéricos como float; vírgula decimal convertida.
- Nenhuma credencial no código.
- Não fazer commit; eu reviso e faço o commit.

## Comandos
- Testes: pytest -v
- Lint: ruff check .
