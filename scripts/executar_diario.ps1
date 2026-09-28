<#
    Wrapper fino chamado pela tarefa agendada "indicadores-bcb diario"
    (ou manualmente, para depurar). Resolve o Python do .venv, ajusta
    variaveis de ambiente, executa `python -m indicadores <args>` e
    grava cabecalho/stdout/stderr/rodape em um log mensal.

    Nao ha logica de negocio aqui - so agendamento/orquestracao de
    processo. Nenhum parametro proprio e declarado: tudo que vier depois
    de `-File executar_diario.ps1` cai em $args e e repassado
    integralmente para `python -m indicadores`.
#>

$ErrorActionPreference = "Stop"

function Write-Log {
    <# Acrescenta $Texto ao log mensal, em UTF-8 sem BOM (append). #>
    param([Parameter(Mandatory = $true)][string]$Texto)

    $codificacao = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::AppendAllText($script:CaminhoLog, $Texto, $codificacao)
}

function Format-Argumento {
    <#
        Envolve $Valor em aspas quando ele contem espaco, tab, aspas ou
        e vazio, para que Start-Process -ArgumentList <array> (que
        apenas junta os elementos com espaco, sem citar nada) produza
        uma linha de comando que o CommandLineToArgvW do processo filho
        interprete como um unico argumento. Segue as regras completas de
        escape do CommandLineToArgvW (mesmo algoritmo usado por
        ArgvQuote em C/C++):
          - uma sequencia de N barras invertidas seguida de " vira 2N+1
            barras e \" ;
          - uma sequencia de N barras no fim do argumento (antes da
            aspa de fechamento que este metodo adiciona) vira 2N barras;
          - barras que nao antecedem uma aspa ficam como estao.
    #>
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Valor)

    if ($Valor -eq "") {
        return '""'
    }

    if ($Valor -notmatch '[\s"]') {
        return $Valor
    }

    $resultado = New-Object System.Text.StringBuilder
    [void]$resultado.Append('"')

    $i = 0
    $n = $Valor.Length
    while ($i -lt $n) {
        $numBarras = 0
        while ($i -lt $n -and $Valor[$i] -eq '\') {
            $numBarras++
            $i++
        }

        if ($i -eq $n) {
            # Barras no fim do argumento: dobram antes da aspa final.
            [void]$resultado.Append('\' * ($numBarras * 2))
            break
        } elseif ($Valor[$i] -eq '"') {
            # Barras seguidas de aspa: 2N+1 barras + aspa escapada.
            [void]$resultado.Append('\' * ($numBarras * 2 + 1))
            [void]$resultado.Append('"')
            $i++
        } else {
            # Barras nao seguidas de aspa: ficam como estao.
            [void]$resultado.Append('\' * $numBarras)
            [void]$resultado.Append($Valor[$i])
            $i++
        }
    }

    [void]$resultado.Append('"')
    return $resultado.ToString()
}

$repoRoot  = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"

$logDir = if ($env:INDICADORES_LOG_DIR) { $env:INDICADORES_LOG_DIR } else { Join-Path $repoRoot "logs" }

# Preparar o log (criar diretorio, montar o caminho do arquivo do mes e
# gravar o cabecalho) tem seu proprio try/catch, separado do resto do
# fluxo: se isso falhar nao ha arquivo de log nenhum para registrar o
# erro, entao a mensagem vai direto para stderr do console, com um
# codigo de infraestrutura proprio (12) que nunca deve ser confundido
# com o "1" (serie ignorada) do proprio pipeline Python.
try {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $nomeArquivoLog = "execucao_{0}.log" -f (Get-Date -Format "yyyy-MM")
    $script:CaminhoLog = Join-Path $logDir $nomeArquivoLog

    $inicio = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Log "===== $inicio - início =====`r`n"
} catch {
    [Console]::Error.WriteLine("ERRO: nao foi possivel preparar o log em '$logDir': $($_.Exception.Message)")
    exit 12
}

if (-not (Test-Path -Path $pythonExe -PathType Leaf)) {
    $fim = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Log "ERRO: python do venv nao encontrado em '$pythonExe'.`r`n"
    Write-Log "===== $fim - fim (exit code: 10) =====`r`n"
    exit 10
}

$env:PYTHONPATH       = Join-Path $repoRoot "src"
$env:PYTHONUTF8       = "1"
$env:PYTHONIOENCODING = "utf-8"

$argumentosPython     = @("-m", "indicadores") + $args
$argumentosFormatados = $argumentosPython | ForEach-Object { Format-Argumento $_ }

$stdoutTmp = [System.IO.Path]::GetTempFileName()
$stderrTmp = [System.IO.Path]::GetTempFileName()

try {
    $processo = $null
    try {
        $processo = Start-Process -FilePath $pythonExe `
            -ArgumentList $argumentosFormatados `
            -WorkingDirectory $repoRoot `
            -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput $stdoutTmp `
            -RedirectStandardError $stderrTmp
    } catch {
        $fim = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        Write-Log "ERRO ao iniciar o processo Python: $($_.Exception.Message)`r`n"
        Write-Log "===== $fim - fim (exit code: 11) =====`r`n"
        exit 11
    }

    # Contorno de uma armadilha conhecida do PowerShell 5.1: mesmo com
    # -Wait, em alguns cenarios $processo.ExitCode pode vir vazio se o
    # handle nativo do processo foi liberado cedo demais. Acessar
    # .Handle e chamar WaitForExit() (idempotente em processo ja
    # encerrado) forca o CLR a manter o handle e preencher ExitCode.
    try {
        $processo.Handle | Out-Null
        $processo.WaitForExit()
    } catch {
        # Ignorado: o processo ja pode ter terminado/sido descartado;
        # o proximo bloco trata $exitCodePython nulo como falha.
    }
    $exitCodePython = $processo.ExitCode

    try {
        $stdoutTexto = Get-Content -Raw -Encoding UTF8 -Path $stdoutTmp -ErrorAction Stop
    } catch {
        Write-Log "AVISO: falha ao ler stdout do processo: $($_.Exception.Message)`r`n"
        $stdoutTexto = $null
    }
    try {
        $stderrTexto = Get-Content -Raw -Encoding UTF8 -Path $stderrTmp -ErrorAction Stop
    } catch {
        Write-Log "AVISO: falha ao ler stderr do processo: $($_.Exception.Message)`r`n"
        $stderrTexto = $null
    }

    Write-Log "--- stdout ---`r`n"
    if ($stdoutTexto) { Write-Log $stdoutTexto }
    Write-Log "`r`n"
    Write-Log "--- stderr ---`r`n"
    if ($stderrTexto) { Write-Log $stderrTexto }
    Write-Log "`r`n"

    if ($null -eq $exitCodePython) {
        # $null nunca e tratado como sucesso: e sempre falha do wrapper.
        $fim = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        Write-Log "ERRO: nao foi possivel obter o exit code do processo Python.`r`n"
        Write-Log "===== $fim - fim (exit code: 11) =====`r`n"
        exit 11
    }

    $fim = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Log "===== $fim - fim (exit code: $exitCodePython) =====`r`n"
    exit $exitCodePython
} finally {
    # Este `finally` roda mesmo quando um `catch` interno (acima) chama
    # `exit` (confirmado no PowerShell 5.1: `exit` dentro de um bloco
    # protegido ainda aciona o `finally` correspondente antes de encerrar
    # o processo). Excecao aceita: um encerramento forcado pelo
    # `ExecutionTimeLimit` do Agendador de Tarefas (TerminateProcess) mata
    # o processo de fora para dentro e nao passa por nenhum `finally`;
    # nesse caso os arquivos temporarios abaixo podem sobrar em %TEMP%
    # ate uma limpeza externa do sistema - risco baixo e aceito, sem
    # tratamento especial aqui.
    Remove-Item -Path $stdoutTmp, $stderrTmp -ErrorAction SilentlyContinue
}
