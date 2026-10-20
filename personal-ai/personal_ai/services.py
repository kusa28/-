"""User LaunchAgents for local Ollama and the web interface."""

import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import shutil

LABELS = {"ollama": "local.personal-ai.ollama", "web": "local.personal-ai.web"}


def deploy_runtime():
    """Run LaunchAgents outside protected Documents folders."""
    source = Path(__file__).resolve().parent.parent
    target = Path.home() / ".local/share/personal-ai/runtime"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source / "personal_ai", target / "personal_ai", dirs_exist_ok=True)
    if (source / "native").is_dir():
        shutil.copytree(source / "native", target / "native", dirs_exist_ok=True)
    if not (target / ".venv/bin/python").exists():
        if not (source / ".venv/bin/python").exists():
            raise FileNotFoundError("先にプロジェクトの .venv を作成してください")
        shutil.copytree(source / ".venv", target / ".venv", symlinks=True)
    return target


def disable_ollama_cloud():
    settings = Path.home() / ".ollama/server.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(settings.read_text()) if settings.exists() else {}
    data["disable_ollama_cloud"] = True
    settings.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    settings.chmod(0o600)


def install(name):
    if name not in LABELS:
        raise ValueError("service は ollama または web")
    logs = Path.home() / ".local/state/personal-ai"
    logs.mkdir(parents=True, exist_ok=True)
    logs.chmod(0o700)
    root = Path(__file__).resolve().parent.parent
    if name == "ollama":
        binary = Path.home() / ".local/bin/ollama"
        if not binary.exists():
            raise FileNotFoundError("Ollama CLIが見つかりません")
        disable_ollama_cloud()
        args = [str(binary), "serve"]
        cwd = str(Path.home())
        env = {"OLLAMA_HOST": "127.0.0.1:11434", "OLLAMA_NO_CLOUD": "1"}
    else:
        runtime = deploy_runtime()
        args = [str(runtime / ".venv/bin/python"), "-m", "personal_ai", "serve"]
        cwd = str(runtime)
        env = {"PATH": f"{Path.home()}/.local/bin:/opt/homebrew/bin:/usr/bin:/bin"}
    label = LABELS[name]
    plist = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
    plist.parent.mkdir(parents=True, exist_ok=True)
    spec = {"Label": label, "ProgramArguments": args, "WorkingDirectory": cwd,
            "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 10,
            "EnvironmentVariables": env,
            "StandardOutPath": str(logs / f"{name}.log"),
            "StandardErrorPath": str(logs / f"{name}-error.log")}
    with open(plist, "wb") as file:
        plistlib.dump(spec, file)
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(plist)], capture_output=True)
    result = subprocess.run(["launchctl", "bootstrap", domain, str(plist)], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"LaunchAgent起動失敗: {result.stderr.strip()}")
    return plist


def remove(name):
    if name not in LABELS:
        raise ValueError("service は ollama または web")
    plist = Path.home() / "Library/LaunchAgents" / f"{LABELS[name]}.plist"
    if plist.exists():
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist)], capture_output=True)
        plist.unlink()
    return plist


def main():
    parser = argparse.ArgumentParser(description="個人向けAIのローカル常駐サービス")
    parser.add_argument("action", choices=["install", "remove"])
    parser.add_argument("service", choices=LABELS)
    args = parser.parse_args()
    try:
        print(install(args.service) if args.action == "install" else remove(args.service))
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"エラー: {exc}\n")


if __name__ == "__main__":
    main()
