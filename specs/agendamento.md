# Especificação — Agendamento Diário (Agendador de Tarefas do Windows)

## Status
Quinto "módulo" do projeto, e o primeiro que não é Python: dois scripts
PowerShell em `scripts/`, para agendar `python -m indicadores` no
Agendador de Tarefas do Windows. O pipeline e a CLI já existem e estão
prontos (lidos diretamente de `specs/pipeline.md` e
`src/indicadores/__main__.py` para confirmar exit codes e canais de
I/O): exit codes `0` (sucesso total), `1` (alguma série ignorada), `2`
(uso de CLI inválido, via `argparse`), `3` (erro inesperado) e `4`
(dados gravados, mas o relatório HTML não pôde ser gerado — ver
`specs/relatorio.md`); resumo sempre em stdout, logs sempre em stderr.

Atualização (09/10/2026): o usuário decidiu ver o relatório HTML no
celular via OneDrive; isso gerou a decisão 8 e a seção "Cópia do
relatório para a pasta de destino", que acrescentam uma etapa final ao
wrapper. O desenho dessa etapa (regras de quando copiar, destino,
atomicidade, tratamento de falha) foi proposto pelo orquestrador a partir
do pedido do usuário e **ainda não foi aprovado explicitamente por ele**;
as dúvidas em aberto estão listadas em "Limitações conhecidas" e na
própria seção.

## Objetivo
Rodar `python -m indicadores` automaticamente, todo dia útil às 16:00,
com o usuário logado, registrando cada execução (comando, saída, exit
code, horário) em um log mensal legível, sem exigir que ninguém abra um
terminal manualmente. Ao final, quando o relatório HTML foi regenerado
nessa execução, o wrapper o copia para uma pasta de destino configurável
(padrão: uma pasta dentro do OneDrive), para que o usuário o veja no
celular (decisão 8; ver "Cópia do relatório para a pasta de destino").

Não há nenhuma lógica nova de negócio aqui — só agendamento e um
wrapper fino que invoca a CLI já pronta e captura sua saída de forma
segura no PowerShell 5.1 (que tem uma armadilha conhecida com
`2>&1` em executáveis nativos — ver "Captura de stdout/stderr"). Se o
pipeline terminar com exit `1` (alguma série falhou), o wrapper repete a
execução **uma única vez** depois de uma espera — ver "Nova tentativa
quando o pipeline termina com exit 1".

## Decisões já tomadas pelo usuário (não reabertas)
1. A tarefa roda às **16:00, de segunda a sexta** (era 19:00 até
   08/10/2026; ver decisão 7).
2. Roda **só com o usuário logado** (`LogonType Interactive`, sem pedir
   senha), com `StartWhenAvailable`: se o PC estava desligado no
   horário, a tarefa roda assim que possível após o logon.
3. A tarefa só inicia com rede disponível (`-RunOnlyIfNetworkAvailable`)
   — aprovado em 07/10/2026, depois de a execução de **02/10/2026**,
   logo após o PC acordar da suspensão, falhar nas 3 séries por erro de
   conexão (exit `1`): a rede ainda não estava pronta, e o retry interno
   da extração (`MAX_TENTATIVAS = 3`, backoff de 0,5 s + 1,0 s) cobre só
   cerca de 1,5 s de espera por série.
4. O wrapper faz **uma única nova tentativa** quando o pipeline termina
   com exit `1`, depois de uma espera (padrão 300 s) — mesma aprovação
   e mesmo motivo do item 3. O exit `3` **não** dispara nova tentativa
   (ver "Nova tentativa quando o pipeline termina com exit 1"); o exit
   `4` (falha só do relatório) também não.
5. A tarefa usa `-WakeToRun`, para acordar o PC da suspensão no
   horário — com as condições do notebook do usuário registradas em
   `agendar_tarefa.ps1` (Modern Standby; despertadores só na tomada).
6. O `ExecutionTimeLimit` da tarefa é de **60 minutos** (era 30).
7. O horário passou de **19:00 para 16:00** (decisão de 08/10/2026).
   Motivo: nesse dia o notebook estava em espera (Modern Standby) às
   19:00; o `-WakeToRun` não acordou o PC (só funciona na tomada); a
   tarefa rodou atrasada, às 20:58, quando o PC acordou, e foi
   interrompida com `0xC000013A` (`STATUS_CONTROL_C_EXIT`) antes de
   escrever o log. Às 16:00 o PC costuma estar ligado e em uso, o que
   evita depender do despertar. O BCB já publica a PTAX do dia por volta
   das 13h, então 16:00 continua capturando o dado do dia. O
   `-WakeToRun` e as demais configurações permanecem como estão.
8. **O relatório HTML deve poder ser visto no celular via OneDrive**
   (decisão do usuário de **09/10/2026**). Consequência nesta spec: ao
   final do wrapper, depois da última tentativa do pipeline, o
   `relatorio.html` da raiz do repositório é **copiado** para uma pasta de
   destino (variável `INDICADORES_DESTINO_RELATORIO`; se ausente,
   `%OneDrive%\indicadores-bcb`), desde que tenha sido regenerado nesta
   execução e o exit final seja `0` ou `1`. A cópia é atômica, acontece
   uma única vez, e **uma falha nela nunca altera o exit code do wrapper
   nem dispara nova tentativa** — só vira `AVISO` no log. Detalhes e
   motivos na seção "Cópia do relatório para a pasta de destino". A parte
   decidida pelo usuário é o objetivo (ver no celular, via OneDrive); as
   regras detalhadas foram definidas pelo orquestrador e aprovadas em
   09/10/2026, com estas resoluções: troca atômica com
   `[IO.File]::Replace` quando o destino existe (terceiro argumento
   `[NullString]::Value`, pois `$null` vira string vazia no PowerShell
   5.1) e `[IO.File]::Move` quando não existe; destino relativo em
   `INDICADORES_DESTINO_RELATORIO` não é copiado e gera `AVISO` (o caminho
   precisa ser absoluto); exit `1` com relatório não regenerado não copia.

## Localização
```
scripts/executar_diario.ps1     # wrapper chamado pela tarefa agendada
scripts/agendar_tarefa.ps1       # registra/remove a tarefa no Agendador
```
Testes em `tests/test_scripts.py`. `README.md`/`HANDOFF.md` recebem uma
seção de agendamento — conteúdo do `doc-writer`, não desta spec (só
citado aqui como consumidor destes scripts).

## `scripts/executar_diario.ps1`

### Responsabilidade
Wrapper fino, chamado pela tarefa agendada (ou manualmente, para
depurar): resolve o Python do `.venv`, ajusta variáveis de ambiente,
executa `python -m indicadores <args>`, grava tudo (cabeçalho, stdout,
stderr, exit code, rodapé) no log mensal, e termina com o mesmo exit
code do processo Python (ou um código de infraestrutura distinto — ver
"Exit codes do wrapper" — se o problema for antes de conseguir rodar o
Python). Se o Python terminar com exit `1`, repete a execução uma única
vez após uma espera, e o exit code final é o da última tentativa
executada. Depois da última tentativa, e antes do rodapé, copia o
relatório HTML para a pasta de destino quando as condições da seção
"Cópia do relatório para a pasta de destino" se cumprem — sem nunca mudar
o exit code por causa disso.

### Resolução da raiz do repositório e do Python
```powershell
$repoRoot  = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
```
Se `$pythonExe` não existir (`Test-Path`): registra o erro no log
(usando a mesma rotina de log deste script, sem chegar a chamar
`Start-Process`) e termina com o exit code de infraestrutura `10` (ver
tabela abaixo) — nunca tenta rodar um Python inexistente.

### Variáveis de ambiente
Antes de iniciar o processo Python:
```powershell
$env:PYTHONPATH       = Join-Path $repoRoot "src"
$env:PYTHONUTF8       = "1"
$env:PYTHONIOENCODING = "utf-8"
```
Decisão registrada: definir **as duas** (`PYTHONUTF8` e
`PYTHONIOENCODING`), não só uma — `PYTHONUTF8=1` força UTF-8 como
codificação padrão do interpretador (stdio inclusive, em Python 3.7+);
`PYTHONIOENCODING=utf-8` é o mecanismo mais antigo e específico para
stdio, redundante com `PYTHONUTF8` nas versões recentes, mas mantido
como reforço/compatibilidade, já que o custo de definir os dois é nulo
e a consequência de esquecer é o resumo (que contém acentos, ex.
"séries", "atualizados") corromper no log. Como essas variáveis são
setadas no processo PowerShell **antes** de `Start-Process`, o processo
filho as herda normalmente (comportamento padrão de herança de
ambiente do Windows, não precisa de flag adicional).

Variáveis de ambiente **lidas** pelo wrapper (não repassadas ao Python):
- `INDICADORES_LOG_DIR` — diretório do log (ver "Log mensal"); pensada
  para os testes.
- `INDICADORES_ESPERA_RETRY_SEGUNDOS` — espera antes da nova tentativa
  (ver "Nova tentativa quando o pipeline termina com exit 1"); pensada
  para os testes.
- `INDICADORES_DESTINO_RELATORIO` — pasta de destino da cópia do
  relatório (ver "Cópia do relatório para a pasta de destino"). **Ao
  contrário das duas anteriores, é configuração de uso real**, não só de
  teste (os testes a usam para apontar para `tmp_path`).
- `OneDrive` — variável padrão do Windows (pasta raiz do OneDrive do
  usuário), lida só como destino padrão da cópia do relatório; o wrapper
  nunca a define.

### Repasse de argumentos extras
O script não declara nenhum `param()` próprio — tudo que vier depois
de `-File scripts\executar_diario.ps1` cai na variável automática `$args`
do PowerShell, e é repassado integralmente para
`python -m indicadores`:
```powershell
$argumentosPython = @("-m", "indicadores") + $args
```
Isso é o que permite `... -File executar_diario.ps1 --help` testar o
wrapper inteiro (incluindo a gravação do log) sem tocar na API do BCB —
`--help` do `argparse` sai com exit code `0` sem fazer nenhuma
chamada de rede. Na nova tentativa (se houver), os **mesmos**
`$argumentosPython` são usados, sem nenhuma alteração.

**Ponto a confirmar pelo implementer, registrado como suposição não
verificada nesta spec:** o comportamento exato de
`powershell.exe -File script.ps1 --help` no Windows PowerShell 5.1 —
i.e., se `--help` (e argumentos parecidos, como `--series`, que também
começam com `-`) chegam intactos em `$args` em vez de o próprio
PowerShell tentar interpretá-los como um parâmetro nomeado do script.
A expectativa (baseada no comportamento documentado do PowerShell: só
tokens que colidem com um `param()` **declarado** no script são
vinculados como parâmetros nomeados; sem `param()` declarado, tudo vira
`$args`) é que funcione sem problema, mas isso precisa ser confirmado
rodando de fato no ambiente alvo — é exatamente o que o caso de teste 1
faz.

### Aspas em argumentos com espaço (decisão registrada, com motivo)
No Windows PowerShell 5.1, `Start-Process -ArgumentList <array>`
**junta os elementos do array com um espaço simples**, sem colocar
aspas ao redor de cada um automaticamente. Isso quebra argumentos cujo
próprio valor contém espaço — por exemplo,
`--banco "C:\pasta com espaço\x.duckdb"` viraria, sem tratamento,
`--banco C:\pasta com espaço\x.duckdb` na linha de comando efetiva do
processo filho, e o executável nativo (que usa `CommandLineToArgvW`
para dividir a linha de volta em argumentos) enxergaria `com` e
`espaço\x.duckdb` como argumentos posicionais separados, não como parte
de um único valor de `--banco`.

Solução adotada: antes de montar `$argumentosPython`, cada elemento que
contém espaço (`" "`) ou aspas duplas (`"`) é envolvido em aspas duplas,
com as aspas internas escapadas conforme as regras do próprio
`CommandLineToArgvW` (aspas duplas internas viram `\"`; uma sequência de
barras invertidas imediatamente antes de uma aspas dupla precisa ser
duplicada, para não ser interpretada como escape da aspas em vez de
barras literais) — o mesmo algoritmo de quoting que `Start-Process`/o
runtime do .NET já usam internamente para os próprios argumentos que
ele controla, aplicado aqui manualmente porque `-ArgumentList` não faz
isso pelos elementos de um array repassado como está. Elementos sem
espaço nem aspas (a maioria: `-m`, `indicadores`, `--series`, `432`)
passam sem nenhuma alteração.

### Captura de stdout/stderr (decisão registrada, com motivo)
**Não usa** `& $pythonExe -m indicadores @argumentosPython 2>&1`.
Motivo: no Windows PowerShell 5.1, redirecionar o stream 2 (`stderr`)
de um **executável nativo** (não um cmdlet) com `2>&1` embrulha **cada
linha** de stderr num objeto `ErrorRecord` do PowerShell (não uma
string simples); se o script também tiver
`$ErrorActionPreference = "Stop"` (comum em scripts "seguros"), cada uma
dessas linhas recebidas como `ErrorRecord` é tratada como um erro que
**interrompe o script no meio da execução**, mesmo quando o processo
Python está simplesmente logando normalmente em stderr (não falhando).
Esse é um problema conhecido e bem documentado do PowerShell 5.1 com
`2>&1` em processos nativos.

Solução adotada — `Start-Process` com arquivos temporários:
```powershell
$stdoutTmp = [System.IO.Path]::GetTempFileName()
$stderrTmp = [System.IO.Path]::GetTempFileName()
try {
    $processo = Start-Process -FilePath $pythonExe `
        -ArgumentList $argumentosPython `
        -WorkingDirectory $repoRoot `
        -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $stdoutTmp `
        -RedirectStandardError $stderrTmp
    $exitCodePython = $processo.ExitCode

    try {
        $stdoutTexto = Get-Content -Raw -Encoding UTF8 -Path $stdoutTmp
        $stderrTexto = Get-Content -Raw -Encoding UTF8 -Path $stderrTmp
    } catch {
        $stdoutTexto = $stdoutTexto  # o que já tiver sido lido, se algo
        $stderrTexto = $stderrTexto  # foi lido antes de falhar
        $avisoLeitura = "AVISO: falha ao ler stdout/stderr temporários: $_"
    }
} finally {
    Remove-Item -Path $stdoutTmp, $stderrTmp -ErrorAction SilentlyContinue
}
```
`Start-Process -RedirectStandardOutput/-RedirectStandardError` grava a
saída do processo nativo diretamente em arquivo, sem passar pelo
pipeline de objetos do PowerShell — não há `ErrorRecord` nenhum
envolvido, então `$ErrorActionPreference` (seja qual for) não interfere
na captura. Os arquivos temporários são lidos depois que o processo já
terminou (`-Wait`) e apagados no `finally`, então nunca sobra lixo em
`%TEMP%` mesmo se a leitura falhar. Este bloco descreve **uma**
execução do Python; quando há nova tentativa, o bloco inteiro roda de
novo, com **seu próprio par** de arquivos temporários, apagado no seu
próprio `finally` (nenhuma tentativa reaproveita ou herda os arquivos
da anterior).

Decisão registrada — **falha ao ler os temporários vira `AVISO`, não
muda o exit code:** se `Get-Content` nos arquivos temporários falhar
(cenário raro — ex. antivírus segurando o arquivo por um instante), o
wrapper grava uma linha `AVISO: ...` no log em vez do conteúdo de
stdout/stderr faltante, mas **continua** usando `$exitCodePython`
(já capturado antes, de `$processo.ExitCode`) como exit code final.
Motivo: o resultado do pipeline (o exit code) já é conhecido nesse
ponto, independentemente de conseguir ler os arquivos de log
temporários depois — uma falha de leitura é uma perda de informação de
diagnóstico, não uma falha da execução em si, então não deveria
mascarar um exit code de sucesso nem inflar artificialmente a
severidade de um exit code de erro que já seria reportado de qualquer
forma. (Isso é diferente de uma falha ao **criar** os temporários, que
cai no exit `13` — ver "Exit codes do wrapper".)

### Log mensal
- Caminho do diretório de log: `$env:INDICADORES_LOG_DIR`, se definida;
  senão, `Join-Path $repoRoot "logs"`. Decisão: variável de ambiente
  existe **só para os testes** apontarem para `tmp_path` sem escrever
  no repositório real.
- Nome do arquivo: `execucao_<AAAA-MM>.log` (`Get-Date -Format
  "yyyy-MM"`), um arquivo por mês — cada execução diária **acrescenta**
  (append) ao arquivo do mês corrente; o diretório é criado com
  `New-Item -ItemType Directory -Force` se ainda não existir.
- Codificação: **UTF-8 sem BOM**, escrita via .NET diretamente
  (`[System.IO.File]::AppendAllText($caminhoLog, $texto, (New-Object
  System.Text.UTF8Encoding($false)))`), não via `Add-Content
  -Encoding utf8`/`Out-File -Encoding utf8`. Decisão e motivo: no
  Windows PowerShell 5.1, `-Encoding utf8` nesses cmdlets grava
  UTF-8 **com BOM**, e o comportamento exato de reescrever (ou não) o
  BOM a cada chamada em modo *append* não é garantido de forma
  consistente entre versões/cmdlets — para não arriscar acumular BOMs
  no meio do arquivo (o que quebraria a leitura de texto simples em
  alguns visualizadores/parsers), a spec fixa o uso direto da API .NET
  com um `UTF8Encoding` configurado explicitamente **sem** BOM
  (`$false`), que tem semântica de append clara e testável.
- Conteúdo de cada execução, **num único bloco**, nesta ordem:
  1. Cabeçalho: `===== <AAAA-MM-DD HH:mm:ss> - início =====`.
  2. Logo depois do cabeçalho, **se** `INDICADORES_ESPERA_RETRY_SEGUNDOS`
     estiver definida (e não vazia) com valor inválido: uma linha
     `AVISO: valor inválido em INDICADORES_ESPERA_RETRY_SEGUNDOS ('<valor>'); usando 300`
     (ver "Nova tentativa quando o pipeline termina com exit 1").
  3. Bloco `--- stdout (tentativa 1) ---` seguido do `$stdoutTexto` (o
     resumo do pipeline, quando a execução chega a rodar o Python).
  4. Bloco `--- stderr (tentativa 1) ---` seguido do `$stderrTexto` (os
     logs do pipeline), ou a linha `AVISO: ...` no lugar, se a leitura
     falhou (ver "Captura de stdout/stderr").
  5. **Somente se houve nova tentativa:** a linha
     `AVISO: exit 1; nova tentativa em <N> s`, seguida de
     `--- stdout (tentativa 2) ---` + texto e
     `--- stderr (tentativa 2) ---` + texto (mesmas regras de leitura e
     de `AVISO` por falha de leitura dos blocos da tentativa 1).
  6. **Somente se o Python chegou a rodar e o exit final é `0` ou `1`:**
     as linhas da etapa de cópia do relatório (ver "Cópia do relatório
     para a pasta de destino"): `Relatório copiado para: <caminho>`, ou
     `Relatório não copiado: ...` (informativa), ou
     `AVISO: relatório não copiado: ...` / `AVISO: falha ao copiar
     relatório para '<destino>': <mensagem>`. Sempre **depois** dos blocos
     de stdout/stderr da última tentativa e **antes** do rodapé.
  7. Rodapé: `===== <AAAA-MM-DD HH:mm:ss> - fim (exit code: <N>)
     =====`, com o exit code **final** (o da última tentativa
     executada; a etapa de cópia nunca o altera).
  Se o wrapper falha **antes** de conseguir rodar o Python (ex.: venv
  ausente), os blocos de stdout/stderr do Python são omitidos, e o
  rodapé registra o exit code de infraestrutura correspondente com uma
  linha de erro explicando o motivo.
- Decisão registrada: **os blocos de ambas as tentativas são rotulados
  com o número da tentativa** — `--- stdout (tentativa N) ---` e
  `--- stderr (tentativa N) ---`, **inclusive a tentativa 1**, mesmo
  quando não há nova tentativa. Motivo: formato uniforme, sem lógica
  especial por caso, e um log com uma única execução já é lido do mesmo
  jeito que um com duas. Consequências para os testes: "tentativa 1"
  aparece sempre que o Python chegou a rodar; "tentativa 2" só aparece
  quando houve de fato uma nova tentativa; "tentativa 3" nunca aparece.
  (A linha `AVISO: exit 1; nova tentativa em <N> s` contém "nova
  tentativa" mas **não** a expressão "tentativa 2".)
- Decisão registrada: o conteúdo da tentativa 1 (blocos stdout/stderr) e
  a linha de `AVISO` da nova tentativa são **gravados no log antes** da
  espera. Assim, se o processo do wrapper for encerrado durante a espera
  (ex.: logoff ou desligamento — ver "Limitações conhecidas"), a
  evidência da primeira tentativa já está no arquivo; nesse caso o bloco
  fica sem rodapé ("fim"), o que por si só indica uma execução
  interrompida.
- Se o próprio preparo do log falhar (ver "Exit codes do wrapper",
  código `12`), não existe rodapé nem cabeçalho nenhum — o arquivo de
  log pode nem existir; a mensagem de erro vai para **stderr** do
  processo do wrapper, não para um arquivo.
- Se ocorrer um erro inesperado **depois** de o log ter sido preparado
  (ver código `13`), o wrapper tenta (melhor esforço) gravar no log uma
  linha `ERRO: <mensagem>` e o rodapé com `exit code: 13`; se essa
  própria gravação falhar (ex.: disco cheio), a mensagem vai para
  **stderr** do processo do wrapper.

### Exit codes do wrapper
| Código | Situação |
|---|---|
| `0`–`4` | Repassado **exatamente** como veio do processo Python (`$processo.ExitCode`) da **última tentativa executada** — mesma semântica de `specs/pipeline.md`/`__main__.py` (o `4` é "dados gravados, relatório não gerado", definido em `specs/relatorio.md`). Só o exit `1` provoca uma nova tentativa; `0`, `2`, `3` e `4` encerram o wrapper de imediato. O wrapper **não muda** por causa do `4`: ele já repassa qualquer inteiro como veio e só repete no `1`. A etapa de cópia do relatório (decisão 8) **não altera** este código em nenhuma hipótese. |
| `10` | `.venv\Scripts\python.exe` não encontrado — infraestrutura local ausente, o Python nunca chega a rodar. |
| `11` | Falha ao iniciar o processo Python via `Start-Process` (ex.: exceção do próprio PowerShell antes de conseguir spawnar o processo); **ou** `$processo.ExitCode` vem `$null` — cenário raro relatado em alguns ambientes de PowerShell 5.1 com `-PassThru`. Vale para qualquer das tentativas (inclusive a segunda: se a nova tentativa não consegue nem iniciar, o exit final é `11`, não `1`). |
| `12` | Não foi possível **preparar o log**: falha ao criar o diretório de log (`New-Item -ItemType Directory`) ou falha ao gravar o cabeçalho inicial (a primeira escrita via `AppendAllText`). Nesse caso a mensagem de erro vai para **stderr** do próprio wrapper, porque o arquivo de log não existe ou não pôde ser preparado para receber a mensagem. |
| `13` | **Erro inesperado no wrapper depois que o log foi preparado** — por exemplo disco cheio ao gravar no log, falha ao criar um arquivo temporário (`GetTempFileName`) ou qualquer outra exceção não tratada depois da gravação do cabeçalho. O wrapper registra `ERRO: <mensagem>` e o rodapé no log (melhor esforço; se a gravação no log falhar, a mensagem vai para stderr, como no `12`) e termina com `13`. Se o exit code do Python da tentativa em curso já era conhecido, a mensagem o informa ("exit do pipeline: N"), para não perder essa informação. A etapa de cópia do relatório **não** pode produzir este código: ela tem `try/catch` próprio (ver "Cópia do relatório para a pasta de destino"). |

Decisão registrada: os códigos de infraestrutura do **wrapper**
começam em `10`, deliberadamente fora da faixa `0`–`4` já usada pelo
Python (`specs/pipeline.md`), para que quem olhar o "Resultado da
última execução" no Agendador de Tarefas consiga distinguir, só pelo
número, "o pipeline rodou e teve um problema" (`0`–`4`) de "o wrapper
nem conseguiu rodar o pipeline" (`≥ 10`).

Decisão adicional registrada — **`ExitCode` nulo nunca é sucesso**: o
wrapper testa explicitamente `if ($null -eq $processo.ExitCode)` logo
depois do `Start-Process`, e trata esse caso como falha própria do
wrapper (`exit 11`), nunca como se fosse `exit 0`. Motivo: em alguns
cenários conhecidos do PowerShell 5.1, `$processo.ExitCode` de um
objeto retornado por `-PassThru` pode vir `$null` (em vez do código
real do processo) mesmo depois de `-Wait`; qualquer valor
"desconhecido" não pode ser tratado como sucesso silencioso — silenciar
esse caso como `0` esconderia justamente os casos em que o wrapper não
tem certeza do que aconteceu, o que é pior do que reportar um erro de
infraestrutura explícito.

Decisão adicional registrada — **falha ao preparar o log vira `exit
12`, não o código genérico `1` do PowerShell**: se `New-Item` (criar o
diretório de log) ou a primeira escrita do cabeçalho falharem e a
exceção não for tratada explicitamente, o PowerShell terminaria o
script com o exit code genérico `1` — que colidiria diretamente com o
significado já estabelecido de `1` no restante desta spec ("alguma
série foi ignorada pelo pipeline"), tornando ambíguo o "Resultado da
última execução" no Agendador. Por isso o preparo do log é envolto em
seu próprio `try/catch`, que em caso de falha escreve a mensagem em
stderr (via `[Console]::Error.WriteLine(...)`, já que não há arquivo de
log disponível) e termina explicitamente com `exit 12`, mantendo a
mesma lógica de faixa dedicada (`≥ 10`) para problemas de
infraestrutura do próprio wrapper.

Decisão adicional registrada — **erro inesperado depois do log vira
`exit 13`, pelo mesmo motivo**: tudo o que o wrapper faz **depois** de
gravar o cabeçalho (variável de espera, criação dos temporários,
`Start-Process`, leitura, gravação dos blocos no log, espera, nova
tentativa, rodapé) fica dentro de um `try/catch` de nível mais alto, que
converte qualquer exceção não tratada em `exit 13`. Sem isso, o
PowerShell terminaria com o exit `1` genérico — ambíguo com "série
ignorada" (`1`) do pipeline, exatamente o problema que o `12` já
resolve para a fase de preparo do log. Os `exit` explícitos dos demais
caminhos (`10`, `11`, e o repasse de `0`–`4`) não são exceções e não
passam por esse `catch`; os `finally` que apagam os temporários
continuam rodando (comportamento já confirmado — ver "Limitações
conhecidas"). Com `12` e `13`, **todo** exit `1` observado pelo wrapper
vem do pipeline Python, nunca de uma falha genérica do PowerShell —
pré-condição para o retry abaixo ser disparado só pelo motivo certo.

### Nova tentativa quando o pipeline termina com exit 1 (decisão registrada, com motivo)

**Contexto.** Em **02/10/2026**, uma execução logo depois de o PC acordar
da suspensão falhou nas 3 séries por erro de conexão (exit `1`): a rede
ainda não estava pronta, e o retry da extração cobre só cerca de 1,5 s
de espera por série (`MAX_TENTATIVAS = 3`, backoff de 0,5 s + 1,0 s,
constantes fixas de `extracao.py` que não são configuráveis pela CLI).
Duas medidas complementares foram aprovadas: a condição de rede da
tarefa (`-RunOnlyIfNetworkAvailable`, em `agendar_tarefa.ps1`) e esta
nova tentativa no wrapper, que cobre o intervalo em que o Windows já
declara a rede disponível mas DNS/rota ainda não funcionam.

**Regras.**
- Se a execução do Python terminar com exit **`1`**, o wrapper:
  1. grava no log os blocos stdout/stderr da tentativa 1 (se ainda não
     gravados) e a linha `AVISO: exit 1; nova tentativa em <N> s`;
  2. espera `<N>` segundos (`Start-Sleep -Seconds <N>`);
  3. roda o pipeline **uma única vez** de novo, com os **mesmos
     argumentos** (`$argumentosPython`), seguindo o mesmo procedimento
     de captura (par próprio de arquivos temporários, mesmas regras de
     `AVISO` de leitura);
  4. grava a segunda execução no **mesmo bloco** do log, com os seus
     próprios trechos `--- stdout (tentativa 2) ---` e
     `--- stderr (tentativa 2) ---`;
  5. usa como exit code final o da segunda tentativa (que pode ser `0`,
     `1`, `2`, `3`, `4` ou, se ela nem conseguir iniciar, `11`).
- Nunca há terceira tentativa, mesmo que a segunda também termine com
  exit `1`.
- **Nunca há nova tentativa** para exit `0` (sucesso), `2` (uso de CLI
  inválido — repetir não muda nada), `3`, `4` nem para os códigos do
  próprio wrapper (`10`, `11`, `12`, `13`, que encerram antes ou sem
  resultado confiável do Python).
- **Exit `4` não repete:** os dados já foram gravados e a falha é só do
  relatório (não é de rede), então rebuscar a API não ajuda; repetir todo
  o pipeline por isso seria custo sem benefício (motivo completo em
  `specs/relatorio.md`, "Integração na CLI", decisão 1 e 2).
- **Exit `3` não repete (decisão do usuário):** um banco bloqueado (ou
  outro erro inesperado) raramente se resolve em 5 minutos, e repetir
  esconderia bugs — o exit `3` sinaliza algo que deve aparecer no
  "Resultado da última execução" e no log, não ser mascarado por uma
  segunda execução que talvez termine com `0` por acaso.
- **Segurança da repetição:** é segura graças ao upsert idempotente de
  `persistencia.gravar` — o pipeline sempre rebusca a janela dos
  últimos 5 anos e faz upsert por `(codigo, data)`; séries que já tinham
  sido gravadas com sucesso na tentativa 1 são apenas "atualizadas" de
  novo (mesmos valores, `atualizado_em` novo), sem duplicar nada, e a
  tentativa 1 já terminou (o processo encerrou) antes da tentativa 2
  começar, então não há conflito de arquivo DuckDB aberto por dois
  processos.
- **Limitação aceita:** o exit `1` não distingue falha transitória (rede
  ainda subindo) de permanente (ex. uma série que passou a responder 404
  sempre). Nos casos permanentes a nova tentativa só custa o tempo da
  espera (+ a duração da execução), sem efeito colateral; o resultado
  final continua sendo `1`.

**Espera `N` e a variável `INDICADORES_ESPERA_RETRY_SEGUNDOS`.**
- Padrão: **300** segundos (5 minutos).
- Pode ser alterada pela variável de ambiente
  `INDICADORES_ESPERA_RETRY_SEGUNDOS`; os testes a definem como `0`.
- **Validação estrita, logo depois do cabeçalho do log:** a variável é
  lida e validada **imediatamente depois de gravar o cabeçalho**, em
  toda execução (não só quando a nova tentativa é necessária).
  - **Ausente ou vazia** → usa 300, **sem** aviso (é o caso normal em
    produção). O teste de "vazia/ausente" é feito **antes** do parse,
    para uma string vazia não gerar aviso.
  - **Valor válido** = só dígitos (`0`, `5`, `300`), interpretado de
    forma independente de cultura. Implementação sugerida:
    `[int]::TryParse($texto, [System.Globalization.NumberStyles]::None,
    [System.Globalization.CultureInfo]::InvariantCulture, [ref]$n)`.
    `NumberStyles.None` não aceita sinal, espaços nas pontas, separador
    decimal nem de milhar; estouro de `int` também falha.
  - **Definida e inválida** → cai no padrão 300 **e** o wrapper grava no
    log a linha
    `AVISO: valor inválido em INDICADORES_ESPERA_RETRY_SEGUNDOS ('<valor>'); usando 300`,
    com `<valor>` exatamente como recebido (espaços preservados).
    Exemplos inválidos: `"+5"`, `" 5 "`, `"-1"`, `"abc"`, `"1.5"`.
- Motivo de validar logo no início: o `AVISO` de valor inválido aparece
  no log de qualquer execução, e a validação pode ser testada sem
  esperar 300 s (basta rodar com `--help`, que termina com exit `0` e
  nunca chega a precisar da espera).
- Não há teto imposto para `N`; ver a conta abaixo para o limite
  prático.

**Conta do pior caso (cabe no `ExecutionTimeLimit` de 60 minutos?).**
Premissas, lidas de `extracao.py`: `SGS_TIMEOUT = httpx.Timeout(10.0)`
— o timeout do httpx vale **por operação** (conexão, escrita, leitura e
espera no pool, cada uma com seu próprio limite de 10 s), então uma
única requisição pode, em tese, levar até ~30 s (modelo conservador
usado aqui: 3 operações x 10 s). `MAX_TENTATIVAS = 3`, backoff `0,5 s +
1,0 s`; as séries são buscadas em sequência.
```
Por série, por execução (conservador):
    3 requisições x 30 s + 0,5 s + 1,0 s             =  91,5 s
Por execução do pipeline, com n séries:
    91,5 s x n + ~5 s (Python/pandas/duckdb, limpeza e gravação)
Pior caso do wrapper (2 execuções + espera N):
    2 x (91,5 x n + 5) + N     =   183 x n + 10 + N

Hoje (n = 3, N = 300):
    2 x (274,5 + 5) + 300                            =  859 s  (~ 14 min 19 s)
ExecutionTimeLimit: 60 min                           = 3600 s
Folga                                                = 2741 s  (~ 45 min 41 s)
```
Referência otimista (cada requisição estourando só 10 s): `2 x (3 x 31,5 +
5) + 300 = 499 s` (~ 8 min 19 s). **Conclusão: o padrão de 300 s cabe
com ampla folga.** Cada série nova adicionada ao registro `SERIES` soma
cerca de **91 s por execução** no modelo conservador, vezes 2 tentativas
= ~183 s ao pior caso do wrapper; com `N = 300`, o limite de 3600 s só
estoura a partir de **18 séries** (`183 x 17 + 310 = 3421 s` cabe;
`183 x 18 + 310 = 3604 s` não). Com 3 séries, um `N` acima de ~3000 s
(50 min) pode estourar o limite; como não há teto imposto, essa
responsabilidade é de quem altera a variável (uso previsto: só testes,
com `0`). A conta não cobre uma resposta que chegue aos poucos (o
timeout de leitura reinicia a cada pedaço recebido): nesse caso o
`ExecutionTimeLimit` é a rede de segurança final. (A cópia do relatório,
que acontece depois, copia um arquivo local de algumas centenas de KB e
não muda esta conta de forma relevante; ver "Limitações conhecidas" sobre
uma cópia que trave.)

### Cópia do relatório para a pasta de destino (decisão 8, 09/10/2026)

**Contexto e motivo.** O usuário quer ver o `relatorio.html` no celular
via OneDrive. O wrapper é o único ponto que já roda todo dia útil, no
usuário logado, e que conhece o resultado final do pipeline; acrescentar
uma etapa final de cópia evita uma segunda tarefa agendada e mantém a
regra de que a CLI/o pipeline não sabem nada de OneDrive.
Alternativas descartadas: (a) apontar `--relatorio` da CLI direto para a
pasta do OneDrive — acopla o pipeline a um caminho do usuário, desfaz o
default `relatorio.html` na raiz (`specs/relatorio.md`) e deixaria o
relatório sem cópia local se a pasta estivesse indisponível; (b) uma
segunda tarefa agendada só para copiar — dobra os pontos de falha e o
risco de copiar um arquivo velho; (c) servir o relatório por HTTP — fora
de escopo em `specs/relatorio.md`.

"OneDrive" é **só o destino padrão**: não há nada específico de OneDrive
no código além de ler `$env:OneDrive` para montar esse padrão. Qualquer
outra pasta (Dropbox, pasta de rede, pen drive) funciona definindo
`INDICADORES_DESTINO_RELATORIO`. O wrapper só copia um arquivo local;
nenhuma credencial, token ou chamada de rede é usada.

**Quando copiar (todas as condições).** A etapa roda **uma única vez**,
depois da **última** tentativa (nunca entre a tentativa 1 e a 2) e antes
do rodapé:
1. o Python chegou a rodar e o exit final é `0` ou `1`. Exit `1` também
   copia porque o pipeline gera o relatório mesmo com série ignorada
   (`specs/relatorio.md`, "Integração na CLI", decisão 6) e o banco guarda
   os dados das execuções anteriores, então o relatório continua útil.
   Exit `4` **não** copia (o relatório falhou); exit `2`, `3` e os códigos
   `10`, `11`, `12`, `13` **não** copiam (ou não houve relatório, ou o
   estado é duvidoso — mesmo raciocínio de `specs/relatorio.md`, decisão 5
   da integração);
2. existe `relatorio.html` na raiz do repositório (`$repoRoot`), e
3. `LastWriteTimeUtc` do arquivo é **maior ou igual** ao instante de
   início da execução do wrapper (`$inicioExecucaoUtc`, capturado
   com `[datetime]::UtcNow` no começo do script, antes do preparo do
   log). Motivo: um `relatorio.html` de um dia anterior nunca deve ser
   copiado como se fosse de hoje. Isso acontece, por exemplo, com
   `--sem-relatorio`, com `--so-relatorio` que falhou, com exit `1` em que
   a geração do relatório falhou (a falha fica só no log,
   `specs/relatorio.md`, decisão 11) ou com `--relatorio` apontando para
   outro caminho. A comparação usa o relógio da própria máquina nos dois
   lados (o instante de início e a data de modificação do arquivo).

Se o exit final é `0` ou `1` mas as condições 2 ou 3 falham, o wrapper
grava no log uma linha **informativa, sem prefixo `AVISO`**:
`Relatório não copiado: relatorio.html não foi regenerado nesta execução`,
e não faz mais nada (o destino nem é resolvido). Para os demais exit
codes, a etapa não escreve nada.

**Pasta de destino.** Resolvida nesta ordem, no momento da cópia:
1. `$env:INDICADORES_DESTINO_RELATORIO`, se definida e **não vazia**
   (`[string]::IsNullOrWhiteSpace` falso). É usada **como está** — a
   variável aponta para a pasta final; o wrapper não acrescenta
   `indicadores-bcb` a ela. Se a cópia para esse destino falhar, **não há
   fallback** para o OneDrive (um destino configurado explicitamente que
   falha deve aparecer no log, não ser trocado em silêncio).
2. senão, `Join-Path $env:OneDrive "indicadores-bcb"`, se `$env:OneDrive`
   estiver definida e não vazia.
3. senão, **não copia** e grava no log
   `AVISO: relatório não copiado: INDICADORES_DESTINO_RELATORIO não está definida e a variável OneDrive não existe`.

O destino é esperado como caminho absoluto; o tratamento de caminho
relativo não é especificado (ver dúvidas em aberto).

**Como copiar (atômica).** O cliente de sincronização não pode enviar um
arquivo pela metade, e um leitor (o celular) não pode ver um arquivo
truncado:
1. cria a pasta de destino se não existir (`New-Item -ItemType Directory
   -Force`), inclusive pastas intermediárias;
2. copia `relatorio.html` da raiz para `<destino>\relatorio.html.tmp`
   (`Copy-Item -Force`);
3. substitui `<destino>\relatorio.html` pelo temporário numa operação de
   renomeação (`Move-Item -Force`, ou `[System.IO.File]::Replace` quando o
   destino já existe e `Move` quando não existe — a escolha é do
   implementer, o critério é: **o nome final nunca contém um arquivo
   parcial**);
4. em qualquer falha nos passos 1–3, remove `<destino>\relatorio.html.tmp`
   (melhor esforço, erro ao remover suprimido) antes de registrar o
   `AVISO`. O `relatorio.html` anterior do destino fica intacto.
O nome do arquivo no destino é sempre `relatorio.html` (sem data no nome:
o celular sempre abre o mesmo arquivo; o "Gerado em" dentro do HTML
identifica a data). O temporário fica na **mesma pasta** do destino para
que a renomeação seja no mesmo volume.

**Log.** Em caso de sucesso: `Relatório copiado para: <caminho completo
do arquivo no destino>`. Em caso de falha:
`AVISO: falha ao copiar relatório para '<destino>': <mensagem>`, em que
`<destino>` é a **pasta** de destino resolvida e `<mensagem>` é
`$_.Exception.Message`. As linhas vão no mesmo bloco do log, entre os
blocos da última tentativa e o rodapé (ver "Log mensal").

**Isolamento de falhas (decisão registrada, com motivo).**
- A etapa inteira — resolução do destino, comparação de datas, cópia,
  limpeza **e a própria gravação das linhas no log** — fica num
  `try/catch` próprio, **dentro** do `try` de topo, de modo que nenhuma
  exceção dela chega ao `catch` que gera o exit `13`. Se a gravação de
  uma linha no log falhar dentro da etapa, o erro é suprimido (o próximo
  `Write-Log` do rodapé, se também falhar, cai no `13` de sempre — isso
  seria uma falha do log, não da cópia).
- Falha na cópia **não altera o exit code** do wrapper: os dados e o
  relatório local estão corretos, e o Agendador continua mostrando o
  resultado do pipeline (`0`–`4`). Uma falha só da cópia não deve nem
  mascarar um `0` com um código de erro, nem trocar o `1` por outro.
- Falha na cópia **não dispara nova tentativa**: o pipeline não tem nada
  a ver com isso (e a cópia só acontece depois da última tentativa). A
  próxima execução diária copia o relatório de novo.
- A cópia não tem timeout próprio (um arquivo local de poucas centenas de
  KB); o `ExecutionTimeLimit` da tarefa é a rede de segurança final.

**Relação com os exit codes.** A tabela de "Exit codes do wrapper" não
muda: a etapa nunca produz `10`–`13` nem altera um `0`–`4`.

### Limitações conhecidas
- **`finally` e `exit` dentro de `try`/`catch`:** o bloco `finally` que
  apaga os arquivos temporários (`Remove-Item ... -ErrorAction
  SilentlyContinue`) roda normalmente mesmo quando o caminho de código
  correspondente termina o script com `exit <código>` de dentro de um
  `try`/`catch` — comportamento **confirmado** no Windows PowerShell
  5.1 (não é uma suposição em aberto: `exit` dentro de um `try` ainda
  aciona o `finally` correspondente antes de encerrar o processo,
  igual ao comportamento de `try`/`finally` de outras linguagens).
- **`ExecutionTimeLimit` do Agendador:** se o Agendador de Tarefas
  encerrar o processo **à força** por ultrapassar o
  `ExecutionTimeLimit` de 60 minutos (configurado em
  `agendar_tarefa.ps1`), o processo do wrapper é terminado
  externamente, e o `finally` que apagaria os arquivos temporários de
  stdout/stderr **não chega a rodar** — nesse cenário específico, os
  arquivos temporários em `%TEMP%` podem ficar para trás. Decisão:
  risco aceito, considerado baixo (o cenário exige que o próprio
  pipeline trave por mais de 60 minutos, o que já seria, por si só, um
  problema a ser investigado manualmente) e fora do controle do
  wrapper (o encerramento é externo, pelo sistema operacional/Agendador,
  não algo que um `finally` dentro do próprio processo consiga
  interceptar).
- **Parcialmente resolvido: PC suspenso no horário da tarefa (antes
  19:00, agora 16:00); não resolvido: PC desligado ou hibernado.**
  Histórico: com o horário original de 19:00, a tarefa dependia do
  `-WakeToRun` para acordar o PC da suspensão — mas, no notebook do
  usuário (Modern Standby, despertadores ativados só na tomada, plano
  "Equilibrado"), isso só acontece **na tomada**; na bateria a tarefa
  não acorda o PC e roda só depois que ele for retomado (via
  `StartWhenAvailable`, que no acompanhamento real disparou a tarefa
  logo depois de o PC acordar). Isso se confirmou em **08/10/2026**: o
  notebook estava em espera às 19:00, o `-WakeToRun` não acordou o PC, a
  tarefa rodou atrasada às 20:58, ao acordar, e foi interrompida com
  `0xC000013A` antes de escrever o log (ver também "tarefa encerrada no
  logoff ou no desligamento", abaixo). **Mitigação adotada:** o horário
  foi movido para **16:00** (decisão 7), faixa em que o PC costuma estar
  ligado e em uso, reduzindo a dependência do despertar; o `-WakeToRun`
  continua configurado, como reforço. A mitigação **reduz** o risco, mas
  não o elimina: se o PC estiver suspenso às 16:00 (e na bateria), o
  comportamento descrito acima se repete. Com o PC **desligado ou
  hibernado**, nada acontece: a execução do dia só ocorre depois que o
  PC ligar e o usuário estiver logado (`Interactive`), ou não ocorre
  naquele dia. Não há perda de dados permanente: cada execução rebusca
  os últimos 5 anos e faz upsert, então a execução seguinte recupera o
  que faltou. **Não verificado:** o que o PC faz **durante** a execução
  depois de acordar por um despertador — em Modern Standby ele pode
  voltar a suspender durante a espera de 300 s da nova tentativa; a
  confirmar em uso real, com o histórico do Agendador ativado. **Não
  verificado:** se 16:00 é de fato estável em uso real; a confirmar
  observando os logs das próximas semanas.
- **Não resolvido: tarefa encerrada no logoff ou no desligamento.**
  Como a tarefa roda no contexto do usuário logado (`Interactive`),
  fazer logoff ou desligar o PC durante a execução encerra o processo; o
  Agendador registra então o resultado `0xC000013A`
  (`STATUS_CONTROL_C_EXIT`, interrupção por evento de console),
  **observado em 07/10/2026** (e de novo em **08/10/2026**, na execução
  atrasada das 20:58). A espera de 300 s da nova tentativa
  aumenta a janela em que isso pode acontecer. Efeitos: o bloco do log
  fica sem rodapé ("fim"); a tentativa 1 já está gravada no log (ela é
  escrita antes da espera); os temporários podem ficar para trás (mesma
  limitação do `ExecutionTimeLimit`). A gravação é por série em
  transações próprias com upsert (`specs/pipeline.md`), então se espera
  que uma interrupção deixe no máximo séries já commitadas e a
  execução seguinte complete o restante — **não verificado por teste**.
  Se a interrupção ocorrer entre o fim do pipeline e o fim da cópia do
  relatório, o destino fica com o relatório anterior (ou um
  `relatorio.html.tmp` órfão na pasta de destino, que a cópia seguinte
  sobrescreve); o mesmo vale para os demais encerramentos forçados.
- **Não resolvido: histórico do Agendador desativado por padrão.** No
  Windows, o histórico de tarefas do Agendador vem desativado; sem ele,
  o Agendador só guarda o "Resultado da última execução" e não há trilha
  de execuções passadas nem de disparos que nem chegaram a iniciar o
  wrapper. O log mensal do wrapper é, portanto, a única trilha
  confiável — e só existe para execuções em que o wrapper chegou a
  rodar. `agendar_tarefa.ps1` **não** ativa o histórico: isso exige
  privilégio de administrador (log de eventos do Agendador), contra a
  decisão de `RunLevel Limited`. Orientação de como ativá-lo
  manualmente fica para o `doc-writer`.
- **`-RunOnlyIfNetworkAvailable` não garante internet funcionando, e é
  incerto no Modern Standby:** a condição só exige que o Windows
  considere haver uma conexão de rede (qualquer uma — a spec não fixa
  `NetworkName`/`NetworkId`). No Modern Standby, o Windows pode
  considerar a rede **conectada logo ao acordar**, antes de DNS e rota
  para a internet estarem prontos (cenário do incidente de 02/10/2026) —
  ou seja, a condição pode não atrasar nada nesse caso. A nova tentativa
  do wrapper, depois de 5 minutos, cobre esse caso; as duas medidas são
  complementares. **Não verificado:** o comportamento real (se a
  condição chega a atrasar o início, e por quanto tempo o Agendador
  espera a rede antes de desistir da execução do dia) deve ser
  confirmado em uso real, **com o histórico do Agendador ativado**.
- **Cópia do relatório: "copiado" não é "sincronizado" (decisão 8).** A
  linha `Relatório copiado para: ...` só garante que o arquivo está na
  pasta local de destino. O wrapper não verifica se o cliente de
  sincronização (OneDrive) enviou o arquivo, nem quando o celular o
  receberá; se o OneDrive estiver pausado, sem rede ou com a opção
  "arquivos sob demanda" no celular, o celular continua mostrando o
  relatório anterior até a sincronização. O "Gerado em" dentro do HTML
  mostra a data do relatório que está sendo visto.
- **Cópia do relatório: o `.tmp` pode ser sincronizado.** O
  `relatorio.html.tmp` existe na pasta sincronizada por alguns
  milissegundos e o cliente pode chegar a enviá-lo antes da renomeação.
  Aceito: o nome final nunca fica com conteúdo parcial; o `.tmp` é
  removido (ou renomeado) em seguida. Não verificado com o OneDrive real.
- **Cópia do relatório: substituição do arquivo existente.** Não está
  verificado, no Windows PowerShell 5.1, se `Move-Item -Force` sobre um
  arquivo existente é uma substituição atômica ou uma remoção seguida de
  renomeação (com uma janela mínima sem o arquivo). O implementer deve
  conferir; `[System.IO.File]::Replace` é a alternativa quando o destino
  existe. Em qualquer dos dois casos o nome final nunca fica com conteúdo
  parcial.
- **Cópia do relatório: arquivo bloqueado.** Se o cliente de
  sincronização, o navegador ou o app que abriu o arquivo no PC mantiver
  `relatorio.html` do destino aberto sem compartilhamento, a
  substituição falha: vira `AVISO` no log, sem nova tentativa; a próxima
  execução diária tenta de novo.
- **Cópia do relatório: variáveis de ambiente na tarefa agendada.** Para
  a tarefa (`Interactive`, `RunLevel Limited`) usar
  `INDICADORES_DESTINO_RELATORIO`, ela precisa existir no ambiente do
  usuário de forma persistente (variável de usuário) antes de o processo
  ser iniciado; `$env:OneDrive` normalmente existe na sessão do usuário.
  **Não verificado** que o processo iniciado pelo Agendador recebe essas
  variáveis (a verificar na primeira execução real: o log mostra
  `Relatório copiado para: ...` ou o `AVISO` de destino ausente).
- **Cópia do relatório: só a raiz do repositório.** O wrapper procura
  sempre `<raiz>\relatorio.html`. Se o usuário passar `--relatorio
  outro.html` ao wrapper, o arquivo da raiz não é regenerado e a etapa
  registra "não foi regenerado nesta execução" (nada é copiado).
- **Cópia do relatório: visualização no celular é verificação manual.**
  O app do OneDrive no celular pode exibir um arquivo HTML como texto ou
  oferecer só o download, em vez de renderizá-lo; a forma de ver o
  relatório pode exigir "Abrir em…" e escolher um navegador. O JS do
  tooltip (`specs/relatorio.md`) depende do navegador usado e de a
  visualização permitir scripts; o relatório continua legível sem JS.
  Nada disso é testável aqui. **Não verificado** em celular real.
- **Cópia do relatório: privacidade.** O relatório contém só séries
  públicas do BCB, mas passa a viver numa conta de nuvem do usuário;
  cabe a ele escolher a pasta de destino.

### Fora de escopo (deste script)
- Rotação/limpeza automática de logs antigos — decisão explícita: não
  implementada nesta spec. Cada mês é um arquivo novo (`execucao_
  <AAAA-MM>.log`); arquivos de meses passados se acumulam
  indefinidamente em `logs/`. Se isso um dia virar um problema de
  espaço, é uma spec futura separada.
- Notificação (e-mail etc.) em caso de falha — só o log e o exit code
  registram o problema; ninguém é avisado ativamente.
- Limpeza de temporários órfãos deixados por um encerramento forçado
  via `ExecutionTimeLimit` (ver "Limitações conhecidas").
- Mais de uma nova tentativa, espera crescente (backoff exponencial) ou
  nova tentativa para outros exit codes além do `1`.
- Espera ativa por rede/DNS (ex.: testar conectividade antes de rodar o
  pipeline) — a condição de rede do Agendador e a nova tentativa são as
  únicas medidas desta spec.
- Teto para `INDICADORES_ESPERA_RETRY_SEGUNDOS`.
- Da cópia do relatório: verificar se o OneDrive sincronizou, nova
  tentativa de cópia, guardar histórico de relatórios (arquivos com data
  no nome), copiar outros arquivos (banco, logs), notificar falha de
  cópia, configurar o OneDrive ou o app do celular, usar API/credencial
  de nuvem e tratar caminho de destino relativo.

## `scripts/agendar_tarefa.ps1`

### Responsabilidade
Registrar (ou remover) a tarefa `indicadores-bcb diario` no Agendador
de Tarefas do Windows, usando o módulo `ScheduledTasks` do PowerShell,
de forma idempotente (rodar de novo só atualiza a tarefa existente).

### Parâmetros
```powershell
param(
    [switch]$Remover
)
```
- Sem `-Remover`: registra/atualiza a tarefa.
- Com `-Remover`: remove a tarefa, se existir; se não existir, só
  avisa (não é erro).

### Registro da tarefa (sem `-Remover`)
- **Nome:** `indicadores-bcb diario`.
- **Ação:** executar
  ```
  powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden
      -ExecutionPolicy Bypass -File "<raiz>\scripts\executar_diario.ps1"
  ```
  com `WorkingDirectory` igual à raiz do repositório (`$repoRoot`,
  calculada como `Split-Path -Parent $PSScriptRoot`, igual ao outro
  script). Decisões registradas:
  - `-ExecutionPolicy Bypass` vale **só para este processo específico**
    do `powershell.exe` lançado pela tarefa — não altera a política de
    execução do usuário/máquina de forma persistente (é um parâmetro de
    processo, não uma alteração de registro).
  - Mesmo com `-WindowStyle Hidden`, como a tarefa roda com
    `LogonType Interactive` (na sessão do usuário logado, não em
    background puro), **uma janela de console pode piscar por um
    instante** antes de ser ocultada — comportamento conhecido e aceito
    do Agendador de Tarefas nesse modo, não um bug deste script.
- **Gatilho:** semanal, `DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday`,
  às 16:00, construído como
  `New-ScheduledTaskTrigger -Weekly -DaysOfWeek ... -At (Get-Date -Hour
  16 -Minute 0 -Second 0)`. Decisão registrada: usa um objeto
  `DateTime` montado com `Get-Date -Hour 16 -Minute 0 -Second 0`, **não**
  a string literal `"16:00"`. Motivo: `-At` aceita tanto `DateTime`
  quanto string, mas quando recebe uma string o PowerShell precisa
  interpretá-la como hora usando as configurações de **cultura/locale**
  do Windows configuradas na máquina (formato de hora 12h/24h,
  separador, etc.) — em uma cultura diferente da esperada, `"16:00"`
  poderia ser interpretada de forma diferente da intenção (16h). Um
  `DateTime` construído explicitamente por componentes (`-Hour 16
  -Minute 0 -Second 0`) não depende de nenhuma interpretação de string
  sensível a cultura. (O horário era 19:00 até 08/10/2026; a mudança
  para 16:00 não altera este raciocínio — ver decisão 7.)
- **Configurações** (`New-ScheduledTaskSettingsSet`):
  - `-StartWhenAvailable` (roda assim que possível se o horário foi
    perdido com o PC desligado).
  - `-RunOnlyIfNetworkAvailable`: a tarefa só inicia quando o Windows
    considera que há rede disponível; se não houver no horário, o
    Agendador espera a condição ser satisfeita em vez de iniciar e
    falhar. Motivo: incidente de **02/10/2026** (execução logo após o PC
    acordar da suspensão, com a rede ainda não pronta, falhou nas 3
    séries). Sem `-NetworkName`/`-NetworkId`: qualquer rede serve. Não
    substitui a nova tentativa do wrapper (ver "Limitações conhecidas"
    do wrapper: rede "disponível" não garante DNS/internet prontos, e
    a condição é incerta no Modern Standby).
  - `-WakeToRun` (decisão do usuário): a tarefa pede ao Windows que
    acorde o computador da suspensão para rodar no horário. Contexto
    registrado (informado pelo usuário; os scripts **não** verificam
    nada disso):
    - o notebook usa **Modern Standby**;
    - os **despertadores (wake timers)** estão **ativados na tomada** e
      **desativados na bateria**, no plano de energia **"Equilibrado"**;
    - **por isso o despertar só ocorre com o notebook na tomada**; na
      bateria o `-WakeToRun` não acorda o PC (a tarefa roda quando o
      PC for retomado, via `StartWhenAvailable`);
    - **nada acontece com o PC desligado ou hibernado**;
    - **o plano de energia não é alterado pelo projeto**: nenhum script
      mexe em `powercfg` nem nas configurações de despertadores — o
      projeto só marca a tarefa com `-WakeToRun`.
  - `-MultipleInstances IgnoreNew`: se uma execução anterior ainda
    estiver rodando no horário da próxima, a nova é **ignorada**, não
    enfileirada nem executada em paralelo. Motivo registrado: duas
    execuções simultâneas apontando para o mesmo arquivo DuckDB
    falhariam de qualquer forma (DuckDB é single-writer por arquivo — a
    segunda falharia ao abrir o arquivo bloqueado, exit code `3`, como
    já registrado em `specs/pipeline.md`); `IgnoreNew` evita gastar o
    esforço de tentar e falhar, simplesmente pulando a segunda.
  - `-ExecutionTimeLimit` de **60 minutos** (`(New-TimeSpan -Minutes
    60)`; decisão do usuário) — mata a tarefa se ela travar além desse
    tempo. A conta do pior caso do wrapper (2 execuções do pipeline +
    300 s de espera; modelo conservador com o timeout do httpx por
    operação: ~ 14 min 19 s com 3 séries, ~ 183 s a mais por série
    nova) cabe com ampla folga nesse limite — ver "Nova tentativa
    quando o pipeline termina com exit 1". Ver "Limitações conhecidas"
    quanto ao efeito colateral de um encerramento forçado nos
    temporários do wrapper.
  - Permite rodar na bateria (`-AllowStartIfOnBatteries` e
    `-DontStopIfGoingOnBatteries`), já que é um notebook/desktop com
    uso normal, não um servidor sempre ligado na tomada. (Rodar na
    bateria é diferente de **acordar** na bateria: ver `-WakeToRun`.)
- **Principal** (`New-ScheduledTaskPrincipal`): usuário atual
  (`"$env:USERDOMAIN\$env:USERNAME"`), `-LogonType Interactive`,
  `-RunLevel Limited` (**sem** elevação de administrador — a tarefa não
  precisa de privilégio nenhum além do que o próprio usuário já tem
  para rodar `python -m indicadores`).
- **Registro:** `Register-ScheduledTask -TaskName ... -Action ...
  -Trigger ... -Settings ... -Principal ... -Force`. O `-Force` é o que
  torna o script idempotente: se a tarefa já existir, é **atualizada**
  em vez de o comando falhar com "tarefa já existe".

### Remoção (`-Remover`)
```powershell
if (Get-ScheduledTask -TaskName $nomeTarefa -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $nomeTarefa -Confirm:$false
    # mensagem de sucesso
} else {
    # mensagem avisando que a tarefa não existia; não é erro
}
```
Decisão: `-Confirm:$false` para não travar esperando confirmação
interativa (o script pode ser chamado de forma não interativa também).

### Saída ao terminar (registro no console, não em log de arquivo —
este script é executado manualmente por uma pessoa, não pela tarefa
agendada)
Ao registrar com sucesso, imprime:
- o nome da tarefa;
- o próximo horário de execução (`(Get-ScheduledTaskInfo -TaskName
  $nomeTarefa).NextRunTime`);
- como remover (`... -File agendar_tarefa.ps1 -Remover`).

## Testes (`tests/test_scripts.py`)
Todos marcados com
`@pytest.mark.skipif(sys.platform != "win32", reason="Agendador de
Tarefas é específico do Windows")` — no CI Linux (ou qualquer ambiente
não Windows), a suíte inteira deste arquivo é pulada, não falha.
Nenhum teste registra uma tarefa de verdade no Agendador nem acessa a
API do BCB.

### `executar_diario.ps1`, via `subprocess`
Invocado como:
```
powershell.exe -NoProfile -ExecutionPolicy Bypass -File
    scripts\executar_diario.ps1 --help
```
com a variável de ambiente `INDICADORES_LOG_DIR` apontando para
`tmp_path` (repassada via `env=` do `subprocess.run`, herdando o resto
do ambiente do processo de teste).

**Salvaguarda contra a cópia do relatório (decisão 8):** o `env=` de
**todos** os testes do wrapper (os existentes e os novos) remove
`OneDrive`, `OneDriveConsumer`, `OneDriveCommercial` e
`INDICADORES_DESTINO_RELATORIO` do ambiente herdado; os testes que
precisam de destino definem `INDICADORES_DESTINO_RELATORIO` (ou
`OneDrive`) apontando para uma pasta dentro de `tmp_path`. Assim,
nenhum teste escreve no OneDrive real do desenvolvedor, nem os que não
tratam da cópia. Os testes existentes não regeneram o `relatorio.html` da
raiz (`--help`, `--sem-relatorio` ou `--relatorio <tmp_path>\r.html`, ver
`specs/relatorio.md`), então não chegam a copiar nada.

### Provocar exit 1 offline, com um proxy local próprio (queda de rede simulada)
Os testes da nova tentativa precisam de um exit `1` real do pipeline
sem rede. Técnica: rodar o wrapper com `HTTPS_PROXY` e `HTTP_PROXY`
apontando para um **socket local aberto pelo próprio teste**, que faz
o papel de proxy. O httpx respeita essas variáveis por padrão
(`trust_env=True`, que é o default e **não** é desligado por
`extracao.criar_client()` — confirmado lendo o código), então toda
requisição tenta o proxy; a conexão com ele é encerrada na hora, a
extração trata a falha como erro de transporte e levanta `ErroConexao`
para cada série, todas ficam ignoradas e o pipeline termina com exit `1`.
Nenhum tráfego sai da máquina.

**Fixture `proxy_local`** (escopo de função):
1. cria um socket TCP, faz `bind` em `("127.0.0.1", 0)` (porta efêmera
   escolhida pelo sistema — sem risco de colisão com outro serviço) e
   `listen`;
2. sobe uma thread *daemon* que faz `accept()` em laço, **incrementa um
   contador** a cada conexão recebida e a fecha imediatamente, sem ler
   nem responder;
3. expõe `url` (`http://127.0.0.1:<porta>`) e `conexoes` (o contador);
4. no *teardown*, fecha o socket e encerra a thread.

**Prova de que a falha veio do proxy:** o teste que simula a queda de
rede **exige `conexoes >= 1`** depois da execução do wrapper. Isso
garante que o exit `1` veio de a requisição ter sido de fato desviada
para o proxy do teste, e não de qualquer outra falha por acaso (ex.:
a máquina de teste estar sem rede de verdade) — um teste que passa pelo
motivo errado não protege nada. O número exato de conexões **não** é
verificado (dependeria de detalhes de reuso de conexão do httpx; o
esperado seria da ordem de 6 com `--series 432`: 3 tentativas internas
x 2 execuções). **A confirmar pelo implementer ao rodar o teste:** que o
fechamento imediato produz uma exceção que é subclasse de
`httpx.TransportError` (esperado: `RemoteProtocolError` ou `ProxyError`)
e portanto vira `ErroConexao` / exit `1`; se resultasse em exit `3`,
trocar o comportamento do proxy falso para responder
`HTTP/1.1 502 Bad Gateway` depois de ler o `CONNECT`.

Detalhes registrados:
- O `env=` do teste define `HTTPS_PROXY` e `HTTP_PROXY` (=
  `proxy_local.url`), `INDICADORES_ESPERA_RETRY_SEGUNDOS=0` e
  `INDICADORES_LOG_DIR=tmp_path`, e **remove** `NO_PROXY`, `ALL_PROXY`
  (e variantes em minúsculas) do ambiente herdado, para um `NO_PROXY`
  do desenvolvedor não desviar a conexão do proxy.
- O wrapper recebe `--banco <tmp_path>\dados\x.duckdb`: com a falha de
  rede o pipeline **abre o banco e cria a tabela** (`executar` chama
  `criar_tabela` antes de buscar), mas não grava nenhuma linha — e
  nenhum arquivo `.duckdb` é criado fora de `tmp_path`.
- Os testes usam `--series 432` (uma série) para limitar o tempo: a
  espera real do retry interno da extração (0,5 s + 1,0 s = 1,5 s **por
  série**, não injetável pela CLI) ainda ocorre. Com uma série, cada
  tentativa leva ~1,5 s + a inicialização do Python; um teste com nova
  tentativa leva por volta de 8-10 s no total (estimativa); com as 3
  séries seriam +3 s por tentativa.

### Árvore falsa do repositório, para a cópia do relatório (decisão 8)
Os testes da cópia precisam controlar **qual exit code** o "pipeline"
devolve, **se** ele regenera `relatorio.html` e **quantas vezes** roda,
sem rede, sem DuckDB e sem tocar no `relatorio.html` real da raiz. O
wrapper resolve tudo a partir de `Split-Path -Parent $PSScriptRoot`,
então os testes montam, em `tmp_path\repo` (fixture `arvore_falsa`, escopo
de função), uma raiz de repositório própria:
- `scripts\executar_diario.ps1`: **cópia** do script real (o que está sob
  teste);
- `.venv\Scripts\python.exe`: um interpretador Python executável de
  verdade. **A confirmar pelo implementer:** a forma de obtê-lo; a opção
  esperada é copiar para essa posição o `python.exe` do venv do projeto
  junto com o `pyvenv.cfg` (que fica um nível acima de `Scripts`), o que
  em geral basta para o *launcher* do venv achar a instalação base. Se
  não funcionar, qualquer outra forma de ter `.venv\Scripts\python.exe`
  que execute `python -m indicadores` com o `PYTHONPATH` do wrapper serve;
- `src\indicadores\__init__.py` e `src\indicadores\__main__.py`
  **falsos**, só da árvore de teste (o wrapper define
  `PYTHONPATH=<raiz>\src`, então o Python da árvore carrega o pacote
  falso, não o real). O falso lê duas variáveis de ambiente do teste:
  `FALSO_EXITS` (lista separada por vírgulas, um exit code por tentativa;
  o último valor vale para tentativas além do tamanho da lista, ex.
  `"1,0"`) e `FALSO_GERA_RELATORIO` (`"1"`: grava `relatorio.html` no
  diretório de trabalho, que o wrapper define como a raiz da árvore, com
  um marcador de conteúdo `tentativa=<n>`; `"0"`: não grava nada). O
  número da tentativa vem de um arquivo contador dentro de `tmp_path`.
  Imprime algo curto em stdout e termina com o exit code escolhido.
  Não importa `httpx`, `duckdb` nem `pandas`.

O `env=` desses testes define `INDICADORES_LOG_DIR=<tmp_path>\logs`,
`INDICADORES_ESPERA_RETRY_SEGUNDOS=0` e as variáveis do falso, e aplica a
salvaguarda de ambiente descrita acima (nenhuma variável do OneDrive real
herdada). Cada execução leva cerca de 1–2 s (inicialização do PowerShell e
do Python, sem espera). O wrapper roda **sem argumentos extras** (o falso
ignora `argv`).

### `agendar_tarefa.ps1`, só validação de sintaxe e de texto
Sem registrar tarefa nenhuma: valida que o arquivo é PowerShell
sintaticamente válido, via
`[System.Management.Automation.Language.Parser]::ParseFile(...)`
chamado dentro de um processo `powershell.exe` descartável (não requer
o módulo `ScheduledTasks` nem privilégio nenhum, e não executa o
conteúdo do script). Verificações adicionais por leitura de texto do
arquivo confirmam a presença de `-RunOnlyIfNetworkAvailable`,
`-WakeToRun` e `-Minutes 60`.

## Regras de negócio
1. O wrapper nunca acessa a rede nem o DuckDB diretamente — só invoca
   `python -m indicadores` como subprocesso e captura sua saída. (A
   cópia do relatório, decisão 8, só mexe em arquivos locais; nenhuma
   rede, API ou credencial.)
2. O exit code do wrapper é sempre o exit code do Python (`0`–`4`) da
   última tentativa executada, exceto quando o próprio wrapper falha
   antes de conseguir rodar o Python, não consegue determinar o exit
   code real, não consegue preparar o log ou sofre um erro inesperado
   depois disso (`≥ 10`).
3. O log é sempre append, UTF-8 sem BOM, um arquivo por mês; nunca
   sobrescreve execuções anteriores do mesmo mês.
4. `INDICADORES_LOG_DIR` e `INDICADORES_ESPERA_RETRY_SEGUNDOS` só
   existem para permitir testes sem escrever em `logs/` do repositório
   real e sem esperar 300 s. `INDICADORES_DESTINO_RELATORIO` é a exceção
   deliberada: configuração de uso real, que os testes também usam para
   apontar para `tmp_path`.
5. `agendar_tarefa.ps1 -Force` é idempotente: rodar de novo só
   atualiza a tarefa, nunca duplica nem falha por já existir.
6. A tarefa nunca roda com privilégio de administrador
   (`RunLevel Limited`).
7. Argumentos com espaço ou aspas são citados corretamente ao montar a
   linha de comando do processo Python — nunca quebrados em múltiplos
   argumentos posicionais por causa de um espaço interno.
8. `$processo.ExitCode` nulo nunca é interpretado como sucesso.
9. Falha ao **ler** os arquivos temporários de stdout/stderr nunca muda
   o exit code do wrapper — vira só uma linha `AVISO` no log.
10. Falha ao preparar o log (diretório ou cabeçalho) usa exit code
    `12` e reporta em stderr do próprio wrapper — nunca deixa o
    PowerShell terminar com o código genérico `1`.
11. O horário do gatilho da tarefa é construído com componentes
    explícitos (`Get-Date -Hour/-Minute/-Second`), não com uma string
    de hora dependente de cultura/locale.
12. Exit `1` do Python provoca **uma única** nova tentativa, com os
    mesmos argumentos, no mesmo bloco do log; nunca há terceira
    tentativa.
13. Nunca há nova tentativa para exit `0`, `2`, `3`, `4`, `10`, `11`,
    `12` ou `13`. O exit `3` não repete por decisão do usuário (banco
    bloqueado raramente se resolve em 5 minutos; repetir esconderia
    bugs); o `4` não repete porque a falha é só do relatório e os dados
    já foram gravados.
14. Os blocos da tentativa 1 e a linha `AVISO` da nova tentativa são
    gravados no log **antes** da espera.
15. A espera `N` é 300 s por padrão; `INDICADORES_ESPERA_RETRY_SEGUNDOS`
    só com dígitos (`NumberStyles.None`, `InvariantCulture`) a
    substitui; definida e inválida (`"+5"`, `" 5 "`, `"-1"`, `"abc"`)
    cai em 300 com uma linha `AVISO` no log; vazia ou ausente usa 300
    sem aviso; a variável é validada logo depois do cabeçalho do log.
16. A tarefa registrada usa `-RunOnlyIfNetworkAvailable`, `-WakeToRun`
    e `ExecutionTimeLimit` de 60 minutos.
17. Erro inesperado depois de o log ter sido preparado usa exit code
    `13` — nunca deixa o PowerShell terminar com o código genérico `1`.
18. Os blocos de stdout/stderr no log são sempre rotulados com o número
    da tentativa (`(tentativa 1)`, `(tentativa 2)`).
19. O projeto não altera o plano de energia do Windows (nem
    despertadores, nem `powercfg`).
20. O wrapper copia `relatorio.html` da raiz para a pasta de destino
    **somente** se o exit final é `0` ou `1` e o arquivo foi regenerado
    nesta execução (`LastWriteTimeUtc >= $inicioExecucaoUtc`); nunca para
    `2`, `3`, `4`, `10`–`13`; nunca um arquivo de execução anterior.
21. A cópia acontece **uma única vez**, depois da última tentativa e
    antes do rodapé do log; nunca entre a tentativa 1 e a 2.
22. A pasta de destino é `INDICADORES_DESTINO_RELATORIO` (definida e não
    vazia, sem fallback se falhar; precisa ser caminho absoluto — se for
    relativo, não copia e registra `AVISO`); senão
    `%OneDrive%\indicadores-bcb`; senão não copia e registra `AVISO`.
    Nada no código é específico de OneDrive além desse padrão; nenhuma
    credencial.
23. A cópia é atômica: copia para `relatorio.html.tmp` na pasta de
    destino e renomeia por cima de `relatorio.html`; cria a pasta se não
    existir; em falha remove o `.tmp` e deixa o `relatorio.html`
    anterior do destino intacto.
24. Falha na cópia (ou destino ausente) **nunca altera o exit code**,
    nunca dispara nova tentativa e nunca produz o exit `13`: vira só
    `AVISO: falha ao copiar relatório para '<destino>': <mensagem>` (ou
    `AVISO: relatório não copiado: ...`) no log. Sucesso registra
    `Relatório copiado para: <caminho>`.

## Fora de escopo
- Rotação/limpeza de logs (ver "Fora de escopo (deste script)" acima).
- Notificação por e-mail ou qualquer outro canal em caso de falha.
- Suporte a Linux/`cron` — é uma spec específica de Windows.
- Rodar sem o usuário logado (`LogonType` diferente de `Interactive`,
  ex. `S4U`/`ServiceAccount`) — decisão já tomada pelo usuário, fora de
  escopo mudar aqui.
- Conteúdo da seção de agendamento em `README.md`/`HANDOFF.md` (fica
  para o `doc-writer`; inclui documentar `INDICADORES_DESTINO_RELATORIO`
  e como abrir o HTML no celular).
- Limpeza de temporários órfãos deixados por um encerramento forçado do
  Agendador via `ExecutionTimeLimit` (ver "Limitações conhecidas").
- Alterar o plano de energia (despertadores na bateria, `powercfg`),
  acordar o PC desligado ou hibernado, manter a tarefa ativa após
  logoff/desligamento e ativar o histórico do Agendador (ver
  "Limitações conhecidas").
- Mais de uma nova tentativa, backoff crescente, nova tentativa para
  outros exit codes e teto para a espera (ver "Fora de escopo (deste
  script)").
- Verificar a sincronização com o OneDrive, nova tentativa de cópia,
  histórico de relatórios e demais itens listados em "Fora de escopo
  (deste script)".

## Critérios de aceite
1. `executar_diario.ps1 --help` roda até o fim, sem tocar a rede, com
   exit code `0`, sem nova tentativa; os blocos do log são rotulados
   `(tentativa 1)`.
2. `executar_diario.ps1` grava um log em
   `<INDICADORES_LOG_DIR ou logs/>/execucao_<AAAA-MM>.log`, em append,
   contendo cabeçalho com timestamp de início, stdout, stderr (rotulados
   com o número da tentativa), exit code e timestamp de fim.
3. O log está em UTF-8 sem BOM e sem corrupção de acentos (ex. a
   palavra "séries" do texto de ajuda do `argparse` aparece intacta).
4. `--series abc` (entrada inválida da CLI) resulta em exit code `2`
   no wrapper, refletindo o exit code do Python, registrado no log,
   sem nova tentativa.
5. `.venv\Scripts\python.exe` ausente resulta em exit code `10`, com o
   motivo registrado no log, sem tentar rodar nada e sem nova
   tentativa.
6. Um argumento com espaço embutido (ex. `--banco` apontando para um
   caminho com espaço) chega ao Python como um único valor, não como
   múltiplos argumentos posicionais quebrados.
7. `$processo.ExitCode` nulo resulta em exit code `11`, nunca em `0`.
8. Falha ao preparar o log (diretório inacessível ou impossível de
   criar) resulta em exit code `12`, com a mensagem de erro em stderr
   do processo do wrapper (não em arquivo).
9. Erro inesperado **depois** de o log ter sido preparado (ex.: falha ao
   criar o arquivo temporário) resulta em exit code `13`, com `ERRO:
   <mensagem>` e rodapé `exit code: 13` no log, e nunca no `1` genérico
   do PowerShell.
10. O wrapper não deixa arquivos temporários (`GetTempFileName`) para
    trás após terminar, em nenhum dos cenários normais (exit `0`–`4`,
    `10`, `11`, `12`, `13`, inclusive quando há nova tentativa e no
    cenário de queda de rede) — a única exceção aceita e registrada é o
    encerramento forçado (Agendador por `ExecutionTimeLimit`, ou
    logoff/desligamento).
11. Quando o pipeline termina com exit `1`, o wrapper faz exatamente uma
    nova tentativa: o log contém a linha
    `AVISO: exit 1; nova tentativa em <N> s`, os blocos
    `--- stdout (tentativa 2) ---` e `--- stderr (tentativa 2) ---`
    (com "erro de conexão" no trecho da tentativa 2 no cenário de queda
    de rede), o rodapé registra o exit code da segunda tentativa, e
    "tentativa 3" nunca aparece.
12. Nenhuma nova tentativa ocorre para exit `0`, `2`, `3`, `4`, `10`,
    `11`, `12` ou `13` (o log não contém "tentativa 2"). O exit `4`
    é repassado como veio, sem nenhuma mudança no script (teste 57 de
    `specs/relatorio.md`).
13. `INDICADORES_ESPERA_RETRY_SEGUNDOS` com valor válido (só dígitos,
    inclusive `0`) define `N`; definida e inválida (`"+5"`, `" 5 "`,
    `"-1"`, `"abc"`, `"1.5"`) o wrapper usa 300 e registra, logo depois
    do cabeçalho, a linha `AVISO: valor inválido em ...` com o valor
    recebido; vazia ou ausente usa 300 sem aviso.
14. A conta do pior caso com o padrão (modelo conservador: ~ 14 min 19 s
    com 3 séries; ~ 183 s a mais por série nova) cabe no
    `ExecutionTimeLimit` de 60 minutos (verificável por leitura desta
    spec, não por teste).
15. `agendar_tarefa.ps1` é sintaticamente válido
    (`Parser::ParseFile` sem erros), sem precisar registrar nenhuma
    tarefa de verdade para o teste passar.
16. `agendar_tarefa.ps1` constrói o horário do gatilho via `Get-Date`
    com componentes explícitos, não uma string literal de hora, e usa
    `-RunOnlyIfNetworkAvailable`, `-WakeToRun` e
    `-ExecutionTimeLimit (New-TimeSpan -Minutes 60)` em
    `New-ScheduledTaskSettingsSet`.
17. Nenhum teste deste arquivo roda fora do Windows (todos pulados via
    `skipif` em outras plataformas) nem acessa a API do BCB ou o
    Agendador de Tarefas real; os testes de exit `1` provocam a falha
    offline via um proxy local aberto pelo próprio teste, que exige
    pelo menos uma conexão recebida (prova de que a falha veio do
    proxy).
18. `.gitignore` ignora `logs/`.
19. Com exit final `0` ou `1` e `relatorio.html` da raiz regenerado nesta
    execução, o wrapper copia o arquivo para a pasta de destino
    **exatamente uma vez**, depois da última tentativa e antes do
    rodapé; o log contém `Relatório copiado para: <caminho>`; o arquivo
    no destino tem o mesmo conteúdo do da raiz (o da última tentativa);
    não sobra `relatorio.html.tmp` no destino; o exit code não muda.
20. Com exit final `2`, `3` ou `4` (e `10`–`13`), ou com `relatorio.html`
    ausente ou **não** regenerado nesta execução (data de modificação
    anterior ao início), nada é copiado; no segundo caso o log contém
    `Relatório não copiado: relatorio.html não foi regenerado nesta
    execução` (sem `AVISO`).
21. O destino é `INDICADORES_DESTINO_RELATORIO` quando definida e não
    vazia (prevalece sobre `OneDrive`); senão `%OneDrive%\indicadores-bcb`;
    a pasta de destino é criada se não existir.
22. Sem `INDICADORES_DESTINO_RELATORIO` e sem `OneDrive`: nada é copiado,
    o log contém `AVISO: relatório não copiado: ...` e o exit code do
    wrapper não muda.
23. Falha ao copiar (destino inválido, arquivo de destino bloqueado)
    registra `AVISO: falha ao copiar relatório para '<destino>': ...`,
    não altera o exit code, não produz `13`, não dispara nova tentativa e
    não deixa `relatorio.html.tmp` no destino; o `relatorio.html`
    anterior do destino permanece intacto.
24. Nenhum teste escreve no OneDrive real nem depende de variável
    `OneDrive*` herdada do ambiente; nenhuma credencial aparece no
    script.

## Casos de teste (pytest)
1. `test_executar_diario_help_retorna_zero_e_grava_log` — roda o
   wrapper com `--help` e `INDICADORES_LOG_DIR=tmp_path`; assert
   `returncode == 0`, existe exatamente um arquivo
   `execucao_<AAAA-MM>.log` em `tmp_path`, e ele contém as strings de
   cabeçalho (`"início"`), os rótulos
   `--- stdout (tentativa 1) ---` e `--- stderr (tentativa 1) ---`, o
   texto de uso do `argparse` (ex. `"usage: python -m indicadores"`), e
   o rodapé com `"exit code: 0"`; e **não** contém `"tentativa 2"`.
2. `test_executar_diario_log_preserva_acentos_utf8` — mesmo cenário do
   teste 1; abre o log com `encoding="utf-8"` e assert que a substring
   `"séries"` (presente na descrição do `argparse`, repassada via
   `--help`) aparece intacta, sem caracteres de substituição/mojibake.
3. `test_executar_diario_argumento_invalido_retorna_dois` — roda o
   wrapper com `--series abc`; assert `returncode == 2` e que o log
   contém `"exit code: 2"`.
4. `test_executar_diario_sem_venv_retorna_dez` — roda o wrapper
   a partir de uma cópia/estrutura de teste onde `.venv\Scripts\python.exe`
   não existe (ex.: apontando `$PSScriptRoot` para uma árvore de
   diretórios isolada em `tmp_path`, sem `.venv`); assert
   `returncode == 10` e que o log registra o motivo, sem indicar
   tentativa de rodar o Python e sem `"tentativa 2"`.
5. `test_executar_diario_repassa_multiplos_argumentos` — roda o
   wrapper com `--series 432 433 --help` (ou argumentos equivalentes
   que não acessam rede, ex. só `--help` combinado com outra flag
   qualquer que não cause erro de parse); assert que os argumentos
   chegaram ao Python (confirmável, por exemplo, pelo texto de uso
   do `argparse` no log, que é o mesmo independentemente de outras
   flags presentes quando `--help` está na lista) — este é o teste que
   confirma na prática o ponto em aberto sobre `$args` no PowerShell
   5.1.
6. `test_wrapper_repassa_argumento_com_espaco` — roda o wrapper com
   `--banco "<tmp_path>\pasta com espaço\x.duckdb" --help`; assert
   `returncode == 0`. Como `--help` faz o `argparse` sair antes de
   `abrir_conexao` ser chamada, o teste **não** cria nenhum arquivo
   `.duckdb` — só confirma que o wrapper não quebra/trava ao montar a
   linha de comando com um caminho contendo espaço (o que quebraria
   antes mesmo de chegar no `argparse`, ex. com um erro de argumento
   posicional inesperado, se o quoting estivesse ausente).
7. `test_wrapper_falha_ao_preparar_log_retorna_doze` — roda o wrapper
   com `INDICADORES_LOG_DIR` apontando para um caminho que não pode
   virar diretório de log (ex.: um caminho que colide com um **arquivo
   comum** já existente no lugar onde o diretório precisaria ser
   criado, criado antes pelo próprio teste em `tmp_path`); assert
   `returncode == 12` e que a mensagem de erro aparece no **stderr**
   capturado pelo `subprocess` (não em um arquivo de log, que não
   chegou a ser criado).
8. `test_wrapper_falha_inesperada_apos_log_retorna_treze` — roda o
   wrapper com `INDICADORES_LOG_DIR=tmp_path` (log preparado com
   sucesso) e com `TMP`/`TEMP` apontando para um diretório **inexistente**
   (ex.: `<tmp_path>\nao_existe`), o que faz `GetTempFileName()` falhar
   **depois** do cabeçalho; assert `returncode == 13`, que o log contém
   `"ERRO:"` e `"exit code: 13"`, e que **não** contém `"tentativa 2"`.
   **A confirmar pelo implementer:** que o `powershell.exe` inicia
   normalmente com `TMP`/`TEMP` inválidos; se não iniciar, usar outra
   forma de provocar uma falha depois do cabeçalho (o objetivo do teste
   é só exercitar o `catch` de nível mais alto).
9. `test_wrapper_nao_deixa_temporarios` — lista os arquivos
   `tmp*.tmp` (ou padrão equivalente de `GetTempFileName`) em
   `[System.IO.Path]::GetTempPath()` **antes** de rodar o wrapper;
   roda o wrapper três vezes (uma com `--help`, exit `0`; outra com
   `--series abc`, exit `2`; uma terceira no **cenário de queda de
   rede** — fixture `proxy_local`, exit `1` com nova tentativa, com
   `--series 432` — para cobrir o par de temporários de cada
   tentativa); lista os mesmos arquivos **depois**; assert que o
   conjunto de arquivos temporários não cresceu (mesmos arquivos antes
   e depois, dentro de uma margem razoável para temporários de outros
   processos do sistema alheios ao teste — ex. comparando só os
   arquivos cujo nome aparece associado ao processo do wrapper, se
   identificável, ou aceitando pequena tolerância e registrando isso
   como parte do próprio teste).
10. `test_wrapper_exit_1_faz_uma_nova_tentativa` — cenário de queda de
    rede (ver "Provocar exit 1 offline"): fixture `proxy_local` como
    `HTTPS_PROXY`/`HTTP_PROXY`, `INDICADORES_ESPERA_RETRY_SEGUNDOS=0`,
    `INDICADORES_LOG_DIR=tmp_path`, argumentos
    `--banco <tmp_path>\dados\x.duckdb --series 432`; assert:
    - `returncode == 1`;
    - **`proxy_local.conexoes >= 1`** (prova de que a falha veio do
      proxy do teste e não de outra causa);
    - o log contém a linha `AVISO: exit 1; nova tentativa em 0 s` e
      **não** contém `AVISO: valor inválido`;
    - os marcadores `--- stdout (tentativa 1) ---`,
      `--- stderr (tentativa 1) ---`, `--- stdout (tentativa 2) ---` e
      `--- stderr (tentativa 2) ---` aparecem **uma única vez** cada;
    - o log **não** contém `"tentativa 3"` e tem um único rodapé com
      `"exit code: 1"`;
    - o trecho do log **da tentativa 2** (do marcador
      `--- stdout (tentativa 2) ---` até o rodapé) contém
      `"erro de conexão"` — prova de que a segunda execução de fato
      rodou e falhou de novo, não só que o marcador foi escrito (o
      mesmo texto também aparece no trecho da tentativa 1);
    - o arquivo do banco existe dentro de `tmp_path` (o pipeline abriu o
      banco e criou a tabela).
11. `test_wrapper_exit_2_nao_tenta_de_novo` — wrapper com `--series abc`
    (mesma invocação do teste 3, mantida como teste separado pelo nome
    explícito da regra); assert `returncode == 2` e que o log **não**
    contém `"tentativa 2"` nem `"nova tentativa"`.
12. `test_wrapper_exit_3_nao_tenta_de_novo` — `--banco` apontando para
    um caminho cujo diretório pai colide com um **arquivo comum** criado
    antes pelo teste em `tmp_path` (faz `abrir_conexao` falhar sem
    nenhuma rede envolvida); assert `returncode == 3` e que o log não
    contém `"tentativa 2"` nem `"nova tentativa"` (decisão do usuário:
    o exit `3` não repete).
13. `test_wrapper_espera_retry_invalida_usa_padrao` — parametrizado com
    `INDICADORES_ESPERA_RETRY_SEGUNDOS` em `"abc"`, `"-1"`, `"+5"`,
    `" 5 "` e `"1.5"`; roda o wrapper com `--help` (exit `0`, nunca
    precisa da espera, então o teste não espera 300 s); assert
    `returncode == 0` e que o log contém
    `AVISO: valor inválido em INDICADORES_ESPERA_RETRY_SEGUNDOS ('<valor>'); usando 300`
    com o valor recebido **exatamente** entre aspas simples (ex.
    `(' 5 ')`, `('+5')`), e que essa linha vem **logo depois do
    cabeçalho** (antes do primeiro bloco `--- stdout (tentativa 1) ---`).
    Limitação registrada: o **uso efetivo** de 300 s durante uma nova
    tentativa real não é testado (levaria 5 min); fica validado por
    leitura do código e pela mensagem do aviso, que informa o valor
    adotado.
14. `test_wrapper_espera_retry_vazia_ou_ausente_nao_avisa` —
    parametrizado com a variável **ausente** e **vazia** (`""`); roda o
    wrapper com `--help`; assert `returncode == 0` e que o log **não**
    contém `AVISO: valor inválido`. (Se o ambiente do Windows não
    preservar uma variável vazia, ela equivale a ausente, e o resultado
    esperado é o mesmo.)
15. `test_agendar_tarefa_sintaxe_valida` — invoca `powershell.exe` para
    rodar `Parser::ParseFile` sobre `scripts/agendar_tarefa.ps1`; assert
    que a lista de erros de parse está vazia (exit `0`), sem registrar
    nenhuma tarefa real.
16. `test_agendar_tarefa_usa_run_only_if_network_available` — leitura de
    texto de `scripts/agendar_tarefa.ps1`; assert que contém
    `-RunOnlyIfNetworkAvailable`.
17. `test_agendar_tarefa_usa_wake_to_run` — leitura de texto de
    `scripts/agendar_tarefa.ps1`; assert que contém `-WakeToRun`.
18. `test_agendar_tarefa_limite_de_execucao_60_minutos` — leitura de
    texto de `scripts/agendar_tarefa.ps1`; assert que contém
    `-ExecutionTimeLimit` e `-Minutes 60` (e não `-Minutes 30`).
19. `test_gitignore_ignora_logs` — leitura simples do `.gitignore` do
    repositório; assert que a linha `logs/` está presente.

O teste do wrapper para o exit `4` (`test_wrapper_repassa_exit_4_sem_nova_tentativa`,
caso 57) está definido em `specs/relatorio.md` e não é duplicado aqui.

**Cópia do relatório (decisão 8, 09/10/2026)** — todos Windows-only
(`skipif`), usando a fixture `arvore_falsa` (ver "Árvore falsa do
repositório"), sem rede, sem proxy, sem DuckDB e sem tocar o OneDrive
real: o destino é sempre uma pasta dentro de `tmp_path`, definida por
`INDICADORES_DESTINO_RELATORIO` (ou, nos casos 25 e 26, por `OneDrive`
apontando para `tmp_path`). "Destino" abaixo = `<tmp_path>\destino`
quando não houver outra indicação. Em todos, o `relatorio.html` real da
raiz do projeto nunca é lido nem escrito.

20. `test_wrapper_copia_relatorio_quando_exit_0` — `FALSO_EXITS="0"`,
    `FALSO_GERA_RELATORIO="1"`, destino já existente; assert
    `returncode == 0`; `<destino>\relatorio.html` existe e tem o mesmo
    conteúdo (bytes) do `relatorio.html` da árvore falsa; não existe
    `relatorio.html.tmp` no destino; o log contém
    `Relatório copiado para: <destino>\relatorio.html` **uma única vez**
    e o rodapé `exit code: 0`; a linha de cópia vem depois de
    `--- stderr (tentativa 1) ---` e antes do rodapé; não contém
    `AVISO` nem `"tentativa 2"`.
21. `test_wrapper_copia_relatorio_uma_unica_vez_apos_nova_tentativa` —
    parametrizado por `FALSO_EXITS` em `"1,1"` (exit final `1`) e `"1,0"`
    (exit final `0`), `FALSO_GERA_RELATORIO="1"` (o falso grava o
    relatório a cada tentativa, com marcador `tentativa=<n>`);
    `INDICADORES_ESPERA_RETRY_SEGUNDOS=0`; assert `returncode` igual ao da
    última tentativa; o log contém `AVISO: exit 1; nova tentativa em 0 s`
    e `Relatório copiado para:` **exatamente uma vez**, **depois** de
    `--- stderr (tentativa 2) ---` e antes do rodapé (prova de que não
    copiou entre as tentativas); o arquivo copiado contém o marcador
    `tentativa=2`; não contém `"tentativa 3"` nem `.tmp` no destino.
22. `test_wrapper_nao_copia_relatorio_quando_exit_nao_e_0_nem_1` —
    parametrizado por `FALSO_EXITS` em `"2"`, `"3"` e `"4"`, com
    `FALSO_GERA_RELATORIO="1"` (o falso **grava** o relatório mesmo assim,
    para provar que é o exit code que impede a cópia, não a ausência do
    arquivo); assert `returncode` igual ao exit do falso; o destino não
    contém `relatorio.html` nem `.tmp` (o destino pode nem existir); o
    log não contém `Relatório copiado para:` nem `AVISO: falha ao copiar`
    nem `AVISO: relatório não copiado`. Os códigos `10`–`13` não são
    parametrizados aqui (o `10` e o `13` já são exercitados pelos casos 4
    e 8, e a etapa não roda neles).
23. `test_wrapper_nao_copia_relatorio_antigo` — `FALSO_EXITS="0"`,
    `FALSO_GERA_RELATORIO="0"` (o falso não regenera nada); o teste cria
    antes um `relatorio.html` na raiz da árvore falsa com conteúdo
    reconhecível e data de modificação **anterior** ao início da execução
    (`os.utime` para 1 dia atrás); parametrizável também com o arquivo
    **ausente**; assert `returncode == 0`; o destino não recebe
    `relatorio.html` (nem fica um já existente alterado, quando o teste
    cria um com conteúdo diferente antes); o log contém `Relatório não
    copiado: relatorio.html não foi regenerado nesta execução` e **não**
    contém `AVISO`.
24. `test_wrapper_destino_pela_variavel_prevalece_sobre_onedrive` —
    `INDICADORES_DESTINO_RELATORIO=<tmp_path>\via_variavel` **e**
    `OneDrive=<tmp_path>\onedrive_falso`; `FALSO_EXITS="0"`,
    `FALSO_GERA_RELATORIO="1"`; assert `returncode == 0`;
    `<tmp_path>\via_variavel\relatorio.html` existe (a variável é usada
    como está, sem subpasta `indicadores-bcb`) e `<tmp_path>\onedrive_falso`
    **não** foi criado; o log menciona o caminho de `via_variavel`.
25. `test_wrapper_usa_onedrive_como_destino_padrao` — sem
    `INDICADORES_DESTINO_RELATORIO` e com `OneDrive=<tmp_path>\onedrive_falso`
    (pasta existente); `FALSO_EXITS="0"`, `FALSO_GERA_RELATORIO="1"`;
    assert `returncode == 0` e que
    `<tmp_path>\onedrive_falso\indicadores-bcb\relatorio.html` existe
    (subpasta criada) com o conteúdo do relatório; a variável vazia
    (`INDICADORES_DESTINO_RELATORIO=""`) tem o mesmo efeito (parametrizado:
    ausente e vazia).
26. `test_wrapper_sem_destino_registra_aviso_e_mantem_exit` —
    parametrizado por `FALSO_EXITS` em `"0"` e `"1"`
    (`INDICADORES_ESPERA_RETRY_SEGUNDOS=0`); `FALSO_GERA_RELATORIO="1"`;
    **sem** `INDICADORES_DESTINO_RELATORIO` e **sem** `OneDrive` no
    `env=`; assert `returncode` igual ao exit do falso (inalterado, nunca
    `13`); o log contém `AVISO: relatório não copiado:` e o rodapé com o
    exit esperado; nenhum arquivo `relatorio.html` é criado fora da árvore
    falsa dentro de `tmp_path`.
27. `test_wrapper_falha_na_copia_registra_aviso_mantem_exit_e_nao_deixa_tmp`
    — `FALSO_EXITS` em `"0"` e `"1"` (parametrizado, junto com as duas
    formas de falha abaixo), `FALSO_GERA_RELATORIO="1"`; assert
    `returncode` igual ao exit do falso (nunca `13`, e para `"1"` sem
    terceira tentativa: o log não contém `"tentativa 3"`); o log contém
    `AVISO: falha ao copiar relatório para '<destino>':` seguido de uma
    mensagem não vazia, e não contém `Relatório copiado para:`; não existe
    `relatorio.html.tmp` em nenhum lugar de `tmp_path`. Formas de falha:
    (a) `INDICADORES_DESTINO_RELATORIO` aponta para um **arquivo comum**
    existente (a pasta não pode ser criada); (b) a pasta de destino
    existe e contém um `relatorio.html` com conteúdo antigo **aberto pelo
    próprio teste sem compartilhamento** (ex. `CreateFileW` com
    `dwShareMode=0` via `ctypes`, ou equivalente) durante a execução do
    wrapper, de modo que a substituição falha **depois** de o `.tmp` ter
    sido criado; assert adicional em (b): depois de liberar o arquivo, o
    `relatorio.html` do destino ainda tem o conteúdo antigo (intacto) e o
    `.tmp` foi removido. **A confirmar pelo implementer:** que o bloqueio
    de (b) de fato faz o `Move-Item`/`Replace` falhar neste Windows; se
    não falhar, escolher outro modo de provocar uma falha após a criação
    do `.tmp` (o objetivo é exercitar a limpeza do temporário).
28. `test_wrapper_cria_pasta_de_destino_inexistente` —
    `INDICADORES_DESTINO_RELATORIO=<tmp_path>\a\b\c` (nenhum nível
    existe); `FALSO_EXITS="0"`, `FALSO_GERA_RELATORIO="1"`; assert
    `returncode == 0` e que `<tmp_path>\a\b\c\relatorio.html` existe com o
    conteúdo do relatório, sem `.tmp`.

## Convenções seguidas
- Comentários e mensagens de log em português, consistente com o
  restante do projeto.
- Nenhum teste escreve fora de `tmp_path` (via `INDICADORES_LOG_DIR`,
  `--banco`, e, nos testes da cópia, `INDICADORES_DESTINO_RELATORIO`/
  `OneDrive` apontando para `tmp_path`) e nenhum teste registra tarefa
  real no Agendador.
- Exit codes do wrapper (`0`–`4`) espelham exatamente os já definidos
  em `specs/pipeline.md`; códigos de infraestrutura do wrapper
  (`≥ 10`) são uma faixa nova, deliberadamente sem sobreposição.
- Scripts PowerShell ficam em `scripts/`, paralelos a `src/` e `tests/`,
  já que não fazem parte do pacote Python `indicadores`.
- Variáveis de ambiente do wrapper com prefixo `INDICADORES_`; as de log
  e de espera são só para uso em testes, e `INDICADORES_DESTINO_RELATORIO`
  é configuração de uso real (decisão 8).
