"""Testes dos scripts PowerShell de agendamento (`scripts/`).

Só rodam no Windows (skip em qualquer outra plataforma). Nenhum teste
registra tarefa real no Agendador nem acessa a API do BCB: os cenários
de `executar_diario.ps1` sempre usam `--help` ou argumentos inválidos, e
`agendar_tarefa.ps1` é validado só por sintaxe (`Parser::ParseFile`),
nunca executado.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="scripts PowerShell só rodam no Windows"
)

RAIZ = Path(__file__).resolve().parent.parent
SCRIPT_EXECUTAR_DIARIO = RAIZ / "scripts" / "executar_diario.ps1"
SCRIPT_AGENDAR_TAREFA = RAIZ / "scripts" / "agendar_tarefa.ps1"


def _rodar_wrapper(
    tmp_path: Path, *args: str, timeout: int = 120
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT_EXECUTAR_DIARIO),
            *args,
        ],
        env={**os.environ, "INDICADORES_LOG_DIR": str(tmp_path)},
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _unico_log(tmp_path: Path) -> Path:
    """Encontra o único `execucao_*.log` gravado em `tmp_path`.

    Evita depender do nome exato (sensível à virada do mês entre o
    início e o fim da execução do teste).
    """
    logs = list(tmp_path.glob("execucao_*.log"))
    assert len(logs) == 1, f"esperado 1 log, encontrado {len(logs)}: {logs}"
    return logs[0]


def _conteudo_log_utf8_sem_bom(caminho_log: Path) -> str:
    bruto = caminho_log.read_bytes()
    assert not bruto.startswith(b"\xef\xbb\xbf"), "log não deve ter BOM UTF-8"
    return bruto.decode("utf-8")  # levanta UnicodeDecodeError se corrompido


# ---------------------------------------------------------------------------
# 1: --help retorna 0 e grava log
# ---------------------------------------------------------------------------


def test_executar_diario_help_retorna_zero_e_grava_log(tmp_path: Path):
    resultado = _rodar_wrapper(tmp_path, "--help")

    assert resultado.returncode == 0

    caminho_log = _unico_log(tmp_path)
    texto = _conteudo_log_utf8_sem_bom(caminho_log)

    assert "início" in texto
    assert "=====" in texto
    assert "usage: python -m indicadores" in texto
    assert "exit code: 0" in texto


# ---------------------------------------------------------------------------
# 2: log preserva acentos em UTF-8
# ---------------------------------------------------------------------------


def test_executar_diario_log_preserva_acentos_utf8(tmp_path: Path):
    resultado = _rodar_wrapper(tmp_path, "--help")

    assert resultado.returncode == 0

    caminho_log = _unico_log(tmp_path)
    texto = _conteudo_log_utf8_sem_bom(caminho_log)

    assert "séries" in texto


# ---------------------------------------------------------------------------
# 3: argumento de CLI inválido retorna 2
# ---------------------------------------------------------------------------


def test_executar_diario_argumento_invalido_retorna_dois(tmp_path: Path):
    resultado = _rodar_wrapper(tmp_path, "--series", "abc")

    assert resultado.returncode == 2

    caminho_log = _unico_log(tmp_path)
    texto = _conteudo_log_utf8_sem_bom(caminho_log)
    assert "exit code: 2" in texto


# ---------------------------------------------------------------------------
# 4: sem .venv, o wrapper retorna 10 sem tentar rodar o Python
# ---------------------------------------------------------------------------


def test_executar_diario_sem_venv_retorna_dez(tmp_path: Path):
    # Copia só o script (não o repositório inteiro) para uma árvore
    # isolada, sem `.venv`, para não mexer no `.venv` real.
    repo_falso = tmp_path / "repo"
    scripts_falso = repo_falso / "scripts"
    scripts_falso.mkdir(parents=True)
    copia_script = scripts_falso / "executar_diario.ps1"
    shutil.copy(SCRIPT_EXECUTAR_DIARIO, copia_script)

    log_dir = tmp_path / "logs"

    resultado = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(copia_script),
            "--help",
        ],
        env={**os.environ, "INDICADORES_LOG_DIR": str(log_dir)},
        capture_output=True,
        timeout=120,
        check=False,
    )

    assert resultado.returncode == 10

    caminho_log = _unico_log(log_dir)
    texto = _conteudo_log_utf8_sem_bom(caminho_log)
    assert "python do venv nao encontrado" in texto
    assert "exit code: 10" in texto


# ---------------------------------------------------------------------------
# Falha ao preparar o log (ex.: diretório pai é um arquivo comum)
# retorna exit code 12, sem log nenhum.
# ---------------------------------------------------------------------------


def test_wrapper_falha_ao_preparar_log_retorna_doze(tmp_path: Path):
    arquivo_comum = tmp_path / "arquivo"
    arquivo_comum.write_text("x", encoding="utf-8")
    log_dir = arquivo_comum / "logs"  # pai é um arquivo, não um diretório.

    resultado = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT_EXECUTAR_DIARIO),
            "--help",
        ],
        env={**os.environ, "INDICADORES_LOG_DIR": str(log_dir)},
        capture_output=True,
        timeout=120,
        check=False,
    )

    assert resultado.returncode == 12
    stderr_texto = resultado.stderr.decode("utf-8", errors="replace")
    assert "nao foi possivel preparar o log" in stderr_texto
    assert not log_dir.exists() or not list(log_dir.glob("execucao_*.log"))


# ---------------------------------------------------------------------------
# O wrapper não deixa arquivos temporários para trás, no sucesso nem no
# erro do processo Python.
# ---------------------------------------------------------------------------


def test_wrapper_nao_deixa_temporarios(tmp_path: Path):
    # TEMP/TMP próprios (respeitados por `[System.IO.Path]::GetTempFileName()`
    # do .NET) para não depender do %TEMP% real da máquina, onde outros
    # processos podem criar temporários ao mesmo tempo (fragilidade).
    temp_proprio = tmp_path / "meutemp"
    temp_proprio.mkdir()
    log_dir = tmp_path / "logs"

    env = {
        **os.environ,
        "INDICADORES_LOG_DIR": str(log_dir),
        "TEMP": str(temp_proprio),
        "TMP": str(temp_proprio),
    }

    for args in (["--help"], ["--series", "abc"]):
        resultado = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(SCRIPT_EXECUTAR_DIARIO),
                *args,
            ],
            env=env,
            capture_output=True,
            timeout=120,
            check=False,
        )
        # Confirma que o `Start-Process` funciona normalmente com um
        # TEMP customizado (não é só o `GetTempFileName` que precisa
        # respeitar a variável).
        assert resultado.returncode in (0, 2)

    restantes = list(temp_proprio.glob("tmp*.tmp"))
    assert restantes == []


# ---------------------------------------------------------------------------
# 5: múltiplos argumentos chegam intactos ao Python
# ---------------------------------------------------------------------------


def test_executar_diario_repassa_multiplos_argumentos(tmp_path: Path):
    resultado = _rodar_wrapper(tmp_path, "--series", "432", "433", "--help")

    assert resultado.returncode == 0

    caminho_log = _unico_log(tmp_path)
    texto = _conteudo_log_utf8_sem_bom(caminho_log)
    assert "usage: python -m indicadores" in texto


# ---------------------------------------------------------------------------
# 6: argumento com espaço chega como um único valor
# ---------------------------------------------------------------------------


def test_wrapper_repassa_argumento_com_espaco(tmp_path: Path):
    pasta_com_espaco = tmp_path / "pasta com espaço"
    caminho_banco = pasta_com_espaco / "x.duckdb"

    resultado = _rodar_wrapper(tmp_path, "--banco", str(caminho_banco), "--help")

    assert resultado.returncode == 0
    # `--help` sai antes de `abrir_conexao`: nem o diretório nem o
    # arquivo do banco devem ter sido criados.
    assert not pasta_com_espaco.exists()


# ---------------------------------------------------------------------------
# 7: agendar_tarefa.ps1 é sintaticamente válido (sem registrar tarefa)
# ---------------------------------------------------------------------------


def _validar_sintaxe(caminho_script: Path) -> str:
    comando = (
        "$e = $null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{caminho_script}', "
        "[ref]$null, [ref]$e) | Out-Null; "
        "$e.Count"
    )
    resultado = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            comando,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert resultado.returncode == 0, resultado.stderr
    return resultado.stdout.strip()


def test_agendar_tarefa_sintaxe_valida():
    assert _validar_sintaxe(SCRIPT_AGENDAR_TAREFA) == "0"


def test_executar_diario_sintaxe_valida():
    assert _validar_sintaxe(SCRIPT_EXECUTAR_DIARIO) == "0"


# ---------------------------------------------------------------------------
# 8: .gitignore ignora logs/
# ---------------------------------------------------------------------------


def test_gitignore_ignora_logs():
    conteudo = (RAIZ / ".gitignore").read_text(encoding="utf-8")
    linhas = conteudo.splitlines()
    assert "logs/" in linhas
