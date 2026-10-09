"""Testes dos scripts PowerShell de agendamento (`scripts/`).

Só rodam no Windows (skip em qualquer outra plataforma). Nenhum teste
registra tarefa real no Agendador nem acessa a API do BCB: os cenários
de `executar_diario.ps1` sempre usam `--help` ou argumentos inválidos, e
`agendar_tarefa.ps1` é validado só por sintaxe (`Parser::ParseFile`),
nunca executado.
"""

from __future__ import annotations

import ctypes
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


def _env_herdado() -> dict[str, str]:
    """Ambiente herdado sem nada que leve a cópia do relatório ao OneDrive real.

    Remove `OneDrive*` (OneDrive, OneDriveConsumer, OneDriveCommercial) e
    `INDICADORES_DESTINO_RELATORIO`, sem diferenciar maiúsculas.
    """
    return {
        k: v
        for k, v in os.environ.items()
        if not k.upper().startswith("ONEDRIVE")
        and k.upper() != "INDICADORES_DESTINO_RELATORIO"
    }


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
        env={**_env_herdado(), "INDICADORES_LOG_DIR": str(tmp_path)},
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
    return {k: v for k, v in _env_herdado().items() if k.upper() != "NO_PROXY"}


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
        env={**_env_herdado(), "INDICADORES_LOG_DIR": str(log_dir)},
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
        env={**_env_herdado(), "INDICADORES_LOG_DIR": str(log_dir)},
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
            **_env_herdado(),
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
        # --sem-relatorio: com série ignorada o pipeline também gera o
        # relatório, que cairia em `relatorio.html` na raiz do repositório.
        (
            env_queda_de_rede,
            ["--series", "432", "--banco", str(tmp_path / "x.duckdb"), "--sem-relatorio"],
            1,
        ),
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

    # --sem-relatorio: nunca escrever `relatorio.html` na raiz do repositório.
    resultado = _rodar_wrapper_com_env(
        tmp_path, env_extra, "--series", "432", "--banco", str(banco), "--sem-relatorio"
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


def test_gitignore_ignora_relatorio_html():
    linhas = (RAIZ / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "relatorio.html" in linhas
    assert "relatorio.html.tmp" in linhas


# ---------------------------------------------------------------------------
# Exit 4 (relatório não gerado): repassado como veio, sem nova tentativa.
# ---------------------------------------------------------------------------


def test_wrapper_repassa_exit_4_sem_nova_tentativa(tmp_path: Path):
    # Banco inexistente + --so-relatorio: o Python sai com 4 antes de
    # qualquer rede ou escrita (offline e rápido).
    banco = tmp_path / "nao_existe.duckdb"

    resultado = _rodar_wrapper(
        tmp_path,
        "--so-relatorio",
        "--banco",
        str(banco),
        "--relatorio",
        str(tmp_path / "r.html"),
    )

    assert resultado.returncode == 4
    texto = _conteudo_log_utf8_sem_bom(_unico_log(tmp_path))
    assert "fim (exit code: 4)" in texto
    assert "tentativa 2" not in texto
    assert "AVISO: exit 1" not in texto
    assert not banco.exists()
    assert not (tmp_path / "r.html").exists()


# ---------------------------------------------------------------------------
# Cópia do relatório para a pasta de destino (decisão 8) - testes 20 a 28 da
# spec, mais destino relativo e 2ª execução com destino já existente.
# Usam uma árvore falsa do repositório: nenhuma rede, nenhum DuckDB e nenhum
# acesso ao OneDrive real nem ao `relatorio.html` real da raiz do projeto.
# ---------------------------------------------------------------------------

_MAIN_FALSO = '''\
import os
import sys
from pathlib import Path

contador = Path(os.environ["FALSO_CONTADOR"])
n = int(contador.read_text()) + 1 if contador.exists() else 1
contador.write_text(str(n))

exits = [int(x) for x in os.environ.get("FALSO_EXITS", "0").split(",")]
codigo = exits[min(n, len(exits)) - 1]

if os.environ.get("FALSO_GERA_RELATORIO") == "1":
    Path("relatorio.html").write_text(
        "<html>tentativa=%d</html>" % n, encoding="utf-8"
    )

print("execucao falsa n=%d" % n)
sys.exit(codigo)
'''


class _ArvoreFalsa:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.raiz = tmp_path / "repo"
        self.log_dir = tmp_path / "logs"
        self.destino = tmp_path / "destino"
        self.script = self.raiz / "scripts" / "executar_diario.ps1"

    @property
    def relatorio(self) -> Path:
        return self.raiz / "relatorio.html"

    def executar(
        self,
        *,
        exits: str = "0",
        gera: str = "1",
        env_extra: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess:
        env = {
            **_env_herdado(),
            "INDICADORES_LOG_DIR": str(self.log_dir),
            "INDICADORES_ESPERA_RETRY_SEGUNDOS": "0",
            "FALSO_EXITS": exits,
            "FALSO_GERA_RELATORIO": gera,
            "FALSO_CONTADOR": str(self.tmp_path / "contador.txt"),
            **(env_extra or {}),
        }
        return subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.script),
            ],
            env=env,
            cwd=cwd,
            capture_output=True,
            timeout=120,
            check=False,
        )

    def log(self) -> str:
        return _conteudo_log_utf8_sem_bom(_unico_log(self.log_dir))


@pytest.fixture
def arvore_falsa(tmp_path: Path) -> _ArvoreFalsa:
    venv_real = RAIZ / ".venv"
    if not (venv_real / "Scripts" / "python.exe").exists():
        pytest.skip(".venv do projeto não encontrado para copiar o python.exe")

    arvore = _ArvoreFalsa(tmp_path)
    (arvore.raiz / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT_EXECUTAR_DIARIO, arvore.script)

    # O python.exe do venv é um launcher que acha a instalação base pelo
    # pyvenv.cfg um nível acima de Scripts (confirmado na prática).
    scripts_falso = arvore.raiz / ".venv" / "Scripts"
    scripts_falso.mkdir(parents=True)
    shutil.copy(venv_real / "Scripts" / "python.exe", scripts_falso / "python.exe")
    shutil.copy(venv_real / "pyvenv.cfg", arvore.raiz / ".venv" / "pyvenv.cfg")

    pacote = arvore.raiz / "src" / "indicadores"
    pacote.mkdir(parents=True)
    (pacote / "__init__.py").write_text("", encoding="utf-8")
    (pacote / "__main__.py").write_text(_MAIN_FALSO, encoding="utf-8")
    return arvore


def _tmps(tmp_path: Path) -> list[Path]:
    return list(tmp_path.rglob("relatorio.html.tmp"))


def _arquivos_relatorio_fora_da_raiz(arvore: _ArvoreFalsa) -> list[Path]:
    return [
        p for p in arvore.tmp_path.rglob("relatorio.html") if p != arvore.relatorio
    ]


def _destino_env(arvore: _ArvoreFalsa) -> dict[str, str]:
    return {"INDICADORES_DESTINO_RELATORIO": str(arvore.destino)}


def test_arvore_falsa_roda_sem_copia(arvore_falsa: _ArvoreFalsa):
    # Sanidade da infraestrutura: o python falso responde e o wrapper
    # nunca vê o relatório real do projeto.
    r = arvore_falsa.executar(exits="0", gera="0")

    assert r.returncode == 0
    assert "execucao falsa n=1" in arvore_falsa.log()


# 20
def test_wrapper_copia_relatorio_quando_exit_0(arvore_falsa: _ArvoreFalsa):
    arvore_falsa.destino.mkdir()

    r = arvore_falsa.executar(exits="0", env_extra=_destino_env(arvore_falsa))

    assert r.returncode == 0
    copiado = arvore_falsa.destino / "relatorio.html"
    assert copiado.read_bytes() == arvore_falsa.relatorio.read_bytes()
    assert not (arvore_falsa.destino / "relatorio.html.tmp").exists()

    texto = arvore_falsa.log()
    linha = f"Relatório copiado para: {copiado}"
    assert texto.count(linha) == 1
    assert texto.index("--- stderr (tentativa 1) ---") < texto.index(linha)
    assert texto.index(linha) < texto.index("exit code: 0")
    assert "AVISO" not in texto
    assert "tentativa 2" not in texto


# 21
@pytest.mark.parametrize(("exits", "esperado"), [("1,1", 1), ("1,0", 0)])
def test_wrapper_copia_relatorio_uma_unica_vez_apos_nova_tentativa(
    arvore_falsa: _ArvoreFalsa, exits: str, esperado: int
):
    r = arvore_falsa.executar(exits=exits, env_extra=_destino_env(arvore_falsa))

    assert r.returncode == esperado
    texto = arvore_falsa.log()
    assert "AVISO: exit 1; nova tentativa em 0 s" in texto
    assert texto.count("Relatório copiado para:") == 1
    pos = texto.index("Relatório copiado para:")
    assert texto.index("--- stderr (tentativa 2) ---") < pos
    assert pos < texto.index(f"exit code: {esperado}")
    assert "tentativa=2" in (arvore_falsa.destino / "relatorio.html").read_text(
        encoding="utf-8"
    )
    assert "tentativa 3" not in texto
    assert _tmps(arvore_falsa.tmp_path) == []


# 22
@pytest.mark.parametrize("exit_falso", ["2", "3", "4"])
def test_wrapper_nao_copia_relatorio_quando_exit_nao_e_0_nem_1(
    arvore_falsa: _ArvoreFalsa, exit_falso: str
):
    r = arvore_falsa.executar(exits=exit_falso, env_extra=_destino_env(arvore_falsa))

    assert r.returncode == int(exit_falso)
    assert arvore_falsa.relatorio.exists()  # o falso gerou: é o exit que impede.
    assert not (arvore_falsa.destino / "relatorio.html").exists()
    assert _tmps(arvore_falsa.tmp_path) == []
    texto = arvore_falsa.log()
    assert "Relatório copiado para:" not in texto
    assert "AVISO: falha ao copiar" not in texto
    assert "AVISO: relatório não copiado" not in texto
    assert "Relatório não copiado" not in texto


# 23
@pytest.mark.parametrize("arquivo_antigo", [True, False])
def test_wrapper_nao_copia_relatorio_antigo(
    arvore_falsa: _ArvoreFalsa, arquivo_antigo: bool
):
    if arquivo_antigo:
        arvore_falsa.relatorio.write_text("<html>ANTIGO</html>", encoding="utf-8")
        um_dia_atras = arvore_falsa.relatorio.stat().st_mtime - 86400
        os.utime(arvore_falsa.relatorio, (um_dia_atras, um_dia_atras))
    arvore_falsa.destino.mkdir()
    existente = arvore_falsa.destino / "relatorio.html"
    existente.write_text("<html>DESTINO</html>", encoding="utf-8")

    r = arvore_falsa.executar(
        exits="0", gera="0", env_extra=_destino_env(arvore_falsa)
    )

    assert r.returncode == 0
    assert existente.read_text(encoding="utf-8") == "<html>DESTINO</html>"
    assert _tmps(arvore_falsa.tmp_path) == []
    texto = arvore_falsa.log()
    assert (
        "Relatório não copiado: relatorio.html não foi regenerado nesta execução"
        in texto
    )
    assert "AVISO" not in texto
    assert "Relatório copiado para:" not in texto


# 24
def test_wrapper_destino_pela_variavel_prevalece_sobre_onedrive(
    arvore_falsa: _ArvoreFalsa,
):
    via_variavel = arvore_falsa.tmp_path / "via_variavel"
    onedrive_falso = arvore_falsa.tmp_path / "onedrive_falso"

    r = arvore_falsa.executar(
        exits="0",
        env_extra={
            "INDICADORES_DESTINO_RELATORIO": str(via_variavel),
            "OneDrive": str(onedrive_falso),
        },
    )

    assert r.returncode == 0
    assert (via_variavel / "relatorio.html").exists()
    assert not (via_variavel / "indicadores-bcb").exists()
    assert not onedrive_falso.exists()
    assert str(via_variavel) in arvore_falsa.log()


# 25
@pytest.mark.parametrize("variavel", ["ausente", "vazia"])
def test_wrapper_usa_onedrive_como_destino_padrao(
    arvore_falsa: _ArvoreFalsa, variavel: str
):
    onedrive_falso = arvore_falsa.tmp_path / "onedrive_falso"
    onedrive_falso.mkdir()
    env_extra = {"OneDrive": str(onedrive_falso)}
    if variavel == "vazia":
        env_extra["INDICADORES_DESTINO_RELATORIO"] = ""

    r = arvore_falsa.executar(exits="0", env_extra=env_extra)

    assert r.returncode == 0
    copiado = onedrive_falso / "indicadores-bcb" / "relatorio.html"
    assert copiado.read_bytes() == arvore_falsa.relatorio.read_bytes()
    assert f"Relatório copiado para: {copiado}" in arvore_falsa.log()


# 26
@pytest.mark.parametrize("exits", ["0", "1"])
def test_wrapper_sem_destino_registra_aviso_e_mantem_exit(
    arvore_falsa: _ArvoreFalsa, exits: str
):
    r = arvore_falsa.executar(exits=exits)

    assert r.returncode == int(exits)
    texto = arvore_falsa.log()
    assert "AVISO: relatório não copiado:" in texto
    assert f"exit code: {exits}" in texto
    assert "Relatório copiado para:" not in texto
    assert _arquivos_relatorio_fora_da_raiz(arvore_falsa) == []


# 27 (a): destino é um arquivo comum -> a pasta não pode ser criada.
@pytest.mark.parametrize("exits", ["0", "1"])
def test_wrapper_falha_na_copia_destino_e_arquivo(
    arvore_falsa: _ArvoreFalsa, exits: str
):
    arvore_falsa.destino.write_text("sou um arquivo", encoding="utf-8")

    r = arvore_falsa.executar(exits=exits, env_extra=_destino_env(arvore_falsa))

    assert r.returncode == int(exits)  # nunca 13
    texto = arvore_falsa.log()
    prefixo = f"AVISO: falha ao copiar relatório para '{arvore_falsa.destino}': "
    assert prefixo in texto
    resto = texto.split(prefixo, 1)[1].splitlines()[0]
    assert resto.strip() != ""
    assert "Relatório copiado para:" not in texto
    assert "tentativa 3" not in texto
    assert f"exit code: {exits}" in texto
    assert _tmps(arvore_falsa.tmp_path) == []
    assert arvore_falsa.destino.read_text(encoding="utf-8") == "sou um arquivo"


class _BloqueioExclusivo:
    """Abre um arquivo com dwShareMode=0 (sem compartilhamento) via ctypes."""

    def __init__(self, caminho: Path) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = ctypes.c_void_p
        kernel32.CreateFileW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self._kernel32 = kernel32
        generic_read, open_existing, normal = 0x80000000, 3, 0x80
        self._handle = kernel32.CreateFileW(
            str(caminho), generic_read, 0, None, open_existing, normal, None
        )
        if self._handle in (None, ctypes.c_void_p(-1).value):
            raise OSError(ctypes.get_last_error(), "CreateFileW falhou")

    def liberar(self) -> None:
        if self._handle is not None:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


# 27 (b): relatorio.html do destino bloqueado -> falha depois de criar o .tmp.
@pytest.mark.parametrize("exits", ["0", "1"])
def test_wrapper_falha_na_copia_destino_bloqueado(
    arvore_falsa: _ArvoreFalsa, exits: str
):
    arvore_falsa.destino.mkdir()
    existente = arvore_falsa.destino / "relatorio.html"
    existente.write_text("<html>ANTIGO</html>", encoding="utf-8")

    bloqueio = _BloqueioExclusivo(existente)
    try:
        r = arvore_falsa.executar(exits=exits, env_extra=_destino_env(arvore_falsa))
    finally:
        bloqueio.liberar()

    assert r.returncode == int(exits)  # nunca 13
    texto = arvore_falsa.log()
    prefixo = f"AVISO: falha ao copiar relatório para '{arvore_falsa.destino}': "
    assert prefixo in texto
    assert texto.split(prefixo, 1)[1].splitlines()[0].strip() != ""
    assert "Relatório copiado para:" not in texto
    assert "tentativa 3" not in texto
    assert existente.read_text(encoding="utf-8") == "<html>ANTIGO</html>"
    assert _tmps(arvore_falsa.tmp_path) == []


# 28
def test_wrapper_cria_pasta_de_destino_inexistente(arvore_falsa: _ArvoreFalsa):
    destino = arvore_falsa.tmp_path / "a" / "b" / "c"

    r = arvore_falsa.executar(
        exits="0", env_extra={"INDICADORES_DESTINO_RELATORIO": str(destino)}
    )

    assert r.returncode == 0
    copiado = destino / "relatorio.html"
    assert copiado.read_bytes() == arvore_falsa.relatorio.read_bytes()
    assert _tmps(arvore_falsa.tmp_path) == []


# Destino relativo: não copia, avisa e não altera o exit.
@pytest.mark.parametrize("exits", ["0", "1"])
def test_wrapper_destino_relativo_registra_aviso_e_nao_copia(
    arvore_falsa: _ArvoreFalsa, exits: str
):
    r = arvore_falsa.executar(
        exits=exits,
        env_extra={"INDICADORES_DESTINO_RELATORIO": "pasta_relativa"},
        cwd=arvore_falsa.tmp_path,
    )

    assert r.returncode == int(exits)
    texto = arvore_falsa.log()
    assert "AVISO" in texto
    assert "deve ser caminho absoluto" in texto
    assert "Relatório copiado para:" not in texto
    assert f"exit code: {exits}" in texto
    assert not list(arvore_falsa.tmp_path.rglob("pasta_relativa"))
    assert _arquivos_relatorio_fora_da_raiz(arvore_falsa) == []
    assert _tmps(arvore_falsa.tmp_path) == []


# 2ª execução com destino já existente: o arquivo é substituído (regressão do
# [IO.File]::Replace com $null no PowerShell 5.1).
def test_wrapper_segunda_execucao_substitui_relatorio_do_destino(
    arvore_falsa: _ArvoreFalsa,
):
    env_extra = _destino_env(arvore_falsa)
    copiado = arvore_falsa.destino / "relatorio.html"

    r1 = arvore_falsa.executar(exits="0", env_extra=env_extra)
    assert r1.returncode == 0
    assert copiado.read_text(encoding="utf-8") == "<html>tentativa=1</html>"

    r2 = arvore_falsa.executar(exits="0", env_extra=env_extra)

    assert r2.returncode == 0
    assert copiado.read_text(encoding="utf-8") == "<html>tentativa=2</html>"
    assert copiado.read_bytes() == arvore_falsa.relatorio.read_bytes()
    assert _tmps(arvore_falsa.tmp_path) == []
    texto = arvore_falsa.log()
    assert texto.count(f"Relatório copiado para: {copiado}") == 2
    assert "AVISO" not in texto
