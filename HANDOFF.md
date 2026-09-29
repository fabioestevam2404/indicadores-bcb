# Handoff — indicadores-bcb

## Estado atual
Pipeline local que busca séries do SGS/Banco Central, limpa os dados, grava em DuckDB e oferece análises de negócio. **Está completo e funcionando.**
- **Repositório:** https://github.com/fabioestevam2404/indicadores-bcb, branch `main`. O CI (GitHub Actions) roda lint, testes e cobertura em Ubuntu e Windows a cada push.
- **Qualidade:** 149 testes passando, cobertura de 99% e `ruff check .` sem problemas. Nenhum teste acessa a rede.
- **Execução real:** o pipeline rodou contra a API do BCB em 28/09/2026. A primeira execução gravou 3.142 linhas. A segunda atualizou as mesmas 3.142 sem inserir nenhuma, confirmando que rodar de novo não duplica dados.
- **Agendamento:** a tarefa `indicadores-bcb diario` foi registrada no Agendador do Windows em 28/09/2026 (segunda a sexta, 19:00, só com o usuário logado). Uma execução manual pela própria tarefa (`Start-ScheduledTask`) no mesmo dia terminou com resultado 0, e o log `logs/execucao_2026-09.log` saiu completo, em UTF-8 sem BOM. A primeira execução agendada é 29/09/2026 às 19:00.
- **Análises:** implementadas três análises puras (Mudanças Selic, IPCA 12m, PTAX mensal), sem I/O e sem dependência de duckdb/httpx.

## Como rodar
```powershell
.venv\Scripts\activate
$env:PYTHONPATH="src"
python -m indicadores -v                      # busca as 3 séries e grava em dados/indicadores.duckdb
python -m indicadores --series 432            # só a Selic
python -m indicadores --data-referencia 2024-06-30
pytest -v ; pytest --cov=indicadores --cov-report=term-missing ; ruff check .
```

**Códigos de saída:**

| Código | Situação |
|---|---|
| 0 | tudo gravado |
| 1 | alguma série falhou na extração; as outras foram gravadas |
| 2 | argumento inválido |
| 3 | erro inesperado ou de banco |

Descartes de linhas não mudam o código de saída.

## Arquitetura
| Módulo | O que faz |
|---|---|
| `extracao.py` | `buscar_series()` busca 432 (Selic meta), 433 (IPCA) e 1 (PTAX venda) dos últimos 5 anos e devolve os dados brutos. Faz até 3 tentativas em erro 5xx ou de rede, e isola a falha de cada série. |
| `limpeza.py` | `limpar()` gera um DataFrame com as colunas `codigo, serie, data, valor` e devolve também a lista de descartes e de séries ignoradas. |
| `persistencia.py` | Grava na tabela `indicadores`, com chave `(codigo, data)`. Quando a chave já existe, o valor é atualizado. Cada gravação é atômica. `ler()` devolve os dados gravados. |
| `pipeline.py` | `executar()` encadeia as três etapas, grava uma série por vez e monta o resumo. |
| `__main__.py` | Linha de comando: argumentos, configuração do log, resumo na saída padrão e logs na saída de erro. |
| `scripts/executar_diario.ps1` | Wrapper fino chamado pela tarefa agendada (ou manualmente para depurar). Resolve o Python do venv, executa `python -m indicadores` e grava tudo em log mensal. |
| `scripts/agendar_tarefa.ps1` | Registra/remove a tarefa `indicadores-bcb diario` no Agendador de Tarefas do Windows. |
| `analise.py` | Três funções puras: `mudancas_selic()`, `ipca_acumulado_12m()`, `ptax_mensal()`. Sem I/O, sem conhecimento de duckdb/httpx. |

Cada módulo tem uma spec em `specs/`, e as specs são a referência para qualquer decisão.

## Decisões que não são óbvias
- **Separador de milhar:** `"1.234"` vira 1234 (é milhar pela convenção brasileira) e `"5.25"` vira 5,25. A diferença está em quantos dígitos vêm depois do ponto: com exatamente 3 é milhar. A validação aceita só os dígitos de 0 a 9 (`re.ASCII`), porque o `float()` do Python também aceita dígitos arábicos.
- **Tipos de coluna fixos:** todas as colunas têm o tipo convertido de forma explícita, com `astype`. O pandas 3 mudou os tipos padrão, e isso mantém o resultado igual entre versões.
- **Horário:** usa UTC-3 fixo (`FUSO_BRASILIA`), e não `zoneinfo`, porque no Windows `zoneinfo` exigiria instalar o pacote `tzdata`.
- **Selic meta (432):** tem um valor para **cada dia do calendário**, não um por mês. O pipeline grava o que a API devolve.
- **Execuções simultâneas:** duas execuções gravando no mesmo arquivo DuckDB fazem a segunda falhar com código 3, porque o arquivo fica bloqueado. Isso foi confirmado com dois processos de verdade.
- **Log duplicado:** se `main()` for chamada de dentro de uma aplicação que já configurou o próprio log, as mensagens podem sair duplicadas. Na linha de comando isso não acontece.
- **BOM em `executar_diario.ps1`:** o arquivo está em UTF-8 **com BOM** — o PowerShell 5.1 lê `.ps1` sem BOM em ANSI (codificação antiga) e corrompe literais acentuados (como o cabeçalho "início"). Com BOM, o PowerShell o detecta e decodifica corretamente.
- **Log gravado em UTF-8 sem BOM:** via API .NET diretamente (`[System.IO.File]::AppendAllText` com `UTF8Encoding($false)`), nunca via cmdlets PowerShell — porque `-Encoding utf8` do PowerShell 5.1 grava COM BOM, e o comportamento de reescrita em append é inconsistente entre versões.
- **Captura de stdout/stderr:** o wrapper usa `Start-Process -RedirectStandardOutput/-RedirectStandardError` (com arquivos temporários), não `2>&1`. Motivo: no PowerShell 5.1, redirecionar stderr de um executável nativo com `2>&1` embrulha cada linha num `ErrorRecord` que dispara `$ErrorActionPreference = "Stop"` e interrompe o script, mesmo em logs normais (não erros).
- **Escape de argumentos:** cada argumento com espaço ou aspas passa por escape conforme as regras do `CommandLineToArgvW`, sem confiar no `Start-Process` fazer isso automaticamente — porque `-ArgumentList` de um array não cita os elementos.
- **Exit codes do wrapper:** 0–3 repassados da CLI, 10 (venv não encontrado), 11 (falha ao iniciar Python), 12 (falha ao preparar o log — nesse caso a mensagem vai para stderr).
- **Mudanças Selic:** comparação exata de floats (`valor_novo != valor_anterior`); valores de origem são strings determinísticas, então dois valores iguais sempre produzem o mesmo `float64` bit a bit, sem tolerância necessária.
- **Janela do IPCA:** "consecutivo" é checado por mês civil via `Period` mensal, não por dia exato da data — o SGS não garante um dia específico (ex. sempre dia 1) dentro do mês.
- **Sem `mes_completo` no PTAX:** `dias_com_dado` sinaliza incômodo mês parcial; coluna `mes_completo` exigiria conhecimento de calendário de feriados ou injeção de "hoje", fora do escopo de uma função pura. Quem consome a análise usa `dias_com_dado` para essa checagem.
- **`Period.to_timestamp()` no pandas 3:** gera `datetime64[us]` (microssegundos), não nanossegundos; por isso o `astype("datetime64[ns]")` explícito é necessário no PTAX mensal.

## Ambiente
Python 3.11.7 e venv em `.venv`. As versões são fixas: `requirements.txt` tem httpx 0.28.1, pandas 3.0.6 e duckdb 1.5.6; `requirements-dev.txt` inclui esse arquivo e acrescenta pytest 9.1.1, pytest-cov 7.1.0 e ruff 0.16.9. Instale com `pip install -r requirements-dev.txt`. O projeto usa o layout `src/`, e o `pyproject.toml` só configura o pytest. O `.gitignore` ignora `*.duckdb`, `logs/` e `.coverage`.

## Forma de trabalho (definida no CLAUDE.md)
- A sessão principal só coordena e não edita código. O fluxo é spec-writer → implementer → test-writer → code-reviewer → doc-writer.
- Cada etapa é reportada antes de seguir para a próxima.
- Só os subagentes do projeto podem ser usados, nunca os globais.
- O commit é feito pelo usuário.

## Próximos passos sugeridos
1. **Acompanhar as primeiras execuções agendadas.** Conferir `Get-ScheduledTaskInfo -TaskName "indicadores-bcb diario"` (último resultado deve ser 0) e o arquivo `logs/execucao_AAAA-MM.log` depois das 19:00 dos próximos dias úteis para confirmar funcionamento.
2. **Ideias futuras** (não priorizadas): correlações entre as séries e expor as análises na CLI.
