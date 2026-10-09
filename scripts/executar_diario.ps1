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

function Invoke-Pipeline {
    <#
        Executa `python -m indicadores` uma vez (tentativa $Tentativa),
        grava os blocos stdout/stderr no log e devolve o exit code
        (11 se o processo nao iniciou ou se o ExitCode veio nulo).
        Cuida dos proprios arquivos temporarios.
    #>
    param([Parameter(Mandatory = $true)][int]$Tentativa)

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
            Write-Log "ERRO ao iniciar o processo Python: $($_.Exception.Message)`r`n"
            return 11
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
            # Ignorado: o proximo bloco trata ExitCode nulo como falha.
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

        Write-Log "--- stdout (tentativa $Tentativa) ---`r`n"
        if ($stdoutTexto) { Write-Log $stdoutTexto }
        Write-Log "`r`n"
        Write-Log "--- stderr (tentativa $Tentativa) ---`r`n"
        if ($stderrTexto) { Write-Log $stderrTexto }
        Write-Log "`r`n"

        if ($null -eq $exitCodePython) {
            # $null nunca e tratado como sucesso: e sempre falha do wrapper.
            Write-Log "ERRO: nao foi possivel obter o exit code do processo Python.`r`n"
            return 11
        }
        return [int]$exitCodePython
    } finally {
        # Roda mesmo com `return` nos blocos internos. Excecao aceita: um
        # encerramento forcado pelo `ExecutionTimeLimit` do Agendador
        # (TerminateProcess) nao passa por nenhum `finally`; os temporarios
        # podem sobrar em %TEMP% - risco baixo e aceito.
        Remove-Item -Path $stdoutTmp, $stderrTmp -ErrorAction SilentlyContinue
    }
}

function Copy-RelatorioParaDestino {
    <#
        Etapa final (decisao 8): copia relatorio.html da raiz para a pasta
        de destino quando foi regenerado nesta execucao. Grava suas
        proprias linhas no log. Chamada so com exit final 0 ou 1; tem
        try/catch proprio e nunca propaga excecao (nunca altera o exit
        code nem vira 13).
    #>
    $destino = $null
    $tmpDestino = $null
    try {
        $origem = Join-Path $repoRoot "relatorio.html"
        $regenerado = $false
        if (Test-Path -Path $origem -PathType Leaf) {
            $regenerado = ((Get-Item -Path $origem).LastWriteTimeUtc -ge $inicioExecucaoUtc)
        }
        if (-not $regenerado) {
            Write-Log "Relatório não copiado: relatorio.html não foi regenerado nesta execução`r`n"
            return
        }

        if (-not [string]::IsNullOrWhiteSpace($env:INDICADORES_DESTINO_RELATORIO)) {
            $destino = $env:INDICADORES_DESTINO_RELATORIO
            if (-not [System.IO.Path]::IsPathRooted($destino)) {
                Write-Log "AVISO: relatório não copiado: INDICADORES_DESTINO_RELATORIO deve ser caminho absoluto ('$destino')`r`n"
                return
            }
        } elseif (-not [string]::IsNullOrWhiteSpace($env:OneDrive)) {
            $destino = Join-Path $env:OneDrive "indicadores-bcb"
        } else {
            Write-Log "AVISO: relatório não copiado: INDICADORES_DESTINO_RELATORIO não está definida e a variável OneDrive não existe`r`n"
            return
        }

        $arquivoFinal = Join-Path $destino "relatorio.html"
        $tmpDestino = Join-Path $destino "relatorio.html.tmp"
        New-Item -ItemType Directory -Force -Path $destino | Out-Null
        Copy-Item -Path $origem -Destination $tmpDestino -Force
        if (Test-Path -Path $arquivoFinal -PathType Leaf) {
            # [NullString]::Value: no PS 5.1 um $null puro vira "" e o
            # Replace rejeita o caminho de backup vazio.
            [System.IO.File]::Replace($tmpDestino, $arquivoFinal, [NullString]::Value)
        } else {
            [System.IO.File]::Move($tmpDestino, $arquivoFinal)
        }
        Write-Log "Relatório copiado para: $arquivoFinal`r`n"
    } catch {
        $mensagemCopia = $_.Exception.Message
        if ($tmpDestino) {
            try { Remove-Item -Path $tmpDestino -Force -ErrorAction Stop } catch { }
        }
        try {
            Write-Log "AVISO: falha ao copiar relatório para '$destino': $mensagemCopia`r`n"
        } catch { }
    }
}

$repoRoot  = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"

# Instante de inicio da execucao (UTC), antes do preparo do log: so um
# relatorio.html com LastWriteTimeUtc >= este valor foi regenerado agora.
$inicioExecucaoUtc = [datetime]::UtcNow

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

# Tudo o que vem depois da preparacao do log fica num try/catch de topo:
# qualquer excecao inesperada do proprio wrapper (GetTempFileName,
# Write-Log, laco de tentativas...) vira exit 13, com registro no log se
# ainda for possivel, ou em stderr caso contrario. 10, 11 e 12 mantem o
# significado proprio (venv ausente, falha ao iniciar, log inacessivel).
try {
    # Espera (em segundos) antes da nova tentativa quando o pipeline sai com 1
    # (ex.: rede ainda indisponivel logo apos o PC acordar). Padrao 300; pode
    # ser sobrescrita por INDICADORES_ESPERA_RETRY_SEGUNDOS. Parse estrito:
    # so digitos (NumberStyles.None), entao "+5", " 5 ", "-1" e "abc" sao
    # invalidos. Variavel ausente/vazia usa 300 sem aviso.
    $esperaRetrySegundos = 300
    $esperaBruta = $env:INDICADORES_ESPERA_RETRY_SEGUNDOS
    if (-not [string]::IsNullOrEmpty($esperaBruta)) {
        $esperaLida = 0
        $valida = [int]::TryParse(
            $esperaBruta,
            [System.Globalization.NumberStyles]::None,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [ref]$esperaLida
        )
        if ($valida) {
            $esperaRetrySegundos = $esperaLida
        } else {
            Write-Log "AVISO: valor inválido em INDICADORES_ESPERA_RETRY_SEGUNDOS ('$esperaBruta'); usando 300`r`n"
        }
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

    $exitFinal = Invoke-Pipeline -Tentativa 1
    if ($exitFinal -eq 1) {
        # Uma unica nova tentativa; nunca ha tentativa 3. Os demais codigos
        # (0, 2, 3, 10, 11, 12) nunca disparam nova tentativa.
        Write-Log "AVISO: exit 1; nova tentativa em $esperaRetrySegundos s`r`n"
        Start-Sleep -Seconds $esperaRetrySegundos
        $exitFinal = Invoke-Pipeline -Tentativa 2
    }

    # Copia do relatorio: uma unica vez, depois da ultima tentativa e antes
    # do rodape; so com exit final 0 ou 1. A funcao nunca lanca excecao.
    if ($exitFinal -eq 0 -or $exitFinal -eq 1) {
        Copy-RelatorioParaDestino
    }

    $fim = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Log "===== $fim - fim (exit code: $([int]$exitFinal)) =====`r`n"
    exit [int]$exitFinal
} catch {
    $mensagemInesperada = $_.Exception.Message
    try {
        Write-Log "ERRO inesperado no wrapper: $mensagemInesperada`r`n"
        $fimInesperado = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        Write-Log "===== $fimInesperado - fim (exit code: 13) =====`r`n"
    } catch {
        [Console]::Error.WriteLine("ERRO inesperado no wrapper: $mensagemInesperada")
    }
    exit 13
}
