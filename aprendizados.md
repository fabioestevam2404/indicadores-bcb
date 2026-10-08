# Aprendizados — indicadores-bcb

Piloto feito de 26/09 a 29/09/2026 para testar o método orquestrador + subagentes antes de levá-lo à NovaBank. Este documento reúne o que funcionou, o que precisou ser refeito e o que muda no `CLAUDE.md` do próximo projeto.

> Rascunho montado a partir do histórico das sessões, do git e dos logs. Os trechos marcados com **[completar]** dependem da sua avaliação.

## Resultado

- 10 commits, tag `v0.1.0`, CI verde em Ubuntu e Windows.
- 149 testes, 99% de cobertura, `ruff` limpo e nenhum teste acessa a rede.
- Pipeline real: 3.142 linhas gravadas; a segunda execução não duplicou nada.
- O escopo passou do roadmap: além das sessões 1 a 6, entraram fixação de versões, CI, agendamento diário no Windows e o módulo de análises.

## O que funcionou

**Spec antes de código.** As 6 specs (`specs/`) foram a referência de cada etapa. Os problemas de desenho apareceram na spec, que é barata de corrigir, e não no código. Exemplos: o parâmetro `esperar` para os testes de retry não esperarem de verdade, e a regra `"1.234"` → 1234 vs. `"5.25"` → 5,25, decidida e registrada antes da implementação.

**O code-reviewer pagou o custo.** Em todos os módulos ele achou pelo menos um problema real que os testes não pegavam:
- extração: lista de esperas presa ao número de tentativas (daria `IndexError` se `MAX_TENTATIVAS` subisse);
- limpeza: `format=` ignorado pelo pandas em silêncio, e `$` da regex aceitando quebra de linha;
- persistência: falha no `ROLLBACK` escondendo o erro original;
- pipeline: `criar_client()` fora do `try`, escapando do código de saída 3;
- agendamento: falha ao criar o log terminando com código 1, que já significa "série falhou".

**O test-writer não achou nenhum bug em `src/`** em nenhuma rodada. Os bugs vieram do code-reviewer, do lint numa versão nova e de rodar de verdade. Os testes valeram como rede de proteção para as correções, não como detector.

**Rodar de verdade revelou o que os mocks escondem.** O venv expôs a regra DTZ011 do ruff 0.16, que o Python global não acusava. O teste real do `.ps1` revelou que o PowerShell 5.1 corrompe acentos de script sem BOM. A concorrência no DuckDB foi confirmada com dois processos reais.

**Paralelizar tarefas independentes.** spec-writer + implementer, ou test-writer + doc-writer, rodando juntos encurtaram cada ciclo sem conflito, porque cada um mexia em arquivos diferentes.

**O orquestrador conferindo o trabalho.** Ler o arquivo inteiro e rodar `pytest`/`ruff` de novo depois de cada subagente pegou um `out.txt` esquecido na raiz e o HANDOFF desatualizado várias vezes.

**Commit feito pelo usuário.** Dava um ponto de revisão natural no fim de cada etapa.

## O que precisou ser refeito

| Problema | Causa | Como foi resolvido |
|---|---|---|
| Orquestrador cogitando agentes globais (`bcb-ingestion`, `etl-transformer`) | O `~/.claude/CLAUDE.md` global descreve outro projeto, com PySpark | Regra no `CLAUDE.md` do projeto: só os subagentes locais |
| Comandos com `&&` falhando | O PowerShell 5.1 não aceita `&&` | Passar a usar `; if ($?) { ... }` |
| CI quebrou no Windows | Os testes dos scripts dependiam do `.venv` local, que o CI não cria | Passo no CI que cria o `.venv` |
| HANDOFF desatualizado após vários commits | Ninguém era dono de atualizá-lo | Correções pontuais; nenhuma regra ainda |
| Plan Mode perdido sem aviso | Trocar de modelo com `/model` sai do Plan Mode | Reativar depois de trocar o modelo |
| `code-reviewer.md` esvaziado fora de commit | Não identificado | Restaurado com `git restore` em 03/10 |

## Custo de contexto dos subagentes

Cada papel foi **reaproveitado como a mesma instância** do início ao fim (o "spec-writer" de todos os módulos continuou com o nome "Especificar módulo de extração SGS"). O contexto foi acumulando:

| Subagente | Primeira chamada | Última chamada |
|---|---|---|
| spec-writer | ~58 mil tokens | ~387 mil tokens |
| test-writer | ~39 mil tokens | ~317 mil tokens |
| implementer | — | ~272 mil tokens e ~10 min para remover um `sort_values` |

Uma instância nova por módulo, recebendo a spec e o código como contexto, teria saído mais barata e mais rápida.

Na mesma linha, o roadmap pedia `/clear` no fim de cada sessão, mas tudo correu numa única conversa de 26/09 a 29/09.

## O que ainda não foi conferido

- **Valores contra as fontes oficiais.** O roadmap pedia comparar a Selic com o site do BC e o IPCA de 12 meses com o IBGE. O pipeline calculou IPCA 12m de 4,223% em ago/26 e Selic de 13,75% em 28/09/2026. **[completar: conferir no site do BC e no IBGE]**
- **Agendamento.** As execuções de 29/09 e 30/09 terminaram com código 0. A de 30/09 rodou às 20:32, e não às 19:00, provavelmente porque o PC estava desligado. Não há registro de 01/10. Em 02/10 as três séries falharam com erro de conexão (código 1). Vale entender se foi rede local ou instabilidade da API, e se o código 1 é suficiente como alerta, já que ninguém olha o Agendador. **[completar]**
- **Critério final do roadmap:** "explicar cada arquivo e cada decisão sem ajuda do agente". **[completar: autoavaliação]**

## Ajustes para o `CLAUDE.md` do próximo projeto

1. **Isolar do contexto global.** Declarar logo no topo quais subagentes valem para o projeto e que os agentes do `~/.claude/CLAUDE.md` não se aplicam. Ou tirar do arquivo global o que é específico de outro projeto.
2. **Ambiente explícito.** Registrar o shell (PowerShell 5.1: sem `&&`, `.ps1` com acento precisa de BOM, `-Encoding utf8` grava BOM) e que toda verificação roda no venv, nunca no Python global.
3. **Subagente novo por módulo.** Pedir explicitamente uma instância nova a cada módulo e passar o contexto por arquivo (spec + código), em vez de continuar a mesma.
4. **Code-reviewer é obrigatório; o test-writer pode ser mais enxuto.** Manter a revisão em todo módulo. Avaliar se o test-writer pode receber a lista de casos direto da spec, sem rodada própria de descoberta.
5. **Dono do HANDOFF.** Encarregar o doc-writer de atualizar o HANDOFF em toda entrega, junto com o README.
6. **Subagentes não deixam arquivos soltos.** Saídas temporárias de verificação vão para a pasta temporária do sistema, nunca para a raiz do repositório.
7. **Paridade local × CI.** Se um teste depende de algo do ambiente local (venv, caminho), o CI precisa reproduzir esse ambiente.
8. **`/clear` entre etapas.** Fechar a conversa ao fim de cada módulo commitado e reabrir a partir do HANDOFF.

## Levando para a NovaBank

**[completar]** O que muda com PySpark, Delta Lake e Databricks:
- Os testes "sem rede" viram testes sem cluster (SparkSession local? fixtures pequenas?).
- O equivalente ao `ON CONFLICT` é o `MERGE` do Delta.
- O que do agendamento no Windows deixa de existir, com Jobs do Databricks no lugar?
- Quais subagentes novos fazem sentido, se algum?
