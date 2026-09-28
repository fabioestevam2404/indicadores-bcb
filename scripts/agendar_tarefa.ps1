<#
    Registra (sem -Remover) ou remove (-Remover) a tarefa
    "indicadores-bcb diario" no Agendador de Tarefas do Windows, para
    rodar `python -m indicadores` todo dia util as 19:00, com o usuario
    logado.

    Idempotente: rodar de novo so atualiza a tarefa existente
    (Register-ScheduledTask -Force), nunca duplica nem falha por ja
    existir.
#>

param(
    [switch]$Remover
)

$ErrorActionPreference = "Stop"

$nomeTarefa = "indicadores-bcb diario"
$repoRoot   = Split-Path -Parent $PSScriptRoot

if ($Remover) {
    if (Get-ScheduledTask -TaskName $nomeTarefa -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $nomeTarefa -Confirm:$false
        Write-Host "Tarefa '$nomeTarefa' removida."
    } else {
        Write-Host "Tarefa '$nomeTarefa' nao existia; nada a remover."
    }
    return
}

$caminhoScriptDiario = Join-Path $repoRoot "scripts\executar_diario.ps1"

$acao = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
    '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $caminhoScriptDiario
) -WorkingDirectory $repoRoot

$gatilho = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday `
    -At (Get-Date -Hour 19 -Minute 0 -Second 0)

$configuracoes = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $nomeTarefa `
    -Action $acao `
    -Trigger $gatilho `
    -Settings $configuracoes `
    -Principal $principal `
    -Force | Out-Null

$info = Get-ScheduledTaskInfo -TaskName $nomeTarefa

Write-Host "Tarefa registrada: $nomeTarefa"
Write-Host "Proxima execucao: $($info.NextRunTime)"
$caminhoScriptAgendar = Join-Path $repoRoot "scripts\agendar_tarefa.ps1"
Write-Host "Para remover: powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$caminhoScriptAgendar`" -Remover"
