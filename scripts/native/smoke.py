"""Model-free smoke checks against only the newly launched packaged server."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


def isolated_environment(home):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("OLLAMA_", "GGML_", "MLX_", "DYLD_", "LD_"))}
    # Do not let an SDK/compiler installation hide missing packaged DLLs.
    if os.name == "nt":
        system = Path(os.environ["SystemRoot"])
        env["PATH"] = os.pathsep.join(map(str, [system / "System32", system]))
    else:
        env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    env.update(HOME=str(home), USERPROFILE=str(home),
               OLLAMA_MODELS=str(home / "models"), OLLAMA_HOST="127.0.0.1:0",
               OLLAMA_NO_CLOUD="1", OLLAMA_NOPRUNE="1", OLLAMA_DEBUG="1")
    return env


def listening_address(log):
    # This message is printed only after net.Listen succeeds, and includes the
    # OS-assigned port. Never probe a reserved/released port or the default server.
    match = re.search(r"Listening on (127\.0\.0\.1:[1-9][0-9]*) \(version ([^)]+)\)", log)
    return match.groups() if match else None


def validate_mlx_result(result):
    output = result.stdout + result.stderr
    if (result.returncode != 1 or "MLX engine initialized" not in output
            or "Error: file does not exist" not in output.splitlines()):
        raise RuntimeError(f"MLX initialization smoke failed: {output}")


def smoke(executable, version, log_path):
    executable = executable.resolve(strict=True)
    runtime = executable.parent / "lib/ollama"
    suffix = ".exe" if os.name == "nt" else ""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ollama-smoke-") as temp:
        home = Path(temp)
        env = isolated_environment(home)
        for name, args in [("llama-server", ["--version"]),
                           ("llama-server", ["--list-devices"])]:
            subprocess.run([str(runtime / (name + suffix)), *args], env=env,
                           cwd=home, check=True, timeout=90)
        quantize = subprocess.run([str(runtime / ("llama-quantize" + suffix)), "--help"],
                                  env=env, cwd=home, capture_output=True, text=True, timeout=30)
        # Upstream b11963's quantize usage() exits 1, including for --help.
        if quantize.returncode not in (0, 1) or "usage:" not in quantize.stdout.lower():
            raise RuntimeError(f"Quantize helper failed: {quantize.stdout}\n{quantize.stderr}")
        if (runtime / "mlx_metal_v3").is_dir():
            # The runner initializes MLX before opening a local manifest. A
            # deliberately absent model exercises dlopen without pulling weights.
            mlx = subprocess.run(
                [str(executable), "runner", "--model", "native-smoke-missing:latest"],
                env={**env, "OLLAMA_LLM_LIBRARY": "mlx_metal_v3"}, cwd=home,
                capture_output=True, text=True, timeout=90
            )
            output = mlx.stdout + mlx.stderr
            log_path.with_name("mlx-smoke.log").write_text(output)
            validate_mlx_result(mlx)
        with log_path.open("w") as log:
            process = subprocess.Popen([str(executable), "serve"], cwd=home,
                                       env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 120
                address = None
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(f"Packaged server exited: {log_path.read_text()}")
                    address = listening_address(log_path.read_text())
                    if address:
                        break
                    time.sleep(0.2)
                if not address or address[1] != version:
                    raise RuntimeError(f"No matching new listener: {log_path.read_text()}")
                base = f"http://{address[0]}"
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                while True:
                    try:
                        with opener.open(base + "/api/version", timeout=5) as response:
                            result = json.load(response)
                        break
                    except (urllib.error.URLError, TimeoutError):
                        if process.poll() is not None or time.monotonic() >= deadline:
                            raise
                        time.sleep(0.2)
                if result != {"version": version}:
                    raise RuntimeError(f"Unexpected version response: {result}")
                with opener.open(base + "/api/tags", timeout=10) as response:
                    models = json.load(response)
                if models != {"models": []}:
                    raise RuntimeError(f"Smoke home is not isolated: {models}")
                subprocess.run([str(executable), "--version"], env={**env, "OLLAMA_HOST": base},
                               cwd=home, check=True, timeout=30)
                if process.poll() is not None:
                    raise RuntimeError("New server died during smoke checks")
                print(f"PASS: {executable.name}, {base}, version={version}, empty model store")
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--log", type=Path, default=Path(".build/smoke.log"))
    args = parser.parse_args()
    smoke(args.executable, args.version, args.log.resolve())
