# Handoff — indicadores-bcb

## Estado atual
Pipeline local que busca séries do SGS/Banco Central, limpa os dados, grava em DuckDB e oferece análises de negócio. **Está completo e funcionando.**
- **Repositório:** https://github.com/fabioestevam2404/indicadores-bcb, branch `main`. O CI (GitHub Actions) roda lint, testes e cobertura em Ubuntu e Windows a cada push.
- **Qualidade:** 159 testes passando, cobertura de 99% e `ruff check .` sem problemas. Nenhum teste acessa a rede.
- **Execução real:** o pipeline rodou contra a API do BCB em 28/09/2026. A primeira execução gravou 3.142 linhas. A segunda atualizou as mesmas 3.142 sem inserir nenhuma, confirmando que rodar de novo não duplica dados.
- **Agendamento:** a tarefa `indicadores-bcb diario` foi registrada no Agendador do Windows em 28/09/2026 (segunda a sexta, 19:00, só com o usuário logado) e atualizada em 07/10/2026 com suporte a despertar de suspensão, espera de rede, 60-min limit e retry automático em exit 1. Uma execução manual pela própria tarefa (`Start-ScheduledTask`) no mesmo dia terminou com resultado 0, e o log `logs/execucao_2026-09.log` saiu completo, em UTF-8 sem BOM. Execuções monitoradas entre 29/09–07/10: dias 29–30 OK (exit 0 e 1 com retry bem-sucedido), dias 01/05/06/10 não rodaram (PC suspenso ou desligado), dia 02 falha de rede logo após o PC acordar, dia 07 encerramento forçado (0xC000013A, provavelmente logoff). Em 08/10/2026, o horário foi alterado de 19:00 para 16:00 (segunda a sexta) — decisão 7 em specs/agendamento.md.
- **Análises:** implementadas três análises puras (Mudanças Selic, IPCA 12m, PTAX mensal), sem I/O e sem dependência de duckdb/httpx.
- **Relatório HTML:** implementado em 09/10/2026. Módulo `relatorio.py` gera HTML autocontido com 4 cards (resumo executivo), 4 gráficos (históricos com tooltip), 3 tabelas (análise de dados), temas claro/escuro, formatação em pt-BR, sem recursos externos. Integrado à CLI com flags `--relatorio`, `--sem-relatorio`, `--so-relatorio`. Exit code 4 adicionado para falha do relatório (dados gravados normalmente). Arquivo `relatorio.html` ignorado pelo git.

## Como rodar
```powershell
.venv\Scripts\activate
$env:PYTHONPATH="src"
python -m indicadores -v                      # busca as 3 séries e grava em dados/indicadores.duckdb
python -m indicadores --series 432            # só a Selic
python -m indicadores --data-referencia 2024-06-30
python -m indicadores --so-relatorio          # só regenera o relatório (sem rede)
pytest -v ; pytest --cov=indicadores --cov-report=term-missing ; ruff check .
```

**Códigos de saída:**

| Código | Situação |
|---|---|
| 0 | tudo gravado e relatório gerado (se pedido) |
| 1 | alguma série falhou na extração; as outras foram gravadas |
| 2 | argumento inválido |
| 3 | erro inesperado ou de banco |
| 4 | dados gravados normalmente, mas relatório não pôde ser gerado ou gravado (ou, em `--so-relatorio`, banco não encontrado) |

Descartes de linhas não mudam o código de saída.

**Códigos de saída do wrapper (`executar_diario.ps1`):**

| Código | Situação |
|---|---|
| 0–4 | repassado da CLI (da última tentativa se houver retry) |
| 10 | venv não encontrado |
| 11 | falha ao iniciar Python |
| 12 | falha ao preparar o log |
| 13 | erro inesperado no wrapper depois que o log foi preparado |

## Arquitetura
| Módulo | O que faz |
|---|---|
| `extracao.py` | `buscar_series()` busca 432 (Selic meta), 433 (IPCA) e 1 (PTAX venda) dos últimos 5 anos e devolve os dados brutos. Faz até 3 tentativas em erro 5xx ou de rede, e isola a falha de cada série. |
| `limpeza.py` | `limpar()` gera um DataFrame com as colunas `codigo, serie, data, valor` e devolve também a lista de descartes e de séries ignoradas. |
| `persistencia.py` | Grava na tabela `indicadores`, com chave `(codigo, data)`. Quando a chave já existe, o valor é atualizado. Cada gravação é atômica. `ler()` devolve os dados gravados. |
| `pipeline.py` | `executar()` encadeia as três etapas, grava uma série por vez e monta o resumo. |
| `__main__.py` | Linha de comando: argumentos, configuração do log, resumo na saída padrão e logs na saída de erro. Integra o relatório. |
| `relatorio.py` | Núcleo puro: `gerar_html()` recebe um DataFrame e data/hora e devolve string HTML. `gravar_relatorio()` escreve de forma atômica. Sem I/O no núcleo, sem acesso a banco nem rede. |
| `analise.py` | Três funções puras: `mudancas_selic()`, `ipca_acumulado_12m()`, `ptax_mensal()`. Sem I/O, sem conhecimento de duckdb/httpx. |
| `scripts/executar_diario.ps1` | Wrapper fino chamado pela tarefa agendada (ou manualmente para depurar). Resolve o Python do venv, executa `python -m indicadores`, grava tudo em log mensal e implementa retry automático em exit 1 (espera 5 min padrão, configurável via `INDICADORES_ESPERA_RETRY_SEGUNDOS`). |
| `scripts/agendar_tarefa.ps1` | Registra/remove a tarefa `indicadores-bcb diario` no Agendador de Tarefas do Windows. Usa `-WakeToRun` (desperta de suspensão) e `-RunOnlyIfNetworkAvailable` (espera rede). |

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
- **Teste da nova tentativa sem internet:** `tests/test_scripts.py` provoca o exit 1 apontando `HTTPS_PROXY`/`HTTP_PROXY` para um socket local aberto pelo próprio teste, que conta as conexões recebidas. O teste exige pelo menos uma conexão, o que prova que o httpx foi pelo proxy local e não pela internet.
- **Exit codes do wrapper:** 0–4 repassados da CLI (última tentativa), 10 (venv não encontrado), 11 (falha ao iniciar Python), 12 (falha ao preparar o log — mensagem vai para stderr nesse caso), 13 (erro inesperado depois que o log foi preparado).
- **Retry automático:** apenas exit 1 dispara nova tentativa (série falhada). Exit 3 (erro na CLI) nunca retenta, porque indica falha não-transiente (banco bloqueado, permissão, etc). Exit 4 também não retenta (falha só do relatório). Idempotência garantida por upsert `(codigo, data)`. Retry não distingue entre falhas transientes e permanentes — ambas esperam o mesmo tempo (padrão 300s); quem quer ajustar usa `INDICADORES_ESPERA_RETRY_SEGUNDOS`.
- **Mudanças Selic:** comparação exata de floats (`valor_novo != valor_anterior`); valores de origem são strings determinísticas, então dois valores iguais sempre produzem o mesmo `float64` bit a bit, sem tolerância necessária.
- **Janela do IPCA:** "consecutivo" é checado por mês civil via `Period` mensal, não por dia exato da data — o SGS não garante um dia específico (ex. sempre dia 1) dentro do mês.
- **Sem `mes_completo` no PTAX:** `dias_com_dado` sinaliza o mês parcial; coluna `mes_completo` exigiria conhecimento de calendário de feriados ou injeção de "hoje", fora do escopo de uma função pura. Quem consome a análise usa `dias_com_dado` para essa checagem.
- **`Period.to_timestamp()` no pandas 3:** gera `datetime64[us]` (microssegundos), não nanossegundos; por isso o `astype("datetime64[ns]")` explícito é necessário no PTAX mensal.
- **Wake-to-run (agendamento):** a flag `-WakeToRun` desperta o PC de suspensão, mas só quando alimentado (notebook na tomada). PC desligado ou hibernado não acorda.
- **Network wait (agendamento):** a flag `-RunOnlyIfNetworkAvailable` atrasa o início até a rede estar pronta. Observado: logo após o PC acordar, a rede pode demorar 1–2 minutos. Se timeout da rede ocorrer, a tarefa é adiada para o próximo dia.
- **Relatório HTML:** `gerar_html()` é pura (sem I/O, sem relógio); reutiliza `mudancas_selic`, `ipca_acumulado_12m` e `ptax_mensal` de `analise.py`. Gráficos em SVG com tooltip em JS mínimo (string, não executada em testes). Sem CDN, sem recurso externo — o arquivo abre offline. Escrita atômica do arquivo HTML. Cada execução regenera o relatório; `--so-relatorio` permite sem buscar a API.

## Ambiente
Python 3.11.7 e venv em `.venv`. As versões são fixas: `requirements.txt` tem httpx 0.28.1, pandas 3.0.6 e duckdb 1.5.6; `requirements-dev.txt` inclui esse arquivo e acrescenta pytest 9.1.1, pytest-cov 7.1.0 e ruff 0.16.9. Instale com `pip install -r requirements-dev.txt`. O projeto usa o layout `src/`, e o `pyproject.toml` só configura o pytest. O `.gitignore` ignora `*.duckdb`, `logs/`, `relatorio.html`, `relatorio.html.tmp` e `.coverage`.

## Forma de trabalho (definida no CLAUDE.md)
- A sessão principal só coordena e não edita código. O fluxo é spec-writer → implementer → test-writer → code-reviewer → doc-writer.
- Cada etapa é reportada antes de seguir para a próxima.
- Só os subagentes do projeto podem ser usados, nunca os globais.
- O commit é feito pelo usuário.

## Próximos passos sugeridos
1. **Verificação manual do relatório HTML** — contra o banco real:
   - Executar `python -m indicadores --so-relatorio` e abrir `relatorio.html` com a rede desligada; conferir que tudo aparece sem requisição externa (aba Rede do navegador vazia).
   - Alternar tema claro/escuro do SO; conferir cores e legibilidade (inclusive os rótulos dos eixos nos dois temas).
   - Estreitar a janela até ~360px; conferir que cards quebram e tabelas/gráficos rolam.
   - Passar o mouse em cada gráfico; verificar que crosshair, data e valor funcionam; desabilitar JS e conferir que o gráfico continua legível.
   - Conferir que os números dos cards batem com `mudancas_selic`, `ipca_acumulado_12m` e `ptax_mensal` rodados à mão sobre o mesmo banco.

2. **Acompanhar as execuções agendadas continuamente.** Conferir `Get-ScheduledTaskInfo -TaskName "indicadores-bcb diario"` (último resultado deve ser 0; se a nova tentativa der certo, o resultado final já é 0, e um 1 significa que as duas tentativas falharam) e o arquivo `logs/execucao_AAAA-MM.log` depois das 16:00 dos próximos dias úteis para confirmar funcionamento. Ativar o histórico de operações do Agendador com `wevtutil set-log Microsoft-Windows-TaskScheduler/Operational /enabled:true` (requer admin) para ver detalhes em caso de falha.

3. **Ideias futuras** (não priorizadas): correlações entre as séries e expor as análises na CLI.
