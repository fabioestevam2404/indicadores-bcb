# Especificação — Agendamento Diário (Agendador de Tarefas do Windows)

## Status
Quinto "módulo" do projeto, e o primeiro que não é Python: dois scripts
PowerShell em `scripts/`, para agendar `python -m indicadores` no
Agendador de Tarefas do Windows. O pipeline e a CLI já existem e estão
prontos (lidos diretamente de `specs/pipeline.md` e
`src/indicadores/__main__.py` para confirmar exit codes e canais de
I/O): exit codes `0` (sucesso total), `1` (alguma série ignorada), `2`
(uso de CLI inválido, via `argparse`), `3` (erro inesperado); resumo
sempre em stdout, logs sempre em stderr.

## Objetivo
Rodar `python -m indicadores` automaticamente, todo dia útil às 19:00,
com o usuário logado, registrando cada execução (comando, saída, exit
code, horário) em um log mensal legível, sem exigir que ninguém abra um
terminal manualmente.

Não há nenhuma lógica nova de negócio aqui — só agendamento e um
wrapper fino que invoca a CLI já pronta e captura sua saída de forma
segura no PowerShell 5.1 (que tem uma armadilha conhecida com
`2>&1` em executáveis nativos — ver "Captura de stdout/stderr").

## Decisões já tomadas pelo usuário (não reabertas)
1. A tarefa roda às **19:00, de segunda a sexta**.
2. Roda **só com o usuário logado** (`LogonType Interactive`, sem pedir
   senha), com `StartWhenAvailable`: se o PC estava desligado no
   horário, a tarefa roda assim que possível após o logon.

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
Python).

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

### Repasse de argumentos extras
O script não declara nenhum `param()` próprio — tudo que vier depois de
`-File scripts\executar_diario.ps1` cai na variável automática `$args`
do PowerShell, e é repassado integralmente para
`python -m indicadores`:
```powershell
$argumentosPython = @("-m", "indicadores") + $args
```
Isso é o que permite `... -File executar_diario.ps1 --help` testar o
wrapper inteiro (incluindo a gravação do log) sem tocar na API do BCB —
`--help` do `argparse` sai com exit code `0` sem fazer nenhuma
chamada de rede.

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
`%TEMP%` mesmo se a leitura falhar.

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
forma.

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
- Conteúdo de cada execução, nesta ordem:
  1. Cabeçalho: `===== <AAAA-MM-DD HH:mm:ss> - início =====`.
  2. Bloco `--- stdout ---` seguido do `$stdoutTexto` (o resumo do
     pipeline, quando a execução chega a rodar o Python).
  3. Bloco `--- stderr ---` seguido do `$stderrTexto` (os logs do
     pipeline), ou a linha `AVISO: ...` no lugar, se a leitura falhou
     (ver "Captura de stdout/stderr").
  4. Rodapé: `===== <AAAA-MM-DD HH:mm:ss> - fim (exit code: <N>)
     =====`.
  Se o wrapper falha **antes** de conseguir rodar o Python (ex.: venv
  ausente), os blocos de stdout/stderr do Python são omitidos, e o
  rodapé registra o exit code de infraestrutura correspondente com uma
  linha de erro explicando o motivo.
- Se o próprio preparo do log falhar (ver "Exit codes do wrapper",
  código `12`), não existe rodapé nem cabeçalho nenhum — o arquivo de
  log pode nem existir; a mensagem de erro vai para **stderr** do
  processo do wrapper, não para um arquivo.

### Exit codes do wrapper
| Código | Situação |
|---|---|
| `0`–`3` | Repassado **exatamente** como veio do processo Python (`$processo.ExitCode`) — mesma semântica de `specs/pipeline.md`/`__main__.py`. |
| `10` | `.venv\Scripts\python.exe` não encontrado — infraestrutura local ausente, o Python nunca chega a rodar. |
| `11` | Falha ao iniciar o processo Python via `Start-Process` (ex.: exceção do próprio PowerShell antes de conseguir spawnar o processo); **ou** `$processo.ExitCode` vem `$null` — cenário raro relatado em alguns ambientes de PowerShell 5.1 com `-PassThru`. |
| `12` | Não foi possível **preparar o log**: falha ao criar o diretório de log (`New-Item -ItemType Directory`) ou falha ao gravar o cabeçalho inicial (a primeira escrita via `AppendAllText`). Nesse caso a mensagem de erro vai para **stderr** do próprio wrapper, porque o arquivo de log não existe ou não pôde ser preparado para receber a mensagem. |

Decisão registrada: os códigos de infraestrutura do **wrapper**
começam em `10`, deliberadamente fora da faixa `0`–`3` já usada pelo
Python (`specs/pipeline.md`), para que quem olhar o "Resultado da
última execução" no Agendador de Tarefas consiga distinguir, só pelo
número, "o pipeline rodou e teve um problema" (`0`–`3`) de "o wrapper
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
  `ExecutionTimeLimit` de 30 minutos (configurado em
  `agendar_tarefa.ps1`), o processo do wrapper é terminado
  externamente, e o `finally` que apagaria os arquivos temporários de
  stdout/stderr **não chega a rodar** — nesse cenário específico, os
  arquivos temporários em `%TEMP%` podem ficar para trás. Decisão:
  risco aceito, considerado baixo (o cenário exige que o próprio
  pipeline trave por mais de 30 minutos, o que já seria, por si só, um
  problema a ser investigado manualmente) e fora do controle do
  wrapper (o encerramento é externo, pelo sistema operacional/Agendador,
  não algo que um `finally` dentro do próprio processo consiga
  interceptar).

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
  às 19:00, construído como
  `New-ScheduledTaskTrigger -Weekly -DaysOfWeek ... -At (Get-Date -Hour
  19 -Minute 0 -Second 0)`. Decisão registrada: usa um objeto
  `DateTime` montado com `Get-Date -Hour 19 -Minute 0 -Second 0`, **não**
  a string literal `"19:00"`. Motivo: `-At` aceita tanto `DateTime`
  quanto string, mas quando recebe uma string o PowerShell precisa
  interpretá-la como hora usando as configurações de **cultura/locale**
  do Windows configuradas na máquina (formato de hora 12h/24h,
  separador, etc.) — em uma cultura diferente da esperada, `"19:00"`
  poderia ser interpretada de forma diferente da intenção (19h). Um
  `DateTime` construído explicitamente por componentes (`-Hour 19
  -Minute 0 -Second 0`) não depende de nenhuma interpretação de string
  sensível a cultura.
- **Configurações** (`New-ScheduledTaskSettingsSet`):
  - `-StartWhenAvailable` (roda assim que possível se o horário foi
    perdido com o PC desligado).
  - `-MultipleInstances IgnoreNew`: se uma execução anterior ainda
    estiver rodando no horário da próxima, a nova é **ignorada**, não
    enfileirada nem executada em paralelo. Motivo registrado: duas
    execuções simultâneas apontando para o mesmo arquivo DuckDB
    falhariam de qualquer forma (DuckDB é single-writer por arquivo — a
    segunda falharia ao abrir o arquivo bloqueado, exit code `3`, como
    já registrado em `specs/pipeline.md`); `IgnoreNew` evita gastar o
    esforço de tentar e falhar, simplesmente pulando a segunda.
  - `-ExecutionTimeLimit` de 30 minutos — mata a tarefa se ela travar
    além desse tempo (a extração de 3 séries de 5 anos não deveria
    levar perto disso; é uma rede de segurança, não um limite
    ajustado por medição fina). Ver "Limitações conhecidas" quanto ao
    efeito colateral disso nos temporários do wrapper.
  - Permite rodar na bateria (`-AllowStartIfOnBatteries` e
    `-DontStopIfGoingOnBatteries`), já que é um notebook/desktop com
    uso normal, não um servidor sempre ligado na tomada.
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

### `agendar_tarefa.ps1`, só validação de sintaxe
Sem registrar tarefa nenhuma: valida que o arquivo é PowerShell
sintaticamente válido, via
`[System.Management.Automation.Language.Parser]::ParseFile(...)`
chamado dentro de um processo `powershell.exe` descartável (não requer
o módulo `ScheduledTasks` nem privilégio nenhum, e não executa o
conteúdo do script).

## Regras de negócio
1. O wrapper nunca acessa a rede nem o DuckDB diretamente — só invoca
   `python -m indicadores` como subprocesso e captura sua saída.
2. O exit code do wrapper é sempre o exit code do Python (`0`–`3`),
   exceto quando o próprio wrapper falha antes de conseguir rodar o
   Python, não consegue determinar o exit code real, ou não consegue
   preparar o log (`≥ 10`).
3. O log é sempre append, UTF-8 sem BOM, um arquivo por mês; nunca
   sobrescreve execuções anteriores do mesmo mês.
4. `INDICADORES_LOG_DIR` só existe para permitir testes sem escrever em
   `logs/` do repositório real.
5. `agendar_tarefa.ps1 -Force` é idempotente: rodar de novo só
   atualiza a tarefa, nunca duplica nem falha por já existir.
6. A tarefa nunca roda com privilégio de administrador
   (`RunLevel Limited`).
7. Argumentos com espaço ou aspas são citados corretamente ao montar a
   linha de comando do processo Python — nunca quebrados em múltiplos
   argumentos posicionais por causa de um espaço interno.
8. `$processo.ExitCode` nulo nunca é interpretado como sucesso.
9. Falha ao ler os arquivos temporários de stdout/stderr nunca muda o
   exit code do wrapper — vira só uma linha `AVISO` no log.
10. Falha ao preparar o log (diretório ou cabeçalho) usa exit code
    `12` e reporta em stderr do próprio wrapper — nunca deixa o
    PowerShell terminar com o código genérico `1`.
11. O horário do gatilho da tarefa é construído com componentes
    explícitos (`Get-Date -Hour/-Minute/-Second`), não com uma string
    de hora dependente de cultura/locale.

## Fora de escopo
- Rotação/limpeza de logs (ver "Fora de escopo (deste script)" acima).
- Notificação por e-mail ou qualquer outro canal em caso de falha.
- Suporte a Linux/`cron` — é uma spec específica de Windows.
- Rodar sem o usuário logado (`LogonType` diferente de `Interactive`,
  ex. `S4U`/`ServiceAccount`) — decisão já tomada pelo usuário, fora de
  escopo mudar aqui.
- Conteúdo da seção de agendamento em `README.md`/`HANDOFF.md` (fica
  para o `doc-writer`).
- Limpeza de temporários órfãos deixados por um encerramento forçado do
  Agendador via `ExecutionTimeLimit` (ver "Limitações conhecidas").

## Critérios de aceite
1. `executar_diario.ps1 --help` roda até o fim, sem tocar a rede, com
   exit code `0`.
2. `executar_diario.ps1` grava um log em
   `<INDICADORES_LOG_DIR ou logs/>/execucao_<AAAA-MM>.log`, em append,
   contendo cabeçalho com timestamp de início, stdout, stderr, exit
   code e timestamp de fim.
3. O log está em UTF-8 sem BOM e sem corrupção de acentos (ex. a
   palavra "séries" do texto de ajuda do `argparse` aparece intacta).
4. `--series abc` (entrada inválida da CLI) resulta em exit code `2`
   no wrapper, refletindo o exit code do Python, registrado no log.
5. `.venv\Scripts\python.exe` ausente resulta em exit code `10`, com o
   motivo registrado no log, sem tentar rodar nada.
6. Um argumento com espaço embutido (ex. `--banco` apontando para um
   caminho com espaço) chega ao Python como um único valor, não como
   múltiplos argumentos posicionais quebrados.
7. `$processo.ExitCode` nulo resulta em exit code `11`, nunca em `0`.
8. Falha ao preparar o log (diretório inacessível ou impossível de
   criar) resulta em exit code `12`, com a mensagem de erro em stderr
   do processo do wrapper (não em arquivo).
9. O wrapper não deixa arquivos temporários (`GetTempFileName`) para
   trás após terminar, em nenhum dos cenários normais (exit `0`–`3`,
   `10`, `11`, `12`) — a única exceção aceita e registrada é o
   encerramento forçado por `ExecutionTimeLimit` do Agendador.
10. `agendar_tarefa.ps1` é sintaticamente válido
    (`Parser::ParseFile` sem erros), sem precisar registrar nenhuma
    tarefa de verdade para o teste passar.
11. `agendar_tarefa.ps1` constrói o horário do gatilho via `Get-Date`
    com componentes explícitos, não uma string literal de hora.
12. Nenhum teste deste arquivo roda fora do Windows (todos pulados via
    `skipif` em outras plataformas) nem acessa a API do BCB ou o
    Agendador de Tarefas real.
13. `.gitignore` ignora `logs/`.

## Casos de teste (pytest)
1. `test_executar_diario_help_retorna_zero_e_grava_log` — roda o
   wrapper com `--help` e `INDICADORES_LOG_DIR=tmp_path`; assert
   `returncode == 0`, existe exatamente um arquivo
   `execucao_<AAAA-MM>.log` em `tmp_path`, e ele contém as strings de
   cabeçalho (`"início"`), o texto de uso do `argparse` (ex.
   `"usage: python -m indicadores"`), e o rodapé com
   `"exit code: 0"`.
2. `test_executar_diario_log_preserva_acentos_utf8` — mesmo cenário do
   teste 1; abre o log com `encoding="utf-8"` e assert que a substring
   `"séries"` (presente na descrição do `argparse`, repassada via
   `--help`) aparece intacta, sem caracteres de substituição/mojibake.
3. `test_executar_diario_argumento_invalido_retorna_dois` — roda o
   wrapper com `--series abc`; assert `returncode == 2` e que o log
   contém `"exit code: 2"`.
4. `test_executar_diario_sem_venv_retorna_dez` — roda o wrapper a
   partir de uma cópia/estrutura de teste onde `.venv\Scripts\python.exe`
   não existe (ex.: apontando `$PSScriptRoot` para uma árvore de
   diretórios isolada em `tmp_path`, sem `.venv`); assert
   `returncode == 10` e que o log registra o motivo, sem indicar
   tentativa de rodar o Python.
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
8. `test_wrapper_nao_deixa_temporarios` — lista os arquivos
   `tmp*.tmp` (ou padrão equivalente de `GetTempFileName`) em
   `[System.IO.Path]::GetTempPath()` **antes** de rodar o wrapper;
   roda o wrapper duas vezes (uma com `--help`, exit `0`; outra com
   `--series abc`, exit `2`); lista os mesmos arquivos **depois**;
   assert que o conjunto de arquivos temporários não cresceu (mesmos
   arquivos antes e depois, dentro de uma margem razoável para
   temporários de outros processos do sistema alheios ao teste — ex.
   comparando só os arquivos cujo nome aparece associado ao processo
   do wrapper, se identificável, ou aceitando pequena tolerância e
   registrando isso como parte do próprio teste).
9. `test_agendar_tarefa_sintaxe_valida` — invoca `powershell.exe` para
   rodar `Parser::ParseFile` sobre `scripts/agendar_tarefa.ps1`; assert
   que a lista de erros de parse está vazia (exit `0`), sem registrar
   nenhuma tarefa real.
10. `test_gitignore_ignora_logs` — leitura simples do `.gitignore` do
    repositório; assert que a linha `logs/` está presente.

## Convenções seguidas
- Comentários e mensagens de log em português, consistente com o
  restante do projeto.
- Nenhum teste escreve fora de `tmp_path` (via `INDICADORES_LOG_DIR`) e
  nenhum teste registra tarefa real no Agendador.
- Exit codes do wrapper (`0`–`3`) espelham exatamente os já definidos
  em `specs/pipeline.md`; códigos de infraestrutura do wrapper
  (`≥ 10`) são uma faixa nova, deliberadamente sem sobreposição.
- Scripts PowerShell ficam em `scripts/`, paralelos a `src/` e `tests/`,
  já que não fazem parte do pacote Python `indicadores`.
