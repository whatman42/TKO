#!/usr/bin/env python3
"""
GOD ENTITY — REAL-WORLD EVIDENCE-DRIVEN SELF-EVOLUTION ENGINE
Version: 2026.08 (v3.2)
Single-file core. Domain-agnostic. Environment-adaptive. Self-modifying.

MISSION:
Continuously improve capabilities, reliability, reasoning quality,
research quality, software architecture, operational performance,
and usefulness based on evidence from the real world.

FEATURES:
- Objective & Benchmark Engine
- Verification Engine (tests, static analysis)
- Evidence Provenance
- Failure Intelligence
- Security Engine
- Checkpoint & Transactional Promotion
- Change Impact Analysis
- Evolution Lineage
- Telegram Notifications
- Adaptive Research Engine:
  * ResearchPlanner
  * QueryMutationEngine
  * EvidenceQualityEngine
  * FailureClassifier
  * ProviderRegistry
  * DynamicSearchBuilder
  * SearXNG / Wikipedia / DuckDuckGo providers
- Gemini model discovery & fallback
- Validasi sintaks untuk SEMUA output LLM
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# =============================================================================
# CONSTANTS & CONFIG
# =============================================================================

VERSION = "2026.08"
AGENT_NAME = "GOD_ENTITY"
CORE_FILENAME = "self_evolving_agent.py"
GENERATION_PREFIX = "G"
DEFAULT_TIMEOUT = 300
DEFAULT_MAX_ITERATIONS = 5
DEFAULT_SUBPROCESS_TIMEOUT = 60
MEMORY_DB = ".god_entity_memory.db"
MEMORY_JSON = ".god_entity_memory.json"
CANDIDATE_DIR = ".god_entity_candidates"
LOG_DIR = ".god_entity_logs"
GIT_MODE_DEFAULT = "disabled"

PRIMARY_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
FALLBACK_MODEL = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")
AUTO_LATEST = os.environ.get("GEMINI_AUTO_LATEST", "false").lower() == "true"
MODEL_DISCOVERY = os.environ.get("MODEL_DISCOVERY", "true").lower() == "true"


class Event(str, Enum):
    ENVIRONMENT_DISCOVERED = "ENVIRONMENT_DISCOVERED"
    CAPABILITY_DISCOVERED = "CAPABILITY_DISCOVERED"
    REPOSITORY_DISCOVERED = "REPOSITORY_DISCOVERED"
    RESEARCH_STARTED = "RESEARCH_STARTED"
    RESEARCH_COMPLETED = "RESEARCH_COMPLETED"
    GOAL_RECEIVED = "GOAL_RECEIVED"
    PLAN_CREATED = "PLAN_CREATED"
    ACTION_STARTED = "ACTION_STARTED"
    ACTION_COMPLETED = "ACTION_COMPLETED"
    ACTION_FAILED = "ACTION_FAILED"
    EXPERIMENT_STARTED = "EXPERIMENT_STARTED"
    EXPERIMENT_COMPLETED = "EXPERIMENT_COMPLETED"
    EVOLUTION_STARTED = "EVOLUTION_STARTED"
    CANDIDATE_CREATED = "CANDIDATE_CREATED"
    VALIDATION_STARTED = "VALIDATION_STARTED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    VALIDATION_SUCCESS = "VALIDATION_SUCCESS"
    GENERATION_PROMOTED = "GENERATION_PROMOTED"
    GENERATION_ROLLED_BACK = "GENERATION_ROLLED_BACK"
    RECOVERY_STARTED = "RECOVERY_STARTED"
    RECOVERY_COMPLETED = "RECOVERY_COMPLETED"
    BOOT_COMPLETE = "BOOT_COMPLETE"
    LOOP_ITERATION = "LOOP_ITERATION"
    MEMORY_INITIALIZED = "MEMORY_INITIALIZED"
    SECURITY_AUDIT_COMPLETED = "SECURITY_AUDIT_COMPLETED"
    CHANGE_IMPACT_ANALYSIS = "CHANGE_IMPACT_ANALYSIS"
    BENCHMARK_COMPLETED = "BENCHMARK_COMPLETED"
    FAILURE_RECORDED = "FAILURE_RECORDED"
    CHECKPOINT_CREATED = "CHECKPOINT_CREATED"
    LINEAGE_UPDATED = "LINEAGE_UPDATED"


class GenerationStatus(str, Enum):
    ACTIVE = "ACTIVE"
    CANDIDATE = "CANDIDATE"
    FAILED = "FAILED"
    PROMOTED = "PROMOTED"
    ROLLED_BACK = "ROLLED_BACK"
    SUPERSEDED = "SUPERSEDED"


# =============================================================================
# STRUCTURED LOGGING
# =============================================================================

class StructuredLogger:
    def __init__(self, name: str = AGENT_NAME, log_dir: Optional[Path] = None):
        self.name = name
        self.log_dir = Path(log_dir or LOG_DIR)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG)
        if not self.logger.handlers:
            fmt = logging.Formatter(
                "%(asctime)s | %(levelname)-7s | %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%SZ",
            )
            ch = logging.StreamHandler(sys.stdout)
            ch.setLevel(logging.INFO)
            ch.setFormatter(fmt)
            self.logger.addHandler(ch)
            fh = logging.FileHandler(self.log_dir / "agent.log", encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(fmt)
            self.logger.addHandler(fh)

    def event(self, event: Event, **payload: Any) -> None:
        safe = {k: v for k, v in payload.items() if not self._is_sensitive(k, v)}
        msg = f"[{event.value}] {json.dumps(safe, default=str, ensure_ascii=False)}"
        self.logger.info(msg)

    def info(self, msg: str, **kwargs: Any) -> None:
        self.logger.info(msg, **kwargs)

    def warning(self, msg: str, **kwargs: Any) -> None:
        self.logger.warning(msg, **kwargs)

    def error(self, msg: str, **kwargs: Any) -> None:
        self.logger.error(msg, **kwargs)

    def debug(self, msg: str, **kwargs: Any) -> None:
        self.logger.debug(msg, **kwargs)

    @staticmethod
    def _is_sensitive(key: str, value: Any) -> bool:
        key_l = key.lower()
        sensitive_keys = (
            "key", "token", "secret", "password", "passwd", "credential",
            "cookie", "auth", "private", "api_key", "access_token",
            "bank_account", "wallet", "ktp", "phone", "email", "name",
            "creator", "identity", "contact", "telegram", "chat_id",
        )
        if any(s in key_l for s in sensitive_keys):
            return True
        if isinstance(value, str) and len(value) > 20:
            if re.search(r"(sk-|AIza|ghp_|gho_|xoxb-|Bearer\s)", value):
                return True
        return False


log = StructuredLogger()


# =============================================================================
# UTILITIES
# =============================================================================

def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def safe_run(
    cmd: List[str],
    cwd: Optional[Path] = None,
    timeout: int = DEFAULT_SUBPROCESS_TIMEOUT,
    env: Optional[Dict[str, str]] = None,
    input_text: Optional[str] = None,
) -> Tuple[int, str, str]:
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            input=input_text,
        )
        return result.returncode, result.stdout or "", result.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"
    except FileNotFoundError:
        return -2, "", "EXECUTABLE_NOT_FOUND"
    except Exception as e:
        return -3, "", str(e)


def is_secret_env(key: str) -> bool:
    key_l = key.lower()
    return any(
        s in key_l
        for s in (
            "key", "token", "secret", "password", "passwd", "credential",
            "cookie", "auth", "private", "api_key", "access_token", "ssh",
            "bank_account", "wallet", "ktp", "phone", "email", "creator",
            "telegram", "chat_id",
        )
    )


# =============================================================================
# MEMORY (SQLite preferred, JSON fallback)
# =============================================================================

class Memory:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.db_path = base_dir / MEMORY_DB
        self.json_path = base_dir / MEMORY_JSON
        self.conn: Optional[sqlite3.Connection] = None
        self.backend = "none"
        self._init()

    def _init(self) -> None:
        try:
            self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self._create_tables()
            self.backend = "sqlite"
            log.event(Event.MEMORY_INITIALIZED, backend="sqlite", path=str(self.db_path))
        except Exception as e:
            log.warning(f"SQLite unavailable ({e}), falling back to JSON")
            self.backend = "json"
            if not self.json_path.exists():
                self._write_json({})
            log.event(Event.MEMORY_INITIALIZED, backend="json", path=str(self.json_path))

    def _create_tables(self) -> None:
        assert self.conn
        cur = self.conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS kv (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event TEXT,
                payload TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS generations (
                id TEXT PRIMARY KEY,
                parent TEXT,
                timestamp TEXT,
                objective TEXT,
                source_hash TEXT,
                changes TEXT,
                test_results TEXT,
                evaluation TEXT,
                status TEXT
            );
            CREATE TABLE IF NOT EXISTS experiments (
                id TEXT PRIMARY KEY,
                hypothesis TEXT,
                change_desc TEXT,
                result TEXT,
                metrics TEXT,
                conclusion TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT,
                content TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS lineage (
                id TEXT PRIMARY KEY,
                parent TEXT,
                child TEXT,
                evidence TEXT,
                experiment TEXT,
                score REAL,
                decision TEXT,
                rollback_reason TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS evidence (
                id TEXT PRIMARY KEY,
                query TEXT,
                source TEXT,
                url TEXT,
                title TEXT,
                snippet TEXT,
                content_hash TEXT,
                evidence_type TEXT,
                confidence REAL,
                retrieved_at TEXT,
                contradiction TEXT
            );
            CREATE TABLE IF NOT EXISTS failures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT,
                context TEXT,
                error_type TEXT,
                stacktrace TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS checkpoints (
                id TEXT PRIMARY KEY,
                stage TEXT,
                generation_id TEXT,
                timestamp TEXT,
                state TEXT,
                active INTEGER DEFAULT 1
            );
            """
        )
        self.conn.commit()

    def set(self, key: str, value: Any) -> None:
        payload = json.dumps(value, default=str)
        if self.backend == "sqlite" and self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO kv (key, value, updated_at) VALUES (?, ?, ?)",
                (key, payload, utc_now()),
            )
            self.conn.commit()
        else:
            data = self._read_json()
            data[key] = {"value": value, "updated_at": utc_now()}
            self._write_json(data)

    def get(self, key: str, default: Any = None) -> Any:
        if self.backend == "sqlite" and self.conn:
            row = self.conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
            if row:
                return json.loads(row["value"])
            return default
        else:
            data = self._read_json()
            entry = data.get(key)
            if entry:
                return entry.get("value", default)
            return default

    def log_event(self, event: str, payload: Dict[str, Any]) -> None:
        if self.backend == "sqlite" and self.conn:
            self.conn.execute(
                "INSERT INTO events (event, payload, created_at) VALUES (?, ?, ?)",
                (event, json.dumps(payload, default=str), utc_now()),
            )
            self.conn.commit()

    def save_generation(self, gen: "Generation") -> None:
        if self.backend == "sqlite" and self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO generations
                (id, parent, timestamp, objective, source_hash, changes, test_results, evaluation, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    gen.id,
                    gen.parent,
                    gen.timestamp,
                    gen.objective,
                    gen.source_hash,
                    json.dumps(gen.changes, default=str),
                    json.dumps(gen.test_results, default=str),
                    json.dumps(gen.evaluation, default=str),
                    gen.status.value,
                ),
            )
            self.conn.commit()
        else:
            data = self._read_json()
            gens = data.get("generations", {})
            gen_dict = asdict(gen)
            gen_dict["status"] = gen.status.value
            gens[gen.id] = gen_dict
            data["generations"] = gens
            self._write_json(data)

    def list_generations(self) -> List[Dict[str, Any]]:
        if self.backend == "sqlite" and self.conn:
            rows = self.conn.execute(
                "SELECT * FROM generations ORDER BY timestamp DESC"
            ).fetchall()
            return [dict(r) for r in rows]
        else:
            data = self._read_json()
            return list(data.get("generations", {}).values())

    def get_active_generation(self) -> Optional[Dict[str, Any]]:
        gens = self.list_generations()
        for g in gens:
            if g.get("status") == GenerationStatus.ACTIVE.value:
                return g
        return None

    def add_observation(self, category: str, content: Any) -> None:
        if self.backend == "sqlite" and self.conn:
            self.conn.execute(
                "INSERT INTO observations (category, content, created_at) VALUES (?, ?, ?)",
                (category, json.dumps(content, default=str), utc_now()),
            )
            self.conn.commit()

    def save_lineage(self, lineage: Dict[str, Any]) -> None:
        if self.backend == "sqlite" and self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO lineage
                (id, parent, child, evidence, experiment, score, decision, rollback_reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lineage.get("id", uuid.uuid4().hex),
                    lineage.get("parent"),
                    lineage.get("child"),
                    json.dumps(lineage.get("evidence", [])),
                    lineage.get("experiment"),
                    lineage.get("score", 0.0),
                    lineage.get("decision", "unknown"),
                    lineage.get("rollback_reason", ""),
                    utc_now(),
                ),
            )
            self.conn.commit()

    def save_evidence(self, evidence: Dict[str, Any]) -> None:
        if self.backend == "sqlite" and self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO evidence
                (id, query, source, url, title, snippet, content_hash, evidence_type, confidence, retrieved_at, contradiction)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence.get("id", uuid.uuid4().hex),
                    evidence.get("query", ""),
                    evidence.get("source", ""),
                    evidence.get("url", ""),
                    evidence.get("title", ""),
                    evidence.get("snippet", ""),
                    evidence.get("content_hash", ""),
                    evidence.get("evidence_type", "UNKNOWN"),
                    evidence.get("confidence", 0.0),
                    evidence.get("retrieved_at", utc_now()),
                    evidence.get("contradiction", ""),
                ),
            )
            self.conn.commit()

    def save_failure(self, failure: Dict[str, Any]) -> None:
        if self.backend == "sqlite" and self.conn:
            self.conn.execute(
                """
                INSERT INTO failures (category, context, error_type, stacktrace, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    failure.get("category", "UNKNOWN"),
                    failure.get("context", ""),
                    failure.get("error_type", ""),
                    failure.get("stacktrace", ""),
                    utc_now(),
                ),
            )
            self.conn.commit()

    def save_checkpoint(self, checkpoint: Dict[str, Any]) -> None:
        if self.backend == "sqlite" and self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO checkpoints
                (id, stage, generation_id, timestamp, state, active)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (
                    checkpoint.get("id", uuid.uuid4().hex),
                    checkpoint.get("stage", "unknown"),
                    checkpoint.get("generation_id"),
                    utc_now(),
                    json.dumps(checkpoint.get("state", {})),
                ),
            )
            self.conn.commit()

    def get_failures(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        if self.backend == "sqlite" and self.conn:
            if category:
                rows = self.conn.execute(
                    "SELECT * FROM failures WHERE category = ? ORDER BY id DESC LIMIT 100",
                    (category,),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM failures ORDER BY id DESC LIMIT 100"
                ).fetchall()
            return [dict(r) for r in rows]
        return []

    def _read_json(self) -> Dict:
        if self.json_path.exists():
            try:
                return json.loads(self.json_path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _write_json(self, data: Dict) -> None:
        self.json_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def close(self) -> None:
        if self.conn:
            self.conn.close()


# =============================================================================
# GENERATION SYSTEM
# =============================================================================

@dataclass
class Generation:
    id: str
    parent: Optional[str]
    timestamp: str
    objective: str
    source_hash: str
    changes: List[str] = field(default_factory=list)
    test_results: Dict[str, Any] = field(default_factory=dict)
    evaluation: Dict[str, Any] = field(default_factory=dict)
    status: GenerationStatus = GenerationStatus.ACTIVE

    @staticmethod
    def next_id(existing: List[str]) -> str:
        nums = []
        for e in existing:
            m = re.match(rf"{GENERATION_PREFIX}(\d+)", e)
            if m:
                nums.append(int(m.group(1)))
        n = max(nums) + 1 if nums else 1
        return f"{GENERATION_PREFIX}{n:06d}"


# =============================================================================
# ENVIRONMENT SCANNER
# =============================================================================

class EnvironmentScanner:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.fingerprint: Dict[str, Any] = {}

    def scan(self) -> Dict[str, Any]:
        fp: Dict[str, Any] = {
            "timestamp": utc_now(),
            "os": {
                "system": platform.system(),
                "release": platform.release(),
                "version": platform.version(),
                "machine": platform.machine(),
                "processor": platform.processor(),
                "platform": platform.platform(),
            },
            "python": {
                "version": sys.version,
                "version_info": list(sys.version_info),
                "executable": sys.executable,
                "implementation": platform.python_implementation(),
            },
            "cpu": {"count": os.cpu_count()},
            "memory": self._memory_info(),
            "disk": self._disk_info(),
            "cwd": str(Path.cwd().resolve()),
            "agent_path": str(Path(__file__).resolve()) if "__file__" in globals() else None,
            "env_vars": self._scan_env_vars(),
            "executables": self._scan_executables(),
            "package_managers": self._scan_package_managers(),
            "installed_packages": self._scan_installed_packages(),
            "compilers_interpreters": self._scan_compilers(),
            "git": self._scan_git(),
            "github_cli": self._scan_gh(),
            "docker": self._scan_docker(),
            "testing_frameworks": self._scan_testing(),
            "linters_formatters": self._scan_linters(),
            "build_systems": self._scan_build(),
            "network": self._scan_network(),
            "ci_cd": self._scan_ci(),
        }
        self.fingerprint = fp
        log.event(Event.ENVIRONMENT_DISCOVERED, summary={
            "os": fp["os"]["system"],
            "python": fp["python"]["version_info"][:3],
            "cwd": fp["cwd"],
            "git": fp["git"].get("available"),
            "network": fp["network"].get("available"),
        })
        return fp

    def _memory_info(self) -> Dict[str, Any]:
        info = {}
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        info["total_kb"] = int(line.split()[1])
                    elif line.startswith("MemAvailable:"):
                        info["available_kb"] = int(line.split()[1])
        except Exception:
            pass
        return info

    def _disk_info(self) -> Dict[str, Any]:
        try:
            st = os.statvfs("/")
            return {"total": st.f_frsize * st.f_blocks, "free": st.f_frsize * st.f_bfree, "available": st.f_frsize * st.f_bavail}
        except Exception:
            return {}

    def _scan_env_vars(self) -> Dict[str, str]:
        result = {}
        for k in sorted(os.environ.keys()):
            if is_secret_env(k):
                result[k] = "AVAILABLE"
            else:
                v = os.environ[k]
                result[k] = v[:80] + "..." if len(v) > 80 else v
        return result

    def _scan_executables(self) -> Dict[str, bool]:
        candidates = [
            "git", "gh", "docker", "python", "python3", "pip", "pip3",
            "node", "npm", "npx", "yarn", "go", "rustc", "cargo",
            "java", "javac", "gcc", "g++", "clang", "make", "cmake",
            "ruby", "php", "perl", "lua", "R", "julia",
            "pytest", "unittest", "mypy", "flake8", "black", "ruff",
            "eslint", "prettier", "tsc", "mvn", "gradle",
            "curl", "wget", "jq", "yq", "terraform", "kubectl",
        ]
        found = {}
        for c in candidates:
            path = shutil.which(c)
            found[c] = path is not None
        return found

    def _scan_package_managers(self) -> Dict[str, bool]:
        return {
            "pip": shutil.which("pip") is not None or shutil.which("pip3") is not None,
            "npm": shutil.which("npm") is not None,
            "yarn": shutil.which("yarn") is not None,
            "cargo": shutil.which("cargo") is not None,
            "go": shutil.which("go") is not None,
            "apt": shutil.which("apt") is not None or shutil.which("apt-get") is not None,
            "brew": shutil.which("brew") is not None,
        }

    def _scan_installed_packages(self) -> List[str]:
        pkgs = []
        code, out, _ = safe_run([sys.executable, "-m", "pip", "list", "--format=freeze"], timeout=30)
        if code == 0:
            for line in out.splitlines():
                if "==" in line:
                    pkgs.append(line.strip())
        return pkgs[:200]

    def _scan_compilers(self) -> Dict[str, Optional[str]]:
        tools = ["python3", "node", "go", "rustc", "javac", "gcc", "g++", "clang", "ruby", "php"]
        result = {}
        for t in tools:
            path = shutil.which(t)
            if path:
                code, out, err = safe_run([path, "--version"], timeout=5)
                result[t] = (out or err).splitlines()[0] if (out or err) else path
            else:
                result[t] = None
        return result

    def _scan_git(self) -> Dict[str, Any]:
        info: Dict[str, Any] = {"available": False}
        if shutil.which("git"):
            info["available"] = True
            code, out, _ = safe_run(["git", "--version"])
            info["version"] = out.strip() if code == 0 else None
            code, out, _ = safe_run(["git", "rev-parse", "--is-inside-work-tree"], cwd=self.base_dir)
            info["in_repo"] = code == 0 and out.strip() == "true"
            if info["in_repo"]:
                for key, cmd in [
                    ("root", ["git", "rev-parse", "--show-toplevel"]),
                    ("branch", ["git", "rev-parse", "--abbrev-ref", "HEAD"]),
                    ("commit", ["git", "rev-parse", "HEAD"]),
                    ("remote", ["git", "remote", "get-url", "origin"]),
                ]:
                    c, o, _ = safe_run(cmd, cwd=self.base_dir)
                    info[key] = o.strip() if c == 0 else None
        return info

    def _scan_gh(self) -> Dict[str, Any]:
        info = {"available": False}
        if shutil.which("gh"):
            info["available"] = True
            code, out, _ = safe_run(["gh", "--version"])
            info["version"] = out.splitlines()[0] if code == 0 else None
            code, out, _ = safe_run(["gh", "auth", "status"], timeout=10)
            info["authenticated"] = code == 0
        return info

    def _scan_docker(self) -> Dict[str, Any]:
        info = {"available": False}
        if shutil.which("docker"):
            info["available"] = True
            code, out, _ = safe_run(["docker", "--version"])
            info["version"] = out.strip() if code == 0 else None
        return info

    def _scan_testing(self) -> Dict[str, bool]:
        return {
            "pytest": shutil.which("pytest") is not None,
            "unittest": True,
            "nose": False,
            "jest": shutil.which("jest") is not None or (shutil.which("npx") is not None),
            "go_test": shutil.which("go") is not None,
            "cargo_test": shutil.which("cargo") is not None,
        }

    def _scan_linters(self) -> Dict[str, bool]:
        return {
            "ruff": shutil.which("ruff") is not None,
            "flake8": shutil.which("flake8") is not None,
            "mypy": shutil.which("mypy") is not None,
            "black": shutil.which("black") is not None,
            "eslint": shutil.which("eslint") is not None,
            "prettier": shutil.which("prettier") is not None,
        }

    def _scan_build(self) -> Dict[str, bool]:
        return {
            "make": shutil.which("make") is not None,
            "cmake": shutil.which("cmake") is not None,
            "npm_scripts": (Path.cwd() / "package.json").exists(),
            "setuptools": (Path.cwd() / "setup.py").exists() or (Path.cwd() / "pyproject.toml").exists(),
            "cargo": (Path.cwd() / "Cargo.toml").exists(),
            "go_mod": (Path.cwd() / "go.mod").exists(),
            "maven": (Path.cwd() / "pom.xml").exists(),
            "gradle": (Path.cwd() / "build.gradle").exists() or (Path.cwd() / "build.gradle.kts").exists(),
        }

    def _scan_network(self) -> Dict[str, Any]:
        available = False
        urls = [
            "https://www.google.com/generate_204",
            "https://api.duckduckgo.com/?q=test&format=json",
            "https://httpbin.org/get",
        ]
        for u in urls:
            try:
                if HAS_HTTPX:
                    r = httpx.get(u, timeout=5)
                else:
                    r = requests.get(u, timeout=5)
                if r.status_code in (200, 204):
                    available = True
                    break
            except Exception:
                continue
        return {"available": available, "httpx": HAS_HTTPX, "requests": HAS_REQUESTS}

    def _scan_ci(self) -> Dict[str, Any]:
        return {
            "github_actions": os.environ.get("GITHUB_ACTIONS") == "true",
            "ci": os.environ.get("CI") == "true",
            "github_workspace": os.environ.get("GITHUB_WORKSPACE"),
            "github_repository": os.environ.get("GITHUB_REPOSITORY"),
            "github_ref": os.environ.get("GITHUB_REF"),
            "github_sha": os.environ.get("GITHUB_SHA"),
            "runner_os": os.environ.get("RUNNER_OS"),
        }


# =============================================================================
# REPOSITORY SCANNER & MODEL
# =============================================================================

class RepositoryScanner:
    EXTENSION_MAP = {
        ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "typescript",
        ".jsx": "javascript", ".java": "java", ".c": "c", ".cpp": "cpp", ".cc": "cpp",
        ".h": "c_header", ".hpp": "cpp_header", ".cs": "csharp", ".go": "go",
        ".rs": "rust", ".rb": "ruby", ".php": "php", ".kt": "kotlin", ".swift": "swift",
        ".dart": "dart", ".r": "r", ".jl": "julia", ".lua": "lua", ".sh": "shell",
        ".bash": "shell", ".zsh": "shell", ".ps1": "powershell", ".sql": "sql",
        ".html": "html", ".htm": "html", ".css": "css", ".scss": "scss",
        ".yaml": "yaml", ".yml": "yaml", ".json": "json", ".toml": "toml",
        ".xml": "xml", ".md": "markdown", ".rst": "rst", ".txt": "text",
        ".dockerfile": "dockerfile", "Dockerfile": "dockerfile",
        ".tf": "terraform", ".hcl": "hcl", ".makefile": "makefile", "Makefile": "makefile",
        ".ipynb": "jupyter", ".vue": "vue", ".svelte": "svelte",
    }

    IGNORE_DIRS = {
        ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
        ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
        ".god_entity_candidates", ".god_entity_logs", ".tox", "target",
        "vendor", ".idea", ".vscode",
    }

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.model: Dict[str, Any] = {}

    def scan(self) -> Dict[str, Any]:
        files: List[Dict[str, Any]] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in self.IGNORE_DIRS and not d.startswith(".")]
            for fn in filenames:
                if fn.startswith(".") and fn not in (".gitignore", ".env.example", ".dockerignore"):
                    continue
                full = Path(dirpath) / fn
                try:
                    rel = full.relative_to(self.root)
                    info = self._analyze_file(full, rel)
                    files.append(info)
                except Exception:
                    continue

        by_lang: Dict[str, List[str]] = {}
        for f in files:
            lang = f.get("language") or "unknown"
            by_lang.setdefault(lang, []).append(f["path"])

        entrypoints = self._find_entrypoints(files)
        configs = [f for f in files if f.get("category") == "config"]
        tests = [f for f in files if f.get("category") == "test"]
        workflows = [f for f in files if "github/workflows" in f["path"].replace("\\", "/")]
        docs = [f for f in files if f.get("language") in ("markdown", "rst")]

        self.model = {
            "root": str(self.root),
            "file_count": len(files),
            "files": files[:500],
            "languages": {k: len(v) for k, v in by_lang.items()},
            "entrypoints": entrypoints,
            "configs": [c["path"] for c in configs],
            "tests": [t["path"] for t in tests],
            "workflows": [w["path"] for w in workflows],
            "documentation": [d["path"] for d in docs],
            "has_git": (self.root / ".git").exists(),
            "dependency_files": self._find_dependency_files(),
        }
        log.event(Event.REPOSITORY_DISCOVERED, summary={
            "root": str(self.root),
            "files": len(files),
            "languages": self.model["languages"],
            "entrypoints": entrypoints,
        })
        return self.model

    def _analyze_file(self, full: Path, rel: Path) -> Dict[str, Any]:
        ext = full.suffix.lower()
        name = full.name
        language = self.EXTENSION_MAP.get(ext) or self.EXTENSION_MAP.get(name)
        if not language:
            language = self._detect_by_content(full)
        category = self._categorize(rel, language)
        size = 0
        try:
            size = full.stat().st_size
        except Exception:
            pass
        return {"path": str(rel).replace("\\", "/"), "language": language, "category": category, "size": size, "extension": ext}

    def _detect_by_content(self, path: Path) -> Optional[str]:
        try:
            with open(path, "rb") as f:
                head = f.read(512)
            text = head.decode("utf-8", errors="ignore")
            if text.startswith("#!"):
                if "python" in text:
                    return "python"
                if "bash" in text or "sh" in text:
                    return "shell"
                if "node" in text:
                    return "javascript"
            if "FROM " in text and "Dockerfile" in path.name:
                return "dockerfile"
        except Exception:
            pass
        return "unknown"

    def _categorize(self, rel: Path, language: Optional[str]) -> str:
        p = str(rel).replace("\\", "/").lower()
        if "test" in p or p.startswith("tests/") or p.endswith("_test.py") or p.endswith(".test.js"):
            return "test"
        if p.startswith(".github/workflows") or p.endswith(".yml") and "workflow" in p:
            return "workflow"
        if language in ("yaml", "json", "toml", "xml") or p.endswith(("requirements.txt", "pyproject.toml", "package.json", "Cargo.toml", "go.mod", "pom.xml")):
            return "config"
        if language in ("markdown", "rst"):
            return "documentation"
        if language in ("dockerfile",) or "Dockerfile" in rel.name:
            return "infrastructure"
        if language == "shell" or p.endswith((".sh", ".bash")):
            return "script"
        return "source"

    def _find_entrypoints(self, files: List[Dict]) -> List[str]:
        candidates = []
        for f in files:
            p = f["path"]
            name = Path(p).name
            if name in ("main.py", "app.py", "manage.py", "index.js", "main.go", "main.rs", "index.ts", "server.py", "cli.py"):
                candidates.append(p)
            if name == CORE_FILENAME:
                candidates.append(p)
        return candidates

    def _find_dependency_files(self) -> List[str]:
        names = ["requirements.txt", "pyproject.toml", "setup.py", "Pipfile", "package.json", "package-lock.json", "yarn.lock", "Cargo.toml", "go.mod", "go.sum", "pom.xml", "build.gradle", "Gemfile", "composer.json"]
        found = []
        for n in names:
            if (self.root / n).exists():
                found.append(n)
        return found


# =============================================================================
# CAPABILITY DISCOVERY
# =============================================================================

class CapabilityRegistry:
    def __init__(self, env: Dict[str, Any], repo: Dict[str, Any]):
        self.env = env
        self.repo = repo
        self.capabilities: Dict[str, Any] = {}

    def discover(self) -> Dict[str, Any]:
        caps = {
            "filesystem_read": True,
            "filesystem_write": self._can_write(),
            "subprocess": True,
            "network": self.env.get("network", {}).get("available", False),
            "git": self.env.get("git", {}).get("available", False),
            "git_in_repo": self.env.get("git", {}).get("in_repo", False),
            "github_cli": self.env.get("github_cli", {}).get("available", False),
            "github_authenticated": self.env.get("github_cli", {}).get("authenticated", False),
            "docker": self.env.get("docker", {}).get("available", False),
            "python_package_install": self.env.get("package_managers", {}).get("pip", False),
            "model_provider": self._detect_model_providers(),
            "validators": self._detect_validators(),
            "research": self.env.get("network", {}).get("available", False),
            "research_functional": False,
            "self_modify": True,
            "ci": self.env.get("ci_cd", {}).get("github_actions", False) or self.env.get("ci_cd", {}).get("ci", False),
        }
        self.capabilities = caps
        log.event(Event.CAPABILITY_DISCOVERED, capabilities={k: v for k, v in caps.items() if not isinstance(v, (list, dict))})
        return caps

    def _can_write(self) -> bool:
        try:
            test = self.repo.get("root") or "."
            p = Path(test) / f".god_write_test_{uuid.uuid4().hex[:8]}"
            p.write_text("test")
            p.unlink()
            return True
        except Exception:
            return False

    def _detect_model_providers(self) -> Dict[str, bool]:
        return {
            "gemini": bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")),
            "openai": bool(os.environ.get("OPENAI_API_KEY")),
            "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "grok": bool(os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY")),
            "ollama": False,
        }

    def _detect_validators(self) -> Dict[str, bool]:
        return {
            "py_compile": True,
            "ast_parse": True,
            "import_check": True,
            "pytest": self.env.get("testing_frameworks", {}).get("pytest", False),
            "unittest": True,
            "ruff": self.env.get("linters_formatters", {}).get("ruff", False),
            "mypy": self.env.get("linters_formatters", {}).get("mypy", False),
            "black": self.env.get("linters_formatters", {}).get("black", False),
            "git_status": self.env.get("git", {}).get("available", False),
        }


# =============================================================================
# MODEL PROVIDER ABSTRACTION
# =============================================================================

class ModelProvider(ABC):
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def available(self) -> bool:
        ...

    @abstractmethod
    def generate(self, prompt: str, system: Optional[str] = None, max_tokens: int = 2048) -> str:
        ...


class NullProvider(ModelProvider):
    def name(self) -> str:
        return "null"

    def available(self) -> bool:
        return True

    def generate(self, prompt: str, system: Optional[str] = None, max_tokens: int = 2048) -> str:
        return "[NULL_PROVIDER] No LLM credential configured."


class GeminiProvider(ModelProvider):
    def __init__(self):
        self.api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.primary_model = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
        self.fallback_model = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")
        self.auto_latest = os.environ.get("GEMINI_AUTO_LATEST", "false").lower() == "true"
        self.model_discovery = os.environ.get("MODEL_DISCOVERY", "true").lower() == "true"
        self.available_models: List[str] = []
        self.active_model: Optional[str] = None

    def name(self) -> str:
        return "gemini"

    def available(self) -> bool:
        return bool(self.api_key) and (HAS_HTTPX or HAS_REQUESTS)

    def _discover_models(self) -> None:
        if not self.model_discovery or not self.available():
            return
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={self.api_key}"
        try:
            if HAS_HTTPX:
                r = httpx.get(url, timeout=10)
            else:
                r = requests.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                self.available_models = [m.get("name", "").split("/")[-1] for m in data.get("models", [])]
                log.info(f"🧠 [GEMINI] Discovered {len(self.available_models)} models")
        except Exception as e:
            log.warning(f"⚠️ Model discovery failed: {e}")

    def _choose_model(self) -> str:
        candidates = []
        if self.auto_latest:
            candidates.extend(["gemini-flash-latest", self.primary_model, self.fallback_model])
        else:
            candidates.extend([self.primary_model, self.fallback_model])
        if self.model_discovery and self.available_models:
            for c in candidates:
                if any(c in m or m.startswith(c.split("-")[0]) for m in self.available_models):
                    return c
        return candidates[0]

    def _generate_with_model(self, model: str, prompt: str, system: Optional[str], max_tokens: int) -> Tuple[Optional[str], Dict[str, Any]]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"
        generation_config = {"maxOutputTokens": max_tokens}
        body = {
            "contents": [{"parts": [{"text": (system + "\n\n" + prompt) if system else prompt}]}],
            "generationConfig": generation_config,
        }
        start = time.time()
        try:
            if HAS_HTTPX:
                r = httpx.post(url, json=body, timeout=60)
            else:
                r = requests.post(url, json=body, timeout=60)
            elapsed = time.time() - start
            audit = {"requested_model": model, "latency_ms": int(elapsed * 1000), "error_type": None, "success": r.status_code == 200, "status_code": r.status_code}
            if r.status_code != 200:
                audit["error_type"] = f"HTTP_{r.status_code}"
                return None, audit
            data = r.json()
            candidates = data.get("candidates", [])
            if not candidates:
                audit["error_type"] = "EMPTY_CANDIDATES"
                return None, audit
            parts = candidates[0].get("content", {}).get("parts", [])
            text = parts[0].get("text", "") if parts else ""
            audit["token_usage"] = data.get("usageMetadata", {})
            return text, audit
        except Exception as e:
            elapsed = time.time() - start
            return None, {"requested_model": model, "latency_ms": int(elapsed * 1000), "error_type": type(e).__name__, "success": False}

    def generate(self, prompt: str, system: Optional[str] = None, max_tokens: int = 2048) -> str:
        if not self.available():
            return NullProvider().generate(prompt, system, max_tokens)
        if self.model_discovery and not self.available_models:
            self._discover_models()
        chosen_model = self._choose_model()
        text, audit = self._generate_with_model(chosen_model, prompt, system, max_tokens)
        if text is not None:
            audit["resolved_model"] = chosen_model
            audit["fallback_trigger"] = False
            log.info(f"✅ [GEMINI] Success with {chosen_model}")
            return text
        fallback_candidates = [self.primary_model, self.fallback_model] if self.auto_latest else [self.fallback_model]
        for fb_model in fallback_candidates:
            if fb_model == chosen_model:
                continue
            fb_text, fb_audit = self._generate_with_model(fb_model, prompt, system, max_tokens)
            if fb_text is not None:
                log.info(f"✅ [GEMINI] Fallback success with {fb_model}")
                return fb_text
        log.error(f"❌ [GEMINI] All models failed")
        return "[GEMINI_ERROR] all models failed"


class OpenAIProvider(ModelProvider):
    def __init__(self):
        self.api_key = os.environ.get("OPENAI_API_KEY")
        self.model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self.base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")

    def name(self) -> str:
        return "openai"

    def available(self) -> bool:
        return bool(self.api_key) and (HAS_HTTPX or HAS_REQUESTS)

    def generate(self, prompt: str, system: Optional[str] = None, max_tokens: int = 2048) -> str:
        if not self.available():
            return NullProvider().generate(prompt, system, max_tokens)
        url = f"{self.base}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.3}
        try:
            if HAS_HTTPX:
                r = httpx.post(url, json=body, headers=headers, timeout=60)
            else:
                r = requests.post(url, json=body, headers=headers, timeout=60)
            if r.status_code != 200:
                return f"[OPENAI_ERROR] status={r.status_code}"
            data = r.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            return f"[OPENAI_EXCEPTION] {type(e).__name__}"


class GrokProvider(ModelProvider):
    def __init__(self):
        self.api_key = os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY")
        self.model = os.environ.get("GROK_MODEL", "grok-3")
        self.base = "https://api.x.ai/v1"

    def name(self) -> str:
        return "grok"

    def available(self) -> bool:
        return bool(self.api_key) and (HAS_HTTPX or HAS_REQUESTS)

    def generate(self, prompt: str, system: Optional[str] = None, max_tokens: int = 2048) -> str:
        if not self.available():
            return NullProvider().generate(prompt, system, max_tokens)
        url = f"{self.base}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.3}
        try:
            if HAS_HTTPX:
                r = httpx.post(url, json=body, headers=headers, timeout=60)
            else:
                r = requests.post(url, json=body, headers=headers, timeout=60)
            if r.status_code != 200:
                return f"[GROK_ERROR] status={r.status_code}"
            data = r.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            return f"[GROK_EXCEPTION] {type(e).__name__}"


def select_model_provider() -> ModelProvider:
    for cls in (GrokProvider, GeminiProvider, OpenAIProvider):
        p = cls()
        if p.available():
            log.info(f"Selected model provider: {p.name()}")
            return p
    log.warning("No LLM provider credentials found. Using NullProvider.")
    return NullProvider()


# =============================================================================
# ADAPTIVE RESEARCH ENGINE
# =============================================================================

class ResearchPlanner:
    """Memecah query menjadi sub-query yang lebih terarah."""
    @staticmethod
    def plan(query: str) -> List[str]:
        q = query.strip().lower()
        sub_queries = [q]
        if len(q.split()) > 8:
            words = re.findall(r'[a-zA-Z0-9_\-\.]+', q)
            freq = {}
            for w in words:
                if len(w) > 2:
                    freq[w] = freq.get(w, 0) + 1
            sorted_kw = sorted(freq.items(), key=lambda x: x[1], reverse=True)[:6]
            sub_queries.append(" ".join(kw for kw, _ in sorted_kw))
        sub_queries.extend(QueryMutationEngine.mutate(q))
        seen = set()
        unique = []
        for sq in sub_queries:
            if sq not in seen:
                seen.add(sq)
                unique.append(sq)
        return unique[:5]


class QueryMutationEngine:
    """Menghasilkan variasi query."""
    @staticmethod
    def mutate(query: str) -> List[str]:
        mutations = []
        q = query.strip()
        mutations.append(" ".join(q.split()[:5]))
        stopwords = {"the", "a", "an", "is", "are", "was", "were", "for", "and", "or", "of", "to", "in", "on", "with", "without", "how", "what", "why", "best", "practices", "autonomous", "agent", "architecture"}
        words = [w for w in q.split() if w.lower() not in stopwords]
        if words:
            mutations.append(" ".join(words))
        if "github" in q.lower() or "action" in q.lower():
            mutations.append(q + " GITHUB_TOKEN permission")
        if "error" in q.lower():
            mutations.append(q + " exact error message")
        if "github" in q.lower():
            mutations.append("site:github.com " + q)
        if "stackoverflow" in q.lower() or "error" in q.lower():
            mutations.append("site:stackoverflow.com " + q)
        return mutations


class EvidenceQualityEngine:
    """Menilai kualitas evidence."""
    @staticmethod
    def score(result: Dict[str, Any], query: str) -> float:
        score = 0.5
        source = result.get("source", "").lower()
        url = result.get("url", "").lower()
        title = result.get("title", "").lower()
        snippet = result.get("snippet", "").lower()
        if any(s in source + url for s in ["github.com", "wikipedia.org", "arxiv.org", "stackoverflow.com", "docs."]):
            score += 0.3
        elif "blog" in source or "forum" in source:
            score -= 0.1
        query_words = set(re.findall(r'\w+', query.lower()))
        title_words = set(re.findall(r'\w+', title))
        snippet_words = set(re.findall(r'\w+', snippet))
        overlap = len(query_words & title_words) + len(query_words & snippet_words)
        if overlap > 2:
            score += 0.2
        elif overlap == 0:
            score -= 0.2
        retrieved = result.get("retrieved_at", "")
        if retrieved:
            try:
                dt = datetime.fromisoformat(retrieved)
                age_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
                if age_hours < 24:
                    score += 0.1
                elif age_hours > 720:
                    score -= 0.1
            except Exception:
                pass
        return max(0.0, min(1.0, score))


class FailureClassifier:
    """Klasifikasi kegagalan research."""
    @staticmethod
    def classify(error: Exception, http_status: Optional[int] = None) -> str:
        if isinstance(error, subprocess.TimeoutExpired) or "timeout" in str(error).lower():
            return "TIMEOUT"
        if http_status == 429:
            return "RATE_LIMIT"
        if http_status == 403:
            return "ROBOTS_BLOCK"
        if http_status == 401:
            return "AUTH_REQUIRED"
        if "connection" in str(error).lower() or "network" in str(error).lower():
            return "NETWORK_FAILURE"
        if "dns" in str(error).lower():
            return "DNS_FAILURE"
        if "parse" in str(error).lower() or "json" in str(error).lower():
            return "PARSER_FAILURE"
        return "UNKNOWN"


class ProviderRegistry:
    """Menyimpan statistik provider."""
    def __init__(self, memory: Memory):
        self.memory = memory
        self.key = "provider_stats"

    def update(self, provider: str, success: bool, latency: float, error: Optional[str] = None):
        stats = self.memory.get(self.key, {})
        p = stats.get(provider, {"success": 0, "fail": 0, "latency_sum": 0.0, "latency_count": 0, "last_error": None})
        if success:
            p["success"] += 1
        else:
            p["fail"] += 1
        p["latency_sum"] += latency
        p["latency_count"] += 1
        p["last_error"] = error
        stats[provider] = p
        self.memory.set(self.key, stats)

    def get_best(self) -> Optional[str]:
        stats = self.memory.get(self.key, {})
        if not stats:
            return None
        best = None
        best_rate = -1.0
        for name, p in stats.items():
            total = p["success"] + p["fail"]
            if total == 0:
                continue
            rate = p["success"] / total
            if rate > best_rate:
                best_rate = rate
                best = name
        return best


class DynamicSearchBuilder:
    """Meminta LLM membuat fungsi pencarian baru jika semua provider gagal."""
    def __init__(self, model_provider: ModelProvider):
        self.model_provider = model_provider

    def build(self, query: str) -> Optional[str]:
        if not self.model_provider or not self.model_provider.available():
            return None
        prompt = f"""
All standard search providers failed. Generate a Python function that searches for:
"{query}"

The function must:
- Use only public APIs or public endpoints.
- Return a list of dicts with keys 'title', 'url', 'snippet', 'source'.
- Be safe: no network access to localhost, no file system writes.
- Be self-contained and use only stdlib + requests/httpx.

Return ONLY the Python code for the function, no explanation.
"""
        code = self.model_provider.generate(prompt, max_tokens=1500)
        code = code.strip()
        if code.startswith("```python"):
            code = code[9:]
        if code.endswith("```"):
            code = code[:-3]
        try:
            ast.parse(code)
            return code
        except SyntaxError:
            return None


class AdaptiveResearchEngine:
    """Research engine adaptif dengan fallback, query mutation, dan dynamic provider."""

    def __init__(self, network_available: bool, memory: Memory, model_provider: Optional[ModelProvider] = None):
        self.network = network_available
        self.memory = memory
        self.model_provider = model_provider
        self.registry = ProviderRegistry(memory)
        self.searxng_instances = [
            u.strip().rstrip("/")
            for u in os.environ.get("SEARXNG_INSTANCES", "").split(",")
            if u.strip()
        ]
        self.functional = False
        self.last_error: Optional[str] = None

    def research(self, query: str, max_results: int = 5) -> List[Dict[str, Any]]:
        log.event(Event.RESEARCH_STARTED, query=query)
        if not self.network:
            self.functional = False
            self.last_error = "NO_NETWORK"
            return []

        sub_queries = ResearchPlanner.plan(query)
        all_results: List[Dict[str, Any]] = []

        for sq in sub_queries:
            results = self._try_standard_providers(sq, max_results)
            if results:
                all_results.extend(results)
                break

        if not all_results:
            best_provider = self.registry.get_best()
            for sq in sub_queries:
                results = self._try_provider_by_name(best_provider, sq, max_results) if best_provider else []
                if results:
                    all_results.extend(results)
                    break

        if not all_results and self.model_provider:
            builder = DynamicSearchBuilder(self.model_provider)
            code = builder.build(query)
            if code:
                results = self._execute_dynamic_provider(code, query, max_results)
                if results:
                    all_results.extend(results)

        all_results = self._deduplicate(all_results, max_results)
        scored = []
        for r in all_results:
            r["evidence_score"] = EvidenceQualityEngine.score(r, query)
            scored.append(r)

        scored.sort(key=lambda x: x.get("evidence_score", 0), reverse=True)
        final_results = scored[:max_results]

        self.functional = len(final_results) > 0
        self.last_error = None if self.functional else "NO_RESULTS_AFTER_ALL_STRATEGIES"

        log.event(Event.RESEARCH_COMPLETED, query=query, results=len(final_results), functional=self.functional)
        return final_results

    def _try_standard_providers(self, query: str, max_results: int) -> List[Dict[str, Any]]:
        for instance in self.searxng_instances:
            start = time.time()
            try:
                results = self._search_searxng(instance, query, max_results)
                latency = time.time() - start
                self.registry.update("searxng", True, latency)
                return results
            except Exception as e:
                latency = time.time() - start
                self.registry.update("searxng", False, latency, str(e))
                continue
        start = time.time()
        try:
            results = self._search_wikipedia(query, max_results)
            latency = time.time() - start
            self.registry.update("wikipedia", True, latency)
            return results
        except Exception as e:
            latency = time.time() - start
            self.registry.update("wikipedia", False, latency, str(e))
        start = time.time()
        try:
            results = self._search_ddg_html(query, max_results)
            latency = time.time() - start
            self.registry.update("ddg_html", True, latency)
            return results
        except Exception as e:
            latency = time.time() - start
            self.registry.update("ddg_html", False, latency, str(e))
        return []

    def _try_provider_by_name(self, provider_name: Optional[str], query: str, max_results: int) -> List[Dict[str, Any]]:
        if provider_name == "searxng":
            for instance in self.searxng_instances:
                try:
                    return self._search_searxng(instance, query, max_results)
                except Exception:
                    continue
        elif provider_name == "wikipedia":
            try:
                return self._search_wikipedia(query, max_results)
            except Exception:
                pass
        elif provider_name == "ddg_html":
            try:
                return self._search_ddg_html(query, max_results)
            except Exception:
                pass
        return []

    def _execute_dynamic_provider(self, code: str, query: str, max_results: int) -> List[Dict[str, Any]]:
        try:
            namespace: Dict[str, Any] = {"requests": requests, "httpx": httpx, "json": json, "re": re}
            exec(code, namespace)
            for name, obj in namespace.items():
                if callable(obj) and name not in ("requests", "httpx", "json", "re"):
                    try:
                        result = obj(query)
                        if isinstance(result, list):
                            normalized = []
                            for item in result:
                                if isinstance(item, dict) and "url" in item:
                                    normalized.append({
                                        "title": item.get("title", ""),
                                        "url": item.get("url", ""),
                                        "snippet": item.get("snippet", ""),
                                        "source": item.get("source", "dynamic"),
                                        "retrieved_at": utc_now(),
                                    })
                            return normalized[:max_results]
                    except Exception:
                        continue
        except Exception as e:
            log.warning(f"Dynamic provider execution failed: {e}")
        return []

    def _search_searxng(self, instance, query, max_results):
        url = f"{instance}/search"
        params = {"q": query, "format": "json", "categories": "general", "pageno": 1}
        if HAS_REQUESTS:
            r = requests.get(url, params=params, timeout=10)
            if r.status_code != 200:
                return []
            data = r.json()
        else:
            r = httpx.get(url, params=params, timeout=10)
            if r.status_code != 200:
                return []
            data = r.json()
        results = []
        for item in data.get("results", [])[:max_results]:
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", "") or item.get("description", ""),
                "source": "searxng",
                "engine": item.get("engine", "unknown"),
                "retrieved_at": utc_now(),
            })
        return results

    def _search_wikipedia(self, query, max_results):
        url = "https://en.wikipedia.org/w/api.php"
        params = {"action": "query", "list": "search", "srsearch": query, "format": "json", "utf8": 1, "srlimit": max_results}
        if HAS_REQUESTS:
            r = requests.get(url, params=params, timeout=10)
            data = r.json()
        else:
            r = httpx.get(url, params=params, timeout=10)
            data = r.json()
        results = []
        for item in data.get("query", {}).get("search", []):
            page_url = f"https://en.wikipedia.org/wiki/{item['title'].replace(' ', '_')}"
            results.append({
                "title": item["title"],
                "url": page_url,
                "snippet": item.get("snippet", ""),
                "source": "wikipedia",
                "retrieved_at": utc_now(),
            })
        return results

    def _search_ddg_html(self, query, max_results):
        url = "https://html.duckduckgo.com/html/"
        if HAS_REQUESTS:
            r = requests.post(url, data={"q": query}, timeout=10)
            if r.status_code != 200:
                return []
            html = r.text
        else:
            r = httpx.post(url, data={"q": query}, timeout=10)
            if r.status_code != 200:
                return []
            html = r.text
        results = []
        pattern = re.compile(r'<a rel="nofollow" class="result__a" href="([^"]+)">(.*?)</a>.*?<a class="result__snippet".*?>(.*?)</a>', re.DOTALL | re.IGNORECASE)
        for match in pattern.finditer(html)[:max_results]:
            raw_url = match.group(1)
            title = re.sub('<.*?>', '', match.group(2)).strip()
            snippet = re.sub('<.*?>', '', match.group(3)).strip()
            if raw_url and title:
                results.append({
                    "title": title,
                    "url": raw_url,
                    "snippet": snippet,
                    "source": "duckduckgo_html",
                    "retrieved_at": utc_now(),
                })
        return results

    def _deduplicate(self, results, max_results):
        seen = set()
        unique = []
        for r in results:
            url = r.get("url", "")
            if url and url in seen:
                continue
            if url:
                seen.add(url)
            unique.append(r)
            if len(unique) >= max_results:
                break
        return unique


# =============================================================================
# SECURITY ENGINE
# =============================================================================

class SecurityEngine:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir

    def audit(self) -> Dict[str, Any]:
        findings = []
        secret_patterns = [
            (r'sk-[A-Za-z0-9]{20,}', 'potential_api_key'),
            (r'AIza[0-9A-Za-z\-_]{35}', 'google_api_key'),
            (r'ghp_[A-Za-z0-9]{36}', 'github_pat'),
            (r'xoxb-[0-9]{10,}-[A-Za-z0-9]+', 'slack_token'),
        ]
        for dirpath, dirnames, filenames in os.walk(self.base_dir):
            dirnames[:] = [d for d in dirnames if d not in RepositoryScanner.IGNORE_DIRS and not d.startswith(".")]
            for fn in filenames:
                if not fn.endswith(('.py', '.json', '.yaml', '.yml', '.toml', '.txt', '.env')):
                    continue
                full = Path(dirpath) / fn
                try:
                    text = full.read_text(encoding='utf-8', errors='ignore')
                except Exception:
                    continue
                for pattern, name in secret_patterns:
                    for m in re.finditer(pattern, text):
                        findings.append({
                            "file": str(full.relative_to(self.base_dir)),
                            "type": name,
                            "line": text.count('\n', 0, m.start()) + 1,
                        })
        log.event(Event.SECURITY_AUDIT_COMPLETED, findings=len(findings))
        return {"findings": findings, "clean": len(findings) == 0}


# =============================================================================
# CHANGE IMPACT ANALYZER
# =============================================================================

class ChangeImpactAnalyzer:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir

    def build_import_graph(self) -> Dict[str, List[str]]:
        graph = {}
        for py_file in self.base_dir.rglob("*.py"):
            if ".git" in py_file.parts or "__pycache__" in py_file.parts:
                continue
            try:
                tree = ast.parse(py_file.read_text(encoding='utf-8'))
            except Exception:
                continue
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imports.append(node.module)
            rel = str(py_file.relative_to(self.base_dir))
            graph[rel] = sorted(set(imports))
        return graph

    def analyze(self, target_files: List[str]) -> Dict[str, Any]:
        graph = self.build_import_graph()
        impacted = set(target_files)
        for _ in range(3):
            for src, imports in graph.items():
                for imp in imports:
                    if any(t in imp for t in target_files):
                        impacted.add(src)
        log.event(Event.CHANGE_IMPACT_ANALYSIS, impacted=list(impacted)[:10])
        return {"target_files": target_files, "impacted_files": sorted(impacted), "graph_size": len(graph)}


# =============================================================================
# VERIFICATION ENGINE
# =============================================================================

class VerificationEngine:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir

    def verify(self, candidate_path: Path) -> Dict[str, Any]:
        results = {"checks": {}}
        try:
            source = candidate_path.read_text(encoding='utf-8')
            ast.parse(source)
            results["checks"]["ast_parse"] = {"ok": True}
        except SyntaxError as e:
            results["checks"]["ast_parse"] = {"ok": False, "error": str(e)}
            results["overall"] = False
            return results

        code, _, err = safe_run([sys.executable, "-m", "py_compile", str(candidate_path)])
        results["checks"]["py_compile"] = {"ok": code == 0, "stderr": err[:300] if err else ""}

        code, out, err = safe_run([sys.executable, str(candidate_path), "--test-boot"], timeout=45, cwd=self.base_dir)
        results["checks"]["boot_test"] = {"ok": code == 0, "stdout_tail": out[-500:], "stderr_tail": err[-300:]}

        if shutil.which("pytest"):
            code, out, _ = safe_run(["pytest", "-q"], timeout=60, cwd=self.base_dir)
            if code in (0, 5):
                results["checks"]["pytest"] = {"ok": True, "output": out[-500:]}
            else:
                results["checks"]["pytest"] = {"ok": False, "output": out[-500:]}
        else:
            results["checks"]["pytest"] = {"ok": True, "output": "pytest not available"}

        results["overall"] = all(c.get("ok", False) for c in results["checks"].values()) if results["checks"] else True
        log.event(Event.VALIDATION_SUCCESS if results["overall"] else Event.VALIDATION_FAILED, path=str(candidate_path))
        return results


# =============================================================================
# OBJECTIVE / BENCHMARK ENGINE
# =============================================================================

class BenchmarkEngine:
    def compute_score(self, verification_results: Dict[str, Any]) -> float:
        score = 0.0
        checks = verification_results.get("checks", {})
        if checks.get("ast_parse", {}).get("ok"):
            score += 30.0
        if checks.get("py_compile", {}).get("ok"):
            score += 30.0
        if checks.get("boot_test", {}).get("ok"):
            score += 40.0
        if checks.get("pytest", {}).get("ok"):
            score += 20.0
        return score

    def is_improvement(self, baseline_score: float, candidate_score: float, threshold: float = 5.0) -> bool:
        return candidate_score >= baseline_score + threshold


# =============================================================================
# FAILURE INTELLIGENCE
# =============================================================================

class FailureIntelligence:
    def __init__(self, memory: Memory):
        self.memory = memory

    def record_failure(self, category: str, context: str, error_type: str, stacktrace: str = "") -> None:
        self.memory.save_failure({
            "category": category,
            "context": context,
            "error_type": error_type,
            "stacktrace": stacktrace,
        })
        log.event(Event.FAILURE_RECORDED, category=category, error_type=error_type)

    def has_failed_before(self, category: str, context_key: str) -> bool:
        failures = self.memory.get_failures(category)
        return any(context_key in f.get("context", "") for f in failures)


# =============================================================================
# CHECKPOINT MANAGER
# =============================================================================

class CheckpointManager:
    def __init__(self, memory: Memory):
        self.memory = memory

    def create(self, stage: str, generation_id: str, state: Dict[str, Any]) -> None:
        self.memory.save_checkpoint({
            "id": uuid.uuid4().hex,
            "stage": stage,
            "generation_id": generation_id,
            "state": state,
        })
        log.event(Event.CHECKPOINT_CREATED, stage=stage, generation=generation_id)

    def latest(self) -> Optional[Dict[str, Any]]:
        if self.memory.backend == "sqlite" and self.memory.conn:
            row = self.memory.conn.execute("SELECT * FROM checkpoints ORDER BY timestamp DESC LIMIT 1").fetchone()
            if row:
                return dict(row)
        return None


# =============================================================================
# TELEGRAM NOTIFIER
# =============================================================================

class TelegramNotifier:
    def __init__(self):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        self.enabled = bool(self.token and self.chat_id)

    def send(self, message: str) -> bool:
        if not self.enabled:
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": message[:4000]}
        try:
            if HAS_REQUESTS:
                r = requests.post(url, json=payload, timeout=10)
                return r.status_code == 200
            else:
                r = httpx.post(url, json=payload, timeout=10)
                return r.status_code == 200
        except Exception:
            return False


# =============================================================================
# EVOLUTION ENGINE (integrated)
# =============================================================================

class EvolutionEngine:
    def __init__(
        self,
        core_path: Path,
        memory: Memory,
        validator: VerificationEngine,
        git: 'GitManager',
        base_dir: Path,
        model_provider: Optional[ModelProvider] = None,
        research_engine: Optional[AdaptiveResearchEngine] = None,
        benchmark: BenchmarkEngine = None,
        failure_intel: FailureIntelligence = None,
        checkpoint_mgr: CheckpointManager = None,
        impact_analyzer: ChangeImpactAnalyzer = None,
    ):
        self.core_path = core_path
        self.memory = memory
        self.validator = validator
        self.git = git
        self.base_dir = base_dir
        self.model_provider = model_provider
        self.research_engine = research_engine
        self.benchmark = benchmark or BenchmarkEngine()
        self.failure_intel = failure_intel or FailureIntelligence(memory)
        self.checkpoint_mgr = checkpoint_mgr or CheckpointManager(memory)
        self.impact_analyzer = impact_analyzer or ChangeImpactAnalyzer(base_dir)
        self.candidate_dir = base_dir / CANDIDATE_DIR
        self.candidate_dir.mkdir(parents=True, exist_ok=True)

    def create_candidate(self, objective: str, modifications: Optional[str] = None) -> Tuple[str, Path]:
        log.event(Event.EVOLUTION_STARTED, objective=objective)
        existing = [g.get("id", "") for g in self.memory.list_generations()]
        new_id = Generation.next_id(existing)
        candidate_path = self.candidate_dir / f"candidate_generation_{new_id}.py"

        shutil.copy2(self.core_path, candidate_path)
        log.event(Event.CANDIDATE_CREATED, generation=new_id, path=str(candidate_path))

        changes_applied = ["duplicate_core"]

        research_context = ""
        if self.research_engine and self.research_engine.network:
            research_results = self.research_engine.research(objective, max_results=3)
            research_context = "\n".join(f"- {r.get('title', '')}: {r.get('snippet', '')[:200]}" for r in research_results)

        enhancement_code = self._generate_enhancement(objective, research_context)
        if enhancement_code:
            with open(candidate_path, "a", encoding="utf-8") as f:
                f.write("\n\n# ==== AUTONOMOUS ENHANCEMENT ====\n")
                f.write(enhancement_code)
                f.write("\n# ==== END ENHANCEMENT ====\n")
            changes_applied.append("llm_enhancement")

        workspace_changes = self._apply_llm_changes(objective, research_context)
        if workspace_changes:
            changes_applied.extend(workspace_changes)

        if modifications:
            with open(candidate_path, "a", encoding="utf-8") as f:
                f.write(f"\n\n# GENERATION {new_id} | parent lineage | objective: {objective[:80]}\n")
            changes_applied.append("lineage_marker")

        gen = Generation(
            id=new_id,
            parent=self.memory.get_active_generation().get("id") if self.memory.get_active_generation() else None,
            timestamp=utc_now(),
            objective=objective,
            source_hash=sha256_file(candidate_path),
            changes=changes_applied,
            status=GenerationStatus.CANDIDATE,
        )
        self.memory.save_generation(gen)
        return new_id, candidate_path

    def _generate_enhancement(self, objective: str, research_context: str) -> str:
        if not self.model_provider or not self.model_provider.available():
            return ""
        prompt = f"""
You are an expert Python developer. Objective: {objective}

Research context:
{research_context}

Current agent code (first 3000 chars):

Generate a standalone Python function/class that adds meaningful value.
Return ONLY Python code, no explanation, no markdown fences.
"""
        response = self.model_provider.generate(prompt, max_tokens=2000)
        code = response.strip()
        if code.startswith("```python"):
            code = code[9:]
        if code.endswith("```"):
            code = code[:-3]
        code = code.strip()
        try:
            ast.parse(code)
            return code
        except SyntaxError:
            self.failure_intel.record_failure(
                category="LLM_CODE_GENERATION",
                context=objective,
                error_type="SYNTAX_ERROR",
            )
            return ""

    def _apply_llm_changes(self, objective: str, research_context: str) -> List[str]:
        if not self.model_provider or not self.model_provider.available():
            return []
        prompt = f"""
You are an autonomous developer. Objective: {objective}

You may create new Python modules or modify existing ones in the repository (except .git, .god_entity_candidates, and backups).
Return a JSON object with optional arrays "create" and "modify".
Each "create" item: {{"path": "relative/path.py", "content": "python code"}}
Each "modify" item: {{"path": "relative/path.py", "search": "substring to find", "replace": "new content"}}

Return ONLY JSON, no markdown.
Research context:
{research_context}
"""
        response = self.model_provider.generate(prompt, max_tokens=3000)
        try:
            data = json.loads(response)
        except json.JSONDecodeError:
            log.warning("⚠️ LLM changes response is not valid JSON")
            self.failure_intel.record_failure(
                category="LLM_JSON_PARSE",
                context=objective,
                error_type="INVALID_JSON",
            )
            return []

        changes = []
        for create_item in data.get("create", []):
            path = (self.base_dir / create_item["path"]).resolve()
            if not self._is_safe_path(path):
                log.warning(f"⚠️ Unsafe path rejected: {create_item['path']}")
                continue
            content = create_item.get("content", "")
            try:
                ast.parse(content)
            except SyntaxError as e:
                log.warning(f"⚠️ LLM generated invalid Python for {create_item['path']} — skipped")
                self.failure_intel.record_failure(
                    category="LLM_CODE_GENERATION",
                    context=create_item["path"],
                    error_type="SYNTAX_ERROR",
                    stacktrace=str(e),
                )
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            changes.append(f"created:{create_item['path']}")
            log.info(f"✅ Created module: {create_item['path']}")

        for mod_item in data.get("modify", []):
            path = (self.base_dir / mod_item["path"]).resolve()
            if not self._is_safe_path(path) or not path.exists():
                log.warning(f"⚠️ Modify target not found or unsafe: {mod_item['path']}")
                continue
            text = path.read_text(encoding="utf-8")
            new_text = text.replace(mod_item.get("search", ""), mod_item.get("replace", ""))
            try:
                ast.parse(new_text)
            except SyntaxError as e:
                log.warning(f"⚠️ Modified file {mod_item['path']} would have syntax error — skipped")
                self.failure_intel.record_failure(
                    category="LLM_CODE_MODIFICATION",
                    context=mod_item["path"],
                    error_type="SYNTAX_ERROR",
                    stacktrace=str(e),
                )
                continue
            if new_text != text:
                path.write_text(new_text, encoding="utf-8")
                changes.append(f"modified:{mod_item['path']}")
                log.info(f"✅ Modified module: {mod_item['path']}")

        return changes

    def _is_safe_path(self, path: Path) -> bool:
        try:
            path.relative_to(self.base_dir)
        except ValueError:
            return False
        forbidden = {".git", ".god_entity_candidates", ".god_entity_logs", "__pycache__"}
        return not any(part in forbidden for part in path.parts)

    def promote(self, gen_id: str, candidate_path: Path, test_results: Dict[str, Any]) -> bool:
        if not test_results.get("overall"):
            self.memory.save_failure({"category": "VALIDATION", "context": gen_id, "error_type": "validation_failed"})
            return False

        backup = self.core_path.with_suffix(".py.bak")
        self.checkpoint_mgr.create("pre_promote", gen_id, {"backup": str(backup)})

        try:
            shutil.copy2(self.core_path, backup)
            shutil.copy2(candidate_path, self.core_path)

            self._deactivate_previous_generations(except_id=gen_id)
            gen_obj = Generation(
                id=gen_id,
                parent=None,
                timestamp=utc_now(),
                objective="promoted",
                source_hash=sha256_file(self.core_path),
                changes=["promoted_from_candidate"],
                test_results=test_results,
                evaluation={"promoted": True, "backup": str(backup)},
                status=GenerationStatus.ACTIVE,
            )
            self.memory.save_generation(gen_obj)

            parent = self.memory.get_active_generation() or {}
            self.memory.save_lineage({
                "id": f"lineage-{gen_id}",
                "parent": parent.get("id") if parent else None,
                "child": gen_id,
                "evidence": [],
                "experiment": None,
                "score": test_results.get("score", 0.0),
                "decision": "ADOPT",
            })

            candidate_path.unlink(missing_ok=True)
            log.event(Event.GENERATION_PROMOTED, generation=gen_id, hash=gen_obj.source_hash)
            self.git.commit_if_enabled(f"GOD_ENTITY: promote {gen_id}")
            return True
        except Exception as e:
            log.error(f"Promotion failed: {e}")
            if backup.exists():
                shutil.copy2(backup, self.core_path)
            log.event(Event.GENERATION_ROLLED_BACK, generation=gen_id, reason=str(e))
            self.memory.save_failure({"category": "PROMOTION", "context": gen_id, "error_type": str(e)})
            return False

    def _deactivate_previous_generations(self, except_id: str) -> None:
        for g in self.memory.list_generations():
            if g.get("id") != except_id and g.get("status") == GenerationStatus.ACTIVE.value:
                gen_obj = Generation(
                    id=g["id"], parent=g.get("parent"), timestamp=g.get("timestamp", utc_now()),
                    objective=g.get("objective", ""), source_hash=g.get("source_hash", ""),
                    changes=json.loads(g["changes"]) if isinstance(g.get("changes"), str) else g.get("changes", []),
                    test_results=json.loads(g["test_results"]) if isinstance(g.get("test_results"), str) else {},
                    evaluation=json.loads(g["evaluation"]) if isinstance(g.get("evaluation"), str) else {},
                    status=GenerationStatus.SUPERSEDED,
                )
                self.memory.save_generation(gen_obj)

    def rollback(self, gen_id: Optional[str] = None) -> bool:
        backup = self.core_path.with_suffix(".py.bak")
        if backup.exists():
            shutil.copy2(backup, self.core_path)
            log.event(Event.GENERATION_ROLLED_BACK, generation=gen_id or "last", source="backup")
            return True
        return False


# =============================================================================
# GIT MANAGER
# =============================================================================

class GitManager:
    def __init__(self, root: Path, mode: str = GIT_MODE_DEFAULT):
        self.root = root
        self.mode = mode
        self.available = shutil.which("git") is not None

    def status(self):
        if not self.available:
            return {"available": False}
        code, out, _ = safe_run(["git", "status", "--porcelain"], cwd=self.root)
        return {"available": True, "clean": code == 0 and not out.strip(), "porcelain": out, "mode": self.mode}

    def commit_if_enabled(self, message: str) -> bool:
        if self.mode == "disabled" or not self.available:
            return False
        safe_run(["git", "add", "-A"], cwd=self.root)
        code, _, err = safe_run(["git", "commit", "-m", message], cwd=self.root)
        if code != 0:
            log.warning(f"Git commit failed: {err[:200]}")
            return False
        if self.mode == "push":
            code, _, err = safe_run(["git", "push"], cwd=self.root, timeout=30)
            if code != 0:
                log.warning(f"Git push failed: {err[:200]}")
                return False
        return True


# =============================================================================
# RECOVERY
# =============================================================================

class RecoveryManager:
    def __init__(self, memory: Memory, evolution: EvolutionEngine, core_path: Path):
        self.memory = memory
        self.evolution = evolution
        self.core_path = core_path

    def recover_if_needed(self) -> Dict[str, Any]:
        log.event(Event.RECOVERY_STARTED)
        active = self.memory.get_active_generation()
        if not active:
            source_hash = sha256_file(self.core_path) if self.core_path.exists() else "unknown"
            init = Generation(id="G000001", parent=None, timestamp=utc_now(), objective="initial_boot", source_hash=source_hash, status=GenerationStatus.ACTIVE)
            self.memory.save_generation(init)
            log.event(Event.RECOVERY_COMPLETED, action="created_initial_generation")
        else:
            log.event(Event.RECOVERY_COMPLETED, action="active_generation_present")
        return {"active": (active or {}).get("id")}


# =============================================================================
# AGENT CORE
# =============================================================================

class GodEntity:
    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = Path(base_dir or Path.cwd()).resolve()
        self.core_path = Path(__file__).resolve() if "__file__" in globals() else self.base_dir / CORE_FILENAME
        self.memory = Memory(self.base_dir)
        self.env_scanner = EnvironmentScanner(self.base_dir)
        self.repo_scanner = RepositoryScanner(self.base_dir)
        self.env: Dict[str, Any] = {}
        self.repo: Dict[str, Any] = {}
        self.capabilities: Dict[str, Any] = {}
        self.provider: ModelProvider = NullProvider()
        self.research: AdaptiveResearchEngine = AdaptiveResearchEngine(False, self.memory, self.provider)
        self.validator: VerificationEngine = VerificationEngine(self.base_dir)
        self.git: GitManager = GitManager(self.base_dir)
        self.benchmark = BenchmarkEngine()
        self.failure_intel = FailureIntelligence(self.memory)
        self.checkpoint_mgr = CheckpointManager(self.memory)
        self.impact_analyzer = ChangeImpactAnalyzer(self.base_dir)
        self.evolution: Optional[EvolutionEngine] = None
        self.recovery: Optional[RecoveryManager] = None
        self.telegram = TelegramNotifier()
        self.goal: Optional[str] = None
        self.creator: Dict[str, str] = {}
        self.config = {
            "timeout": int(os.environ.get("GOD_TIMEOUT", DEFAULT_TIMEOUT)),
            "max_iterations": int(os.environ.get("GOD_MAX_ITERATIONS", DEFAULT_MAX_ITERATIONS)),
            "subprocess_timeout": int(os.environ.get("GOD_SUBPROCESS_TIMEOUT", DEFAULT_SUBPROCESS_TIMEOUT)),
            "git_mode": os.environ.get("GIT_MODE", GIT_MODE_DEFAULT),
            "dry_run": False,
        }
        self._booted = False

    def _load_creator(self):
        self.creator = {
            "identity": os.environ.get("CREATOR_IDENTITY", "").strip(),
            "telegram": os.environ.get("CREATOR_TELEGRAM", "").strip(),
            "phone": os.environ.get("CREATOR_PHONE", "").strip(),
            "email": os.environ.get("CREATOR_EMAIL", "").strip(),
        }
        if any(self.creator.values()):
            log.info("👤 [CREATOR] Identitas kreator dimuat.")

    def boot(self):
        self.env = self.env_scanner.scan()
        self.repo = self.repo_scanner.scan()
        reg = CapabilityRegistry(self.env, self.repo)
        self.capabilities = reg.discover()
        self.provider = select_model_provider()
        self.research = AdaptiveResearchEngine(
            self.capabilities.get("network", False),
            self.memory,
            self.provider
        )
        self._load_creator()
        if not self.goal:
            self.goal = "Continuously improve capabilities, reliability, and evidence-driven evolution."
        self.memory.set("current_goal", self.goal)
        self.evolution = EvolutionEngine(
            self.core_path, self.memory, self.validator, self.git, self.base_dir,
            model_provider=self.provider, research_engine=self.research,
            benchmark=self.benchmark, failure_intel=self.failure_intel,
            checkpoint_mgr=self.checkpoint_mgr, impact_analyzer=self.impact_analyzer,
        )
        self.recovery = RecoveryManager(self.memory, self.evolution, self.core_path)
        self.recovery.recover_if_needed()
        self._booted = True
        log.event(Event.BOOT_COMPLETE, provider=self.provider.name(), active_gen=(self.memory.get_active_generation() or {}).get("id"))
        self.telegram.send(f"🚀 GOD ENTITY booted. Provider={self.provider.name()}")
        return {"booted": True}

    def observe(self):
        if not self._booted:
            self.boot()
        return {
            "environment": {"os": self.env.get("os", {}).get("system"), "python": self.env.get("python", {}).get("version_info"), "cwd": self.env.get("cwd")},
            "repository": {"files": self.repo.get("file_count"), "languages": self.repo.get("languages"), "entrypoints": self.repo.get("entrypoints")},
            "capabilities": {k: v for k, v in self.capabilities.items() if not isinstance(v, dict)},
            "active_generation": self.memory.get_active_generation(),
            "goal": self.goal,
        }

    def discover(self):
        self.env = self.env_scanner.scan()
        self.repo = self.repo_scanner.scan()
        reg = CapabilityRegistry(self.env, self.repo)
        self.capabilities = reg.discover()
        return self.capabilities

    def research_query(self, query):
        return self.research.research(query)

    def plan(self, goal):
        plan = {
            "goal": goal,
            "tasks": ["Observe", "Research", "Hypothesis", "Candidate", "Verify", "Benchmark", "Promote/Rollback"],
            "evaluation_criteria": ["syntax", "boot", "tests", "benchmark"],
        }
        self.memory.set("last_plan", plan)
        return plan

    def evolve_once(self):
        objective = self.goal or "self_improvement"
        gid, path = self.evolution.create_candidate(objective, modifications="lineage_marker")
        verification = self.validator.verify(path)
        verification["score"] = self.benchmark.compute_score(verification)
        baseline_score = self.benchmark.compute_score(self.validator.verify(self.core_path))
        improved = self.benchmark.is_improvement(baseline_score, verification["score"])
        verification["improved"] = improved
        if verification["overall"] and improved and not self.config["dry_run"]:
            self.evolution.promote(gid, path, verification)
            self.telegram.send(f"✅ Evolusi {gid} dipromosikan. Skor={verification['score']}")
        else:
            self.telegram.send(f"⚠️ Evolusi {gid} ditolak. Skor={verification['score']}, improved={improved}")
        return {"generation": gid, "verification": verification}

    def run_loop(self, max_iterations=None):
        if not self._booted:
            self.boot()
        max_iter = max_iterations or self.config["max_iterations"]
        if max_iter <= 0:
            max_iter = float("inf")
        start = time.time()
        history = []
        i = 0
        while True:
            if max_iter != float("inf") and i >= max_iter:
                break
            if self.config["timeout"] and self.config["timeout"] > 0 and time.time() - start > self.config["timeout"]:
                break
            log.event(Event.LOOP_ITERATION, iteration=i)
            self.observe()
            self.discover()
            if self.capabilities.get("research") and i == 0:
                self.research_query("best practices for autonomous agent architecture")
            self.plan(self.goal)
            if i % 3 == 0:
                evo = self.evolve_once()
                history.append({"iteration": i, "evolution": evo})
            else:
                core_verification = self.validator.verify(self.core_path)
                history.append({"iteration": i, "core_validation": core_verification})
            i += 1
        return {"iterations": len(history), "history": history, "elapsed_sec": round(time.time() - start, 2)}


# =============================================================================
# CLI
# =============================================================================

def build_parser():
    p = argparse.ArgumentParser(description="GOD ENTITY — REAL-WORLD EVIDENCE-DRIVEN SELF-EVOLUTION ENGINE")
    p.add_argument("--goal", type=str)
    p.add_argument("--observe", action="store_true")
    p.add_argument("--discover", action="store_true")
    p.add_argument("--research", type=str)
    p.add_argument("--run", action="store_true")
    p.add_argument("--evolve", action="store_true")
    p.add_argument("--validate", action="store_true")
    p.add_argument("--status", action="store_true")
    p.add_argument("--generation", action="store_true")
    p.add_argument("--rollback", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--test-boot", action="store_true")
    p.add_argument("--max-iterations", type=int)
    p.add_argument("--timeout", type=int)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    agent = GodEntity()
    if args.dry_run:
        agent.config["dry_run"] = True
    if args.timeout:
        agent.config["timeout"] = args.timeout
    if args.max_iterations:
        agent.config["max_iterations"] = args.max_iterations
    if args.test_boot:
        report = agent.validator.verify(agent.core_path)
        print(json.dumps(report, indent=2))
        return 0 if report.get("overall") else 1
    agent.boot()
    if args.goal:
        agent.goal = args.goal
        agent.memory.set("current_goal", args.goal)
    if args.observe:
        print(json.dumps(agent.observe(), indent=2))
        return 0
    if args.discover:
        print(json.dumps(agent.discover(), indent=2))
        return 0
    if args.research:
        print(json.dumps(agent.research_query(args.research), indent=2))
        return 0
    if args.status:
        print(json.dumps({"version": VERSION, "provider": agent.provider.name(), "active_generation": agent.memory.get_active_generation()}, indent=2))
        return 0
    if args.generation:
        print(json.dumps(agent.memory.list_generations(), indent=2))
        return 0
    if args.validate:
        res = agent.validator.verify(agent.core_path)
        print(json.dumps(res, indent=2))
        return 0 if res.get("overall") else 1
    if args.rollback:
        ok = agent.evolution.rollback()
        print(json.dumps({"rollback": ok}))
        return 0 if ok else 1
    if args.evolve:
        print(json.dumps(agent.evolve_once(), indent=2))
        return 0
    if args.run:
        report = agent.run_loop()
        print(json.dumps(report, indent=2))
        return 0
    print(json.dumps(agent.observe(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())