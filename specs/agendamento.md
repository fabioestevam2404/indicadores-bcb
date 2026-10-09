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

## Objetivo
Rodar `python -m indicadores` automaticamente, todo dia útil às 16:00,
com o usuário logado, registrando cada execução (comando, saída, exit
code, horário) em um log mensal legível, sem exigir que ninguém abra um
terminal manualmente.

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
executada.

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

Variáveis de ambiente **lidas** pelo wrapper (não repassadas ao Python),
ambas pensadas para os testes:
- `INDICADORES_LOG_DIR` — diretório do log (ver "Log mensal").
- `INDICADORES_ESPERA_RETRY_SEGUNDOS` — espera antes da nova tentativa
  (ver "Nova tentativa quando o pipeline termina com exit 1").

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
  6. Rodapé: `===== <AAAA-MM-DD HH:mm:ss> - fim (exit code: <N>)
     =====`, com o exit code **final** (o da última tentativa
     executada).
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
| `0`–`4` | Repassado **exatamente** como veio do processo Python (`$processo.ExitCode`) da **última tentativa executada** — mesma semântica de `specs/pipeline.md`/`__main__.py` (o `4` é "dados gravados, relatório não gerado", definido em `specs/relatorio.md`). Só o exit `1` provoca uma nova tentativa; `0`, `2`, `3` e `4` encerram o wrapper de imediato. O wrapper **não muda** por causa do `4`: ele já repassa qualquer inteiro como veio e só repete no `1`. |
| `10` | `.venv\Scripts\python.exe` não encontrado — infraestrutura local ausente, o Python nunca chega a rodar. |
| `11` | Falha ao iniciar o processo Python via `Start-Process` (ex.: exceção do próprio PowerShell antes de conseguir spawnar o processo); **ou** `$processo.ExitCode` vem `$null` — cenário raro relatado em alguns ambientes de PowerShell 5.1 com `-PassThru`. Vale para qualquer das tentativas (inclusive a segunda: se a nova tentativa não consegue nem iniciar, o exit final é `11`, não `1`). |
| `12` | Não foi possível **preparar o log**: falha ao criar o diretório de log (`New-Item -ItemType Directory`) ou falha ao gravar o cabeçalho inicial (a primeira escrita via `AppendAllText`). Nesse caso a mensagem de erro vai para **stderr** do próprio wrapper, porque o arquivo de log não existe ou não pôde ser preparado para receber a mensagem. |
| `13` | **Erro inesperado no wrapper depois que o log foi preparado** — por exemplo disco cheio ao gravar no log, falha ao criar um arquivo temporário (`GetTempFileName`) ou qualquer outra exceção não tratada depois da gravação do cabeçalho. O wrapper registra `ERRO: <mensagem>` e o rodapé no log (melhor esforço; se a gravação no log falhar, a mensagem vai para stderr, como no `12`) e termina com `13`. Se o exit code do Python da tentativa em curso já era conhecido, a mensagem o informa ("exit do pipeline: N"), para não perder essa informação. |

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
`ExecutionTimeLimit` é a rede de segurança final.

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
   `python -m indicadores` como subprocesso e captura sua saída.
2. O exit code do wrapper é sempre o exit code do Python (`0`–`4`) da
   última tentativa executada, exceto quando o próprio wrapper falha
   antes de conseguir rodar o Python, não consegue determinar o exit
   code real, não consegue preparar o log ou sofre um erro inesperado
   depois disso (`≥ 10`).
3. O log é sempre append, UTF-8 sem BOM, um arquivo por mês; nunca
   sobrescreve execuções anteriores do mesmo mês.
4. `INDICADORES_LOG_DIR` e `INDICADORES_ESPERA_RETRY_SEGUNDOS` só
   existem para permitir testes sem escrever em `logs/` do repositório
   real e sem esperar 300 s.
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
- Alterar o plano de energia (despertadores na bateria, `powercfg`),
  acordar o PC desligado ou hibernado, manter a tarefa ativa após
  logoff/desligamento e ativar o histórico do Agendador (ver
  "Limitações conhecidas").
- Mais de uma nova tentativa, backoff crescente, nova tentativa para
  outros exit codes e teto para a espera (ver "Fora de escopo (deste
  script)").

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

## Convenções seguidas
- Comentários e mensagens de log em português, consistente com o
  restante do projeto.
- Nenhum teste escreve fora de `tmp_path` (via `INDICADORES_LOG_DIR` e
  `--banco`) e nenhum teste registra tarefa real no Agendador.
- Exit codes do wrapper (`0`–`4`) espelham exatamente os já definidos
  em `specs/pipeline.md`; códigos de infraestrutura do wrapper
  (`≥ 10`) são uma faixa nova, deliberadamente sem sobreposição.
- Scripts PowerShell ficam em `scripts/`, paralelos a `src/` e `tests/`,
  já que não fazem parte do pacote Python `indicadores`.
- Variáveis de ambiente do wrapper com prefixo `INDICADORES_`, só para
  uso em testes.
