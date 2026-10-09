"""Testes dos scripts PowerShell de agendamento (`scripts/`).

Só rodam no Windows (skip em qualquer outra plataforma). Nenhum teste
registra tarefa real no Agendador nem acessa a API do BCB: os cenários
de `executar_diario.ps1` sempre usam `--help` ou argumentos inválidos, e
`agendar_tarefa.ps1` é validado só por sintaxe (`Parser::ParseFile`),
nunca executado.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import threading
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


class _ProxyLocal:
    """Servidor TCP local que aceita conexões, conta e fecha em seguida.

    Usado como HTTP(S)_PROXY: o httpx tenta conectar nele (contador >= 1
    prova que a requisição foi pelo proxy local, não pela internet) e a
    conexão fechada logo após o accept vira erro de conexão.
    """

    def __init__(self) -> None:
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(16)
        self.porta: int = self._socket.getsockname()[1]
        self.conexoes = 0
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._aceitar, daemon=True)
        self._thread.start()

    def _aceitar(self) -> None:
        while True:
            try:
                conexao, _ = self._socket.accept()
            except OSError:
                return  # socket fechado no teardown.
            with self._lock:
                self.conexoes += 1
            conexao.close()

    def env(self) -> dict[str, str]:
        url = f"http://127.0.0.1:{self.porta}"
        return {"HTTPS_PROXY": url, "HTTP_PROXY": url}

    def encerrar(self) -> None:
        self._socket.close()
        self._thread.join(timeout=5)


@pytest.fixture
def proxy_local():
    proxy = _ProxyLocal()
    try:
        yield proxy
    finally:
        proxy.encerrar()


def _env_sem_no_proxy() -> dict[str, str]:
    return {
        k: v for k, v in os.environ.items() if k.upper() != "NO_PROXY"
    }


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
# Falha inesperada depois que o log foi preparado -> exit 13.
# ---------------------------------------------------------------------------


def test_wrapper_falha_inesperada_apos_log_retorna_treze(tmp_path: Path):
    # TEMP/TMP com pai sendo um arquivo comum: `GetTempFileName()` falha
    # depois do cabeçalho do log. (Um diretório simplesmente inexistente
    # não serve: o Windows cai num diretório temporário alternativo e o
    # wrapper termina com 0.)
    arquivo_comum = tmp_path / "arquivo"
    arquivo_comum.write_text("x", encoding="utf-8")
    temp_invalido = str(arquivo_comum / "temp")
    log_dir = tmp_path / "logs"

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
        env={
            **os.environ,
            "INDICADORES_LOG_DIR": str(log_dir),
            "TEMP": temp_invalido,
            "TMP": temp_invalido,
        },
        capture_output=True,
        timeout=120,
        check=False,
    )

    assert resultado.returncode == 13
    texto = _conteudo_log_utf8_sem_bom(_unico_log(log_dir))
    assert "ERRO inesperado no wrapper" in texto
    assert "exit code: 13" in texto
    assert "tentativa 2" not in texto


# ---------------------------------------------------------------------------
# O wrapper não deixa arquivos temporários para trás, no sucesso nem no
# erro do processo Python.
# ---------------------------------------------------------------------------


def test_wrapper_nao_deixa_temporarios(tmp_path: Path, proxy_local):
    # TEMP/TMP próprios (respeitados por `[System.IO.Path]::GetTempFileName()`
    # do .NET) para não depender do %TEMP% real da máquina, onde outros
    # processos podem criar temporários ao mesmo tempo (fragilidade).
    temp_proprio = tmp_path / "meutemp"
    temp_proprio.mkdir()
    log_dir = tmp_path / "logs"

    env = {
        **_env_sem_no_proxy(),
        "INDICADORES_LOG_DIR": str(log_dir),
        "TEMP": str(temp_proprio),
        "TMP": str(temp_proprio),
    }
    env_queda_de_rede = {
        **env,
        **proxy_local.env(),
        "INDICADORES_ESPERA_RETRY_SEGUNDOS": "0",
    }

    casos = [
        (env, ["--help"], 0),
        (env, ["--series", "abc"], 2),
        # Queda de rede: exit 1 + nova tentativa (4 arquivos temporários
        # criados e removidos ao longo das duas tentativas).
        (env_queda_de_rede, ["--series", "432", "--banco", str(tmp_path / "x.duckdb")], 1),
    ]

    for env_caso, args, codigo_esperado in casos:
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
            env=env_caso,
            capture_output=True,
            timeout=120,
            check=False,
        )
        # Confirma que o `Start-Process` funciona normalmente com um
        # TEMP customizado (não é só o `GetTempFileName` que precisa
        # respeitar a variável).
        assert resultado.returncode == codigo_esperado

    restantes = list(temp_proprio.glob("tmp*.tmp"))
    assert restantes == []


# ---------------------------------------------------------------------------
# Nova tentativa: só o exit 1 do pipeline dispara uma (única) nova execução.
# ---------------------------------------------------------------------------


def _rodar_wrapper_com_env(
    tmp_path: Path, env_extra: dict[str, str], *args: str
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
        env={**_env_sem_no_proxy(), "INDICADORES_LOG_DIR": str(tmp_path), **env_extra},
        capture_output=True,
        timeout=120,
        check=False,
    )


def test_wrapper_exit_1_faz_uma_nova_tentativa(tmp_path: Path, proxy_local):
    # Proxy local que aceita e fecha a conexão: o httpx falha com erro de
    # conexão sem sair da máquina (nenhum acesso real à API do BCB).
    env_extra = {
        **proxy_local.env(),
        "INDICADORES_ESPERA_RETRY_SEGUNDOS": "0",
    }
    banco = tmp_path / "x.duckdb"

    resultado = _rodar_wrapper_com_env(
        tmp_path, env_extra, "--series", "432", "--banco", str(banco)
    )

    assert resultado.returncode == 1
    # Prova de que a falha veio do proxy local, não da internet.
    assert proxy_local.conexoes >= 1

    texto = _conteudo_log_utf8_sem_bom(_unico_log(tmp_path))
    assert texto.count("--- stdout (tentativa 1) ---") == 1
    assert texto.count("--- stdout (tentativa 2) ---") == 1
    assert "tentativa 3" not in texto
    assert "AVISO: exit 1; nova tentativa em 0 s" in texto
    assert "exit code: 1" in texto

    # O erro de conexão também aparece na tentativa 2 (trecho depois do
    # marcador), não só na 1.
    depois_da_tentativa_2 = texto.split("--- stdout (tentativa 2) ---", 1)[1]
    assert "erro de conexão" in depois_da_tentativa_2


def test_wrapper_exit_2_nao_tenta_de_novo(tmp_path: Path):
    resultado = _rodar_wrapper(tmp_path, "--series", "abc")

    assert resultado.returncode == 2

    texto = _conteudo_log_utf8_sem_bom(_unico_log(tmp_path))
    assert "tentativa 2" not in texto
    assert "AVISO: exit 1" not in texto
    assert "exit code: 2" in texto


@pytest.mark.parametrize("valor_invalido", ["abc", " 5 ", "+5"])
def test_wrapper_espera_retry_invalida_registra_aviso(tmp_path: Path, valor_invalido):
    # Valor inválido cai no padrão (300 s). Para não esperar de verdade,
    # usa um caso de exit 2 (sem nova tentativa).
    resultado = _rodar_wrapper_com_env(
        tmp_path,
        {"INDICADORES_ESPERA_RETRY_SEGUNDOS": valor_invalido},
        "--series",
        "abc",
    )

    assert resultado.returncode == 2

    texto = _conteudo_log_utf8_sem_bom(_unico_log(tmp_path))
    assert (
        f"AVISO: valor inválido em INDICADORES_ESPERA_RETRY_SEGUNDOS ('{valor_invalido}')"
        in texto
    )
    assert "exit code: 2" in texto


def test_wrapper_espera_retry_vazia_nao_gera_aviso(tmp_path: Path):
    resultado = _rodar_wrapper_com_env(
        tmp_path, {"INDICADORES_ESPERA_RETRY_SEGUNDOS": ""}, "--series", "abc"
    )

    assert resultado.returncode == 2

    texto = _conteudo_log_utf8_sem_bom(_unico_log(tmp_path))
    assert "AVISO: valor inválido" not in texto


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


def _texto_agendar_tarefa() -> str:
    return SCRIPT_AGENDAR_TAREFA.read_text(encoding="utf-8")


def test_agendar_tarefa_usa_run_only_if_network_available():
    assert "-RunOnlyIfNetworkAvailable" in _texto_agendar_tarefa()


def test_agendar_tarefa_usa_wake_to_run():
    assert "-WakeToRun" in _texto_agendar_tarefa()


def test_agendar_tarefa_limite_de_execucao_60_minutos():
    texto = _texto_agendar_tarefa()
    assert "-ExecutionTimeLimit" in texto
    assert "-Minutes 60" in texto
    assert "-Minutes 30" not in texto


def test_agendar_tarefa_gatilho_as_16h():
    texto = _texto_agendar_tarefa()
    assert "-At (Get-Date -Hour 16 -Minute 0 -Second 0)" in texto
    # DateTime por componentes: nenhuma string literal de horário em -At.
    assert not re.search(r"""-At\s+["'][^"']*["']""", texto)
    assert '-At "16:00"' not in texto


# ---------------------------------------------------------------------------
# 8: .gitignore ignora logs/
# ---------------------------------------------------------------------------


def test_gitignore_ignora_logs():
    conteudo = (RAIZ / ".gitignore").read_text(encoding="utf-8")
    linhas = conteudo.splitlines()
    assert "logs/" in linhas
