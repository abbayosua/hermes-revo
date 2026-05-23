# Hermes-Revo 🔄

**Autonomous stage-based coding engine. Run once, let it loop.**

```
"Load. Execute. Revolve. Repeat."
```

Revo is a background loop engine that reads task manifests and executes them stage-by-stage using any OpenAI-compatible LLM (DeepSeek, GPT, Qwen, etc.). Designed to pair with **Hermes Agent** — reads API keys from Hermes' `.env` so you don't need to configure anything.

No cron job needed. Just start it in the background.

---

## 🚀 Quick Start

```bash
# 1. Clone
git clone https://github.com/abbayosua/hermes-revo.git
cd hermes-revo

# 2. Install
pip install -r requirements.txt

# 3. Write your task manifest
# (Create revo.json in the same directory)

# 4. Run
python revo.py
```

Revo automatically finds API keys based on your **provider**:

**For `hermes` (default):**
1. `~/.hermes/.env` (Linux/Mac) or `~/AppData/Local/hermes/.env` (Windows)
2. `HERMES_API_KEY` / `LLM_API_KEY` / `OPENCODE_GO_API_KEY` environment variable

**For `openai` (OpenAI-compatible):**
1. `.env` file in project root → `OPENAI_API_KEY`
2. `OPENAI_API_KEY` environment variable

Set the provider via `revo.yaml` (`ai.provider`) or env var `AI_PROVIDER=openai`.

---

## 📋 How It Works

```
                           ┌──────────────────┐
         revo.json ───────►│                  │
         (task manifest)   │    REVO ENGINE   │
                           │                  │
         revo.yaml ───────►│  1. Read config  │
         (LLM config)      │  2. Parse stages │
                           │  3. Call LLM     │
         .env (Hermes) ───►│  4. Apply files  │
         (API key)         │  5. Loop stage   │
                           │  6. Repeat       │
                           └──────┬───────────┘
                                  │
                                  ▼
                          ┌──────────────────┐
                          │  Your Project    │
                          │  files/*         │
                          └──────────────────┘
```

### Task Manifest (`revo.json`)

```json
{
  "project": "My Project",
  "project_root": "/path/to/project",
  "tasks": [
    {
      "id": "my-feature",
      "title": "Feature name",
      "status": "pending",
      "stages": [
        {
          "action": "create",
          "file": "path/to/File.php",
          "desc": "What this creates",
          "context": "Detailed instructions for the LLM..."
        },
        {
          "action": "patch",
          "file": "path/to/existing.js",
          "desc": "What this modifies",
          "context": "What changes to make..."
        },
        {
          "action": "append",
          "file": "path/to/style.css",
          "desc": "CSS additions",
          "context": "What styles to add..."
        }
      ]
    }
  ]
}
```

### Stage Actions

| Action | Purpose | Example |
|--------|---------|---------|
| `create` | New file | `"action": "create", "file": "api/endpoint.php"` |
| `patch` | Modify existing | `"action": "patch", "file": "app.js"` |
| `append` | Add to end of file | `"action": "append", "file": "style.css"` |

---

## ⚙️ Configuration

Edit `revo.yaml`:

```yaml
# AI Provider — choose "hermes" (default) or "openai" (OpenAI-compatible)
ai:
  provider: "hermes"

  # Hermes Agent / OpenCode Go
  hermes:
    base_url: "https://opencode.ai/zen/go/v1"
    default_model: "deepseek-v4-flash"

  # OpenAI-compatible (OpenAI, Ollama, Groq, Together, vLLM, DeepSeek, OpenRouter...)
  openai:
    base_url: "https://api.openai.com/v1"
    model: "gpt-4o"
    # For Ollama: http://localhost:11434/v1
    # For Groq:   https://api.groq.com/openai/v1
    # For DeepSeek: https://api.deepseek.com/v1

loop:
  interval_seconds: 5
  max_retries: 3
  llm_timeout: 120

files:
  ban_modify_existing: true    # Never full-replace existing files
  preview_lines: 60            # Context lines shown for patching
```

### Available Models (via Hermes / OpenCode Go)

| Model | ID | Cost/req |
|-------|-----|----------|
| DeepSeek V4 Flash | `deepseek-v4-flash` | Cheapest |
| DeepSeek V4 Pro | `deepseek-v4-pro` | Better reasoning |
| Qwen3.6 Plus | `qwen3.6-plus` | Codes well, fast |
| Kimi K2.5 | `kimi-k2.5` | Strong coder |

---

## 📂 Project Structure

```
hermes-revo/
├── revo.py              # Main engine
├── revo.yaml            # Configuration
├── .env.example         # API key template
├── requirements.txt     # Python deps
├── .gitignore           # Security
├── README.md            # This file
├── examples/
│   ├── basic.json        # Simple task example
│   └── freeworship-bible.json  # Multi-stage project
└── .revo/               # Runtime data (gitignored)
    ├── revo.log          # Run log
    └── response_*.txt    # Raw LLM responses
```

---

## 🛡 Security

- **No API keys in Git** — `.env` is in `.gitignore`
- API keys are read from **Hermes Agent's `.env`** (outside repo)
- All secrets stay on your machine
- No telemetry, no tracking

---

## 🔄 Background Mode (Windows)

```bash
# Start (background)
start /B python revo.py

# Or use Hermes Agent's terminal:
# terminal(background=true, command="python revo.py")
```

---

## 📊 Example: FreeWorship Bible Feature

The `examples/freeworship-bible.json` manifest shows a real use case:
- Stage 1: Create `bible.php` proxy API (bible-api.com, Indonesian TB)
- Stage 2: Create `BibleBrowser.js` Vue 3 component
- Stage 3: Patch `app.js` to integrate
- Stage 4: Append CSS styles

---

## 🧠 Philosophy

Revo follows the **1 stage = 1 file = 1 LLM call** principle:

- ❌ Bad: "Create 5 files in 1 call" → token limit, JSON errors
- ✅ Good: "Create 1 file per call" → clean, reliable, trackable

Each stage is a focused, atomic unit of work.

---

Made by [abbayosua](https://github.com/abbayosua) — Ralph Loop Engine for Hermes Agent
