#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
  Hermes-Revo — Ralph Loop Engine v3.1
  "Load. Execute. Revolve. Repeat."
═══════════════════════════════════════════════════════════════

  A background loop engine for autonomous task execution.
  Uses LLM (Hermes or OpenAI-compatible) to implement
  coding tasks stage by stage. No cron job needed.

  Supported providers:
    - hermes  : Hermes Agent / OpenCode Go
    - openai  : OpenAI-compatible (OpenAI, Ollama, Groq,
                Together, vLLM, DeepSeek, OpenRouter, etc.)
═══════════════════════════════════════════════════════════════
"""

import json
import os
import re
import sys
import time
import base64
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None

from openai import OpenAI


# =============================================================================
# DEFAULT CONFIG (overridden by revo.yaml)
# =============================================================================

DEFAULT_CONFIG = {
    "ai": {
        "provider": "hermes",
        "hermes": {
            "base_url": "https://opencode.ai/zen/go/v1",
            "default_model": "deepseek-v4-flash",
        },
        "openai": {
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o",
        },
    },
    "loop": {
        "interval_seconds": 5,
        "max_retries": 3,
        "llm_timeout": 120,
    },
    "files": {
        "ban_modify_existing": True,
        "preview_lines": 60,
        "max_new_file_bytes": 50000,
    },
    "logging": {
        "dir": ".revo",
        "log_file": "revo.log",
    },
}


# =============================================================================
# SYSTEM PROMPTS
# =============================================================================

SYSTEM_CREATE = """
You are Revo, an autonomous coding engine.

## RULES
1. Output ONLY valid JSON. FIRST char = {, LAST char = }.
2. No markdown, no code fences, no commentary outside the JSON.
3. Use project conventions from the context below.
4. Write clean, production-quality code with proper error handling.
5. For PHP files: start with <?php, use require_once for helpers.

## OUTPUT FORMAT
{"file": "path/to/file.ext", "content": "// complete file content here", "explanation": "...", "done": true}
"""

SYSTEM_PATCH = """
You are Revo, an autonomous coding engine performing targeted edits.

## RULES
1. Output ONLY valid JSON. FIRST char = {, LAST char = }.
2. No markdown, no code fences.
3. NEVER rewrite entire existing files. Only output targeted patches.
4. "old" strings MUST be exact matches from the file shown.
5. Include 2-3 surrounding lines of context for uniqueness.
6. NEVER refactor, restructure, or change file organization.
7. Only add the specific feature requested.

## OUTPUT FORMAT (for PATCHING existing files)
{"patches": [{"file": "path/to/file.ext", "operations": [{"type": "replace", "old": "exact existing lines", "new": "new replacement lines"}]}], "explanation": "...", "done": true}

## OUTPUT FORMAT (for APPENDING to existing files)
{"files": {"path/to/file.ext": {"action": "append", "content": "/* new content */"}}, "explanation": "...", "done": true}
"""


# =============================================================================
# CONFIG LOADER
# =============================================================================

def load_config() -> dict:
    """Load revo.yaml config, merged with defaults."""
    config = dict(DEFAULT_CONFIG)

    # Try revo.yaml in current directory
    for path in ["revo.yaml", "revo.yml"]:
        p = Path(path)
        if p.exists():
            try:
                raw = p.read_text()
                if yaml:
                    data = yaml.safe_load(raw)
                else:
                    import json as _json
                    # Fallback: parse YAML-like with simple key extraction
                    data = {}
                    for line in raw.splitlines():
                        if ":" in line and not line.strip().startswith("#"):
                            key, val = line.split(":", 1)
                            data[key.strip()] = val.strip().strip('"')
                deep_merge(config, data or {})
                logging.info(f"Loaded config from {p}")
            except Exception as e:
                logging.warning(f"Failed to load {p}: {e}")
            break

    return config


def deep_merge(base: dict, override: dict) -> None:
    """Merge override into base recursively."""
    for key, val in override.items():
        if isinstance(val, dict) and key in base and isinstance(base[key], dict):
            deep_merge(base[key], val)
        else:
            base[key] = val


# =============================================================================
# API KEY RESOLVER
# =============================================================================

def resolve_api_key(config: dict) -> str:
    """
    Resolve API key based on the active provider.
    
    For 'hermes': checks .env (project root) for HERMES_API_KEY / LLM_API_KEY /
                  OPENCODE_GO_API_KEY / OPENAI_API_KEY, then Hermes' own .env
                  (~/AppData/Local/hermes/.env on Windows), then env vars.
    For 'openai': checks .env (project root) for OPENAI_API_KEY, then env vars.
    """
    provider = config.get("ai", {}).get("provider", "hermes")

    if provider == "openai":
        return _resolve_openai_key()

    return _resolve_hermes_key()


def _resolve_hermes_key() -> str:
    """Resolve Hermes API key from .env files and env vars."""
    env_paths = [
        Path(".env"),
        Path(os.path.expanduser("~/AppData/Local/hermes/.env")),
        Path(os.path.expanduser("~/.hermes/.env")),
        Path(os.path.expanduser("~/hermes/.env")),
    ]

    for env_path in env_paths:
        if env_path.exists():
            try:
                for line in env_path.read_text().splitlines():
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key in ("HERMES_API_KEY", "LLM_API_KEY",
                               "OPENCODE_GO_API_KEY", "OPENAI_API_KEY"):
                        if val and val != "***":
                            return val
            except Exception:
                pass

    # Fallback to env vars
    for env_var in ("HERMES_API_KEY", "LLM_API_KEY",
                    "OPENCODE_GO_API_KEY", "OPENAI_API_KEY"):
        val = os.environ.get(env_var, "")
        if val and val != "***":
            return val

    logging.error(
        "No API key found for provider 'hermes'!\n"
        "  Set HERMES_API_KEY in .env, or use Hermes Agent's .env file."
    )
    sys.exit(1)


def _resolve_openai_key() -> str:
    """Resolve OpenAI-compatible API key from .env and env vars."""
    env_paths = [
        Path(".env"),
    ]

    for env_path in env_paths:
        if env_path.exists():
            try:
                for line in env_path.read_text().splitlines():
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key == "OPENAI_API_KEY":
                        if val and val != "***":
                            return val
            except Exception:
                pass

    val = os.environ.get("OPENAI_API_KEY", "")
    if val and val != "***":
        return val

    logging.error(
        "No API key found for provider 'openai'!\n"
        "  Set OPENAI_API_KEY in .env"
    )
    sys.exit(1)


# =============================================================================
# LLM CLIENT
# =============================================================================

class LLMClient:
    """OpenAI-compatible LLM client with retries + cost tracking."""

    def __init__(self, config: dict):
        self.provider = config.get("ai", {}).get("provider", "hermes")
        self.max_retries = config["loop"]["max_retries"]
        self.timeout = config["loop"]["llm_timeout"]
        self.total_cost = 0.0
        self.total_input = 0
        self.total_output = 0

        if self.provider == "openai":
            ai_cfg = config.get("ai", {}).get("openai", {})
            self.base_url = ai_cfg.get("base_url",
                "https://api.openai.com/v1")
            self.model = ai_cfg.get("model", "gpt-4o")
        else:
            ai_cfg = config.get("ai", {}).get("hermes", {})
            self.base_url = ai_cfg.get("base_url",
                "https://opencode.ai/zen/go/v1")
            self.model = ai_cfg.get("default_model",
                "deepseek-v4-flash")

        self.api_key = resolve_api_key(config)
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
        )

    def call(self, system_prompt: str, user_prompt: str,
             temperature: float = 0.2, max_tokens: int = 16384) -> Optional[str]:
        """Call LLM with retries."""
        for attempt in range(self.max_retries):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                content = resp.choices[0].message.content.strip()
                usage = resp.usage

                cost = (
                    usage.prompt_tokens * 0.15
                    + usage.completion_tokens * 0.60
                ) / 1_000_000
                self.total_cost += cost
                self.total_input += usage.prompt_tokens
                self.total_output += usage.completion_tokens

                logging.info(
                    f"  {self.model}: {usage.prompt_tokens} in "
                    f"/ {usage.completion_tokens} out / ${cost:.4f}"
                )
                return content

            except Exception as e:
                logging.warning(f"  LLM error (attempt {attempt + 1}): {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)

        return None

    def summary(self) -> str:
        return (
            f"Total: {self.total_input:,} in / {self.total_output:,} out"
            f" / ${self.total_cost:.4f}"
        )


# =============================================================================
# FILE OPERATIONS
# =============================================================================

class FileApplier:
    """Apply LLM-generated file changes to the filesystem."""

    def __init__(self, project_root: Path, config: dict):
        self.root = project_root
        self.ban_modify = config["files"]["ban_modify_existing"]
        self.max_bytes = config["files"]["max_new_file_bytes"]

    def read(self, rel_path: str) -> Optional[str]:
        p = self.root / rel_path
        return p.read_text("utf-8") if p.exists() else None

    def write(self, rel_path: str, content: str) -> bool:
        p = self.root / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        old = p.read_text("utf-8") if p.exists() else ""
        p.write_text(content, "utf-8")
        changed = old != content
        if changed:
            logging.info(f"  ✏️ Updated {rel_path} ({len(content)} bytes)")
        return changed

    def append(self, rel_path: str, content: str) -> None:
        p = self.root / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write("\n" + content)
        logging.info(f"  ➕ Appended to {rel_path}")

    def patch(self, rel_path: str, old_str: str, new_str: str) -> bool:
        p = self.root / rel_path
        if not p.exists():
            logging.warning(f"  ⚠️ Cannot patch {rel_path}: file not found")
            return False
        content = p.read_text("utf-8")
        if old_str not in content:
            logging.warning(f"  ⚠️ Cannot patch {rel_path}: old text not found")
            return False
        new_content = content.replace(old_str, new_str, 1)
        if new_content == content:
            return False
        p.write_text(new_content, "utf-8")
        logging.info(f"  ✅ Patched {rel_path}")
        return True

    def apply_changes(self, data: dict) -> int:
        """Apply all changes from parsed LLM response."""
        changed = 0

        # Format 1: "files" key with {"path": {"action": ..., "content": ...}}
        files = data.get("files", data.get("changes", {}))
        if files and isinstance(files, dict):
            for path, ops in files.items():
                action = ops.get("action", "modify")
                content = ops.get("content", "")
                if action == "create":
                    if len(content) < 20:
                        logging.warning(f"  ⚠️ Skipping {path}: content too short")
                        continue
                    if len(content) > self.max_bytes:
                        logging.warning(f"  ⚠️ Skipping {path}: exceeds {self.max_bytes} bytes")
                        continue
                    self.write(path, content)
                    changed += 1
                elif action == "append":
                    if content:
                        self.append(path, content)
                        changed += 1
                elif action == "modify":
                    old_str = ops.get("old")
                    if old_str:
                        if self.patch(path, old_str, ops.get("new", content)):
                            changed += 1
                        continue
                    old_content = self.read(path) or ""
                    if old_content and self.ban_modify:
                        logging.warning(
                            f"  ❌ Blocked: modify existing file {path}"
                            f" ({len(old_content)} bytes). Use patches instead."
                        )
                        continue
                    self.write(path, content)
                    changed += 1

        # Format 2: "patches" array
        patches = data.get("patches", [])
        if patches and isinstance(patches, list):
            for p in patches:
                fname = p.get("file")
                ops = p.get("operations", [])
                for op in ops:
                    t = op.get("type", "replace")
                    if t in ("replace", "modify"):
                        if self.patch(fname, op.get("old", ""), op.get("new", "")):
                            changed += 1
                    elif t == "append":
                        self.append(fname, op.get("content", ""))
                        changed += 1

        # Format 3: Direct operations array
        operations = data.get("operations", [])
        if operations and isinstance(operations, list):
            for op in operations:
                fname = op.get("file", op.get("path"))
                t = op.get("type", op.get("action", "replace"))
                if t in ("replace", "modify"):
                    if self.patch(fname, op.get("old", ""), op.get("new", "")):
                        changed += 1
                elif t == "append":
                    self.append(fname, op.get("content", ""))
                    changed += 1
                elif t in ("create", "write"):
                    self.write(fname, op.get("content", ""))
                    changed += 1

        return changed


# =============================================================================
# JSON EXTRACTOR
# =============================================================================

def extract_json(text: str) -> Optional[dict]:
    """Extract JSON from LLM response (handles markdown + reasoning prefix)."""
    text = text.strip()

    # Strategy 1: try direct parse
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # Strategy 2: markdown code fences
    fences = re.findall(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    for f in fences:
        try:
            data = json.loads(f.strip())
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    # Strategy 3: find outermost { ... }
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        candidate = text[brace_start:brace_end + 1]
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    # Strategy 4: find "files" or "patches" key
    for key in ['"files"', '"patches"', '"operations"']:
        idx = text.find(key)
        if idx >= 0:
            obj_start = text.rfind("{", 0, idx)
            obj_end = text.rfind("}")
            if obj_start >= 0 and obj_end > obj_start:
                try:
                    data = json.loads(text[obj_start:obj_end + 1])
                    if isinstance(data, dict):
                        return data
                except json.JSONDecodeError:
                    pass

    return None


# =============================================================================
# MANIFEST READER
# =============================================================================

class ManifestReader:
    """Read and track task manifests."""

    def __init__(self, manifest_path: Path):
        self.path = manifest_path

    def load(self) -> Optional[dict]:
        if not self.path.exists():
            logging.error(f"Manifest not found: {self.path}")
            return None
        return json.loads(self.path.read_text("utf-8"))

    def save(self, data: dict) -> None:
        self.path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), "utf-8"
        )

    def find_pending(self, data: dict) -> Optional[dict]:
        for task in data.get("tasks", []):
            if task.get("status") in ("pending", "in_progress"):
                return task
        return None

    def find_next_stage(self, task: dict) -> Optional[dict]:
        stages = task.get("stages", [task])
        for stage in stages:
            if stage.get("status", "pending") == "pending":
                return stage
        return task if task.get("status") == "pending" else None

    def update_stage(self, data: dict, task_id: str,
                     stage_idx: int, status: str, result: str = "") -> None:
        for task in data.get("tasks", []):
            if task["id"] == task_id:
                stages = task.get("stages", [task])
                if stage_idx < len(stages):
                    stages[stage_idx]["status"] = status
                    stages[stage_idx]["last_result"] = result
                    stages[stage_idx]["updated_at"] = datetime.now().isoformat()
                # Check if all stages done
                all_done = all(
                    s.get("status") == "completed"
                    for s in stages
                )
                if all_done:
                    task["status"] = "completed"
                    task["completed_at"] = datetime.now().isoformat()
                break


# =============================================================================
# STAGE EXECUTOR
# =============================================================================

class StageExecutor:
    """Execute a single task stage via LLM."""

    def __init__(self, llm: LLMClient, applier: FileApplier, config: dict):
        self.llm = llm
        self.applier = applier
        self.preview_lines = config["files"]["preview_lines"]

    def execute(self, stage: dict, task_id: str, stage_idx: int) -> tuple:
        """Execute one stage. Returns (success: bool, message: str)."""
        action = stage.get("action", "create")
        target_file = stage.get("file", "")
        context = stage.get("context", stage.get("desc", ""))
        desc = stage.get("desc", target_file)

        logging.info(f"  Stage: [{action}] {target_file} — {desc}")

        # Build prompt based on action
        if action == "create":
            prompt = (
                f"Create the file {target_file} for the project.\n\n"
                f"## Context\n{context}\n\n"
                f"Output JSON with the complete file content."
            )
            response = self.llm.call(SYSTEM_CREATE, prompt)

        elif action == "patch":
            # Read existing file for context
            content = self.applier.read(target_file)
            if not content:
                return False, f"File {target_file} not found for patching"

            lines = content.split("\n")
            if len(lines) > self.preview_lines * 2:
                preview = (
                    "\n".join(lines[:self.preview_lines])
                    + f"\n... ({len(lines) - self.preview_lines * 2} lines ...)\n"
                    + "\n".join(lines[-self.preview_lines:])
                )
            else:
                preview = content

            prompt = (
                f"File: {target_file} ({len(lines)} lines)\n\n"
                f"```\n{preview}\n```\n\n"
                f"## Change Needed\n{context}\n\n"
                f"Output ONLY JSON with patches (replace operations)."
            )
            response = self.llm.call(SYSTEM_PATCH, prompt)

        elif action == "append":
            prompt = (
                f"Append content to {target_file}.\n\n"
                f"## Context\n{context}\n\n"
                f"Output JSON with append action (just the content to add)."
            )
            response = self.llm.call(SYSTEM_PATCH, prompt)

        else:
            return False, f"Unknown action: {action}"

        if not response:
            return False, "LLM call failed"

        # Save raw response for debugging
        debug_dir = Path(".revo")
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / f"response_{task_id}_{stage_idx}.txt").write_text(response)

        # Extract and apply JSON
        data = extract_json(response)
        if not data:
            logging.warning(f"  Raw response preview: {response[:200]}...")
            return False, "Failed to parse JSON response"

        changes = self.applier.apply_changes(data)
        done = data.get("done", changes > 0)

        if changes > 0:
            return True, f"{changes} change(s) applied"
        else:
            return done, "No changes needed" if done else "No changes could be applied"


# =============================================================================
# MAIN LOOP
# =============================================================================

class RevoLoop:
    """The main Ralph Loop engine."""

    def __init__(self, config: dict):
        self.config = config
        self.interval = config["loop"]["interval_seconds"]
        self.manifest_path = Path("revo.json")
        self.project_root = Path.cwd()

        # Setup logging
        log_dir = Path(config["logging"]["dir"])
        log_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="[%(asctime)s] %(message)s",
            datefmt="%H:%M:%S",
            handlers=[
                logging.FileHandler(log_dir / config["logging"]["log_file"],
                                    encoding="utf-8"),
                logging.StreamHandler(),
            ],
        )

        # Initialize components
        self.llm = LLMClient(config)
        self.applier = FileApplier(self.project_root, config)
        self.reader = ManifestReader(self.manifest_path)
        self.executor = StageExecutor(self.llm, self.applier, config)
        self.iteration = 0

    def run(self):
        """Run the main loop until all tasks are done."""
        logging.info("=" * 60)
        logging.info("  REVO ENGINE STARTED")
        logging.info(f"  Provider: {self.llm.provider}")
        logging.info(f"  Model: {self.llm.model}")
        logging.info(f"  API: {self.llm.base_url}")
        logging.info(f"  Project: {self.project_root}")
        logging.info(f"  Manifest: {self.manifest_path}")
        logging.info(f"  PID: {os.getpid()}")
        logging.info("=" * 60)

        while True:
            self.iteration += 1

            # Load manifest
            data = self.reader.load()
            if not data:
                time.sleep(self.interval)
                continue

            task = self.reader.find_pending(data)
            if not task:
                logging.info("✨ All tasks done! Waiting for new tasks...")
                logging.info(f"  {self.llm.summary()}")
                # Sleep and re-check for new tasks
                time.sleep(self.interval * 6)
                continue

            task_id = task["id"]
            task["status"] = "in_progress"
            self.reader.save(data)

            # Execute stages
            stages = task.get("stages", [task])
            all_ok = True

            for idx, stage in enumerate(stages):
                if stage.get("status") == "completed":
                    continue

                logging.info(f"🚀 {task_id}: {task.get('title', task_id)}"
                             f" — Stage {idx + 1}/{len(stages)}")

                t0 = time.time()
                ok, msg = self.executor.execute(stage, task_id, idx)
                elapsed = time.time() - t0

                # Reload manifest for status update
                data = self.reader.load()
                status = "completed" if ok else "pending"
                self.reader.update_stage(
                    data, task_id, idx, status,
                    f"{'✅' if ok else '❌'} ({elapsed:.0f}s) {msg[:100]}"
                )

                status_icon = "✅" if ok else "❌"
                logging.info(f"  {status_icon} Stage done in {elapsed:.0f}s")

                if not ok:
                    all_ok = False
                    break

                time.sleep(self.interval)
                self.reader.save(data)

            # Mark entire task
            data = self.reader.load()
            for t in data["tasks"]:
                if t["id"] == task_id:
                    if all_ok:
                        t["status"] = "completed"
                        t["completed_at"] = datetime.now().isoformat()
                    break
            self.reader.save(data)

            if all_ok:
                logging.info(f"🎉 Task {task_id} COMPLETED!")
            else:
                logging.info(
                    f"⏳ Task {task_id} will retry (stage failed)"
                )

            time.sleep(self.interval)


# =============================================================================
# ENTRY POINT
# =============================================================================

def main():
    print("═══ Hermes-Revo Engine ═══")
    print("  Load. Execute. Revolve. Repeat.\n")

    config = load_config()

    # Override with env vars
    if os.environ.get("AI_PROVIDER"):
        config.setdefault("ai", {})["provider"] = os.environ["AI_PROVIDER"]
    if os.environ.get("HERMES_API_URL"):
        config.setdefault("ai", {}).setdefault("hermes", {})["base_url"] = os.environ["HERMES_API_URL"]
    if os.environ.get("HERMES_MODEL"):
        config.setdefault("ai", {}).setdefault("hermes", {})["default_model"] = os.environ["HERMES_MODEL"]
    if os.environ.get("OPENAI_BASE_URL"):
        config.setdefault("ai", {}).setdefault("openai", {})["base_url"] = os.environ["OPENAI_BASE_URL"]
    if os.environ.get("OPENAI_MODEL"):
        config.setdefault("ai", {}).setdefault("openai", {})["model"] = os.environ["OPENAI_MODEL"]
    if os.environ.get("REVO_MANIFEST"):
        config["manifest"] = os.environ["REVO_MANIFEST"]
    if os.environ.get("REVO_INTERVAL"):
        config["loop"]["interval_seconds"] = int(os.environ["REVO_INTERVAL"])

    loop = RevoLoop(config)
    try:
        loop.run()
    except KeyboardInterrupt:
        print("\n\n⏹️  Revo engine stopped by user.")
        print(f"📊 {loop.llm.summary()}")
        sys.exit(0)


if __name__ == "__main__":
    main()
