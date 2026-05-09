"""Language detection, compilation, and run commands for multi-language bot support."""
import subprocess
from pathlib import Path

LANGUAGES = {
    "python": {
        "detect": ["MyBot.py"],
        "compile": None,
        "run": "python3 MyBot.py",
        "starter_dir": "python",
    },
    "java": {
        "detect": ["MyBot.java"],
        "compile": "javac *.java",
        "run": "java MyBot",
        "starter_dir": "java",
    },
    "cpp": {
        "detect": ["MyBot.cc", "MyBot.cpp"],
        "compile": "g++ -O2 -o MyBot *.cc *.cpp 2>/dev/null || g++ -O2 -o MyBot *.cc 2>/dev/null || g++ -O2 -o MyBot *.cpp",
        "run": "./MyBot",
        "starter_dir": "cpp",
    },
    "go": {
        "detect": ["MyBot.go"],
        "compile": "go build -o MyBot .",
        "run": "./MyBot",
        "starter_dir": "go",
    },
    "javascript": {
        "detect": ["MyBot.js"],
        "compile": None,
        "run": "node MyBot.js",
        "starter_dir": "javascript",
    },
    "ruby": {
        "detect": ["MyBot.rb"],
        "compile": None,
        "run": "ruby MyBot.rb",
        "starter_dir": "ruby",
    },
}


def detect_language(bot_dir: Path) -> str | None:
    """Detect bot language from files present in the directory."""
    for lang, config in LANGUAGES.items():
        for filename in config["detect"]:
            if (bot_dir / filename).exists():
                return lang
    return None


def compile_bot(bot_dir: Path, language: str) -> tuple[bool, str]:
    """Compile a bot if needed. Returns (success, error_message)."""
    config = LANGUAGES[language]
    if not config["compile"]:
        return True, ""
    try:
        result = subprocess.run(
            config["compile"], shell=True, capture_output=True, text=True,
            timeout=60, cwd=str(bot_dir)
        )
        if result.returncode != 0:
            return False, result.stderr[:500]
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "Compilation timed out"


def get_run_command(bot_dir: Path, language: str) -> str:
    """Get the full run command for a bot."""
    config = LANGUAGES[language]
    return f"cd {bot_dir} && {config['run']}"


def get_starter_files_dir(language: str, ants_dir: Path) -> Path | None:
    """Get the path to starter bot files for a language."""
    config = LANGUAGES[language]
    starter = ants_dir / "dist" / "starter_bots" / config["starter_dir"]
    return starter if starter.exists() else None
