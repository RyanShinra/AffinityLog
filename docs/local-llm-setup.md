# Local LLM setup — Ollama + Continue in VS Code, per machine

**Status: Mac done and measured (2026-09-16). PC not started.** This doc exists because the
Continue config lives in `~/.continue`, the Ollama env lives in launchd or Windows user variables,
and the assistant's memory is per machine. None of it syncs. The repo is the only channel between
the two machines, so the reasoning, the config, and the measurements live here.

If you are an AI assistant starting the PC half: read this whole file, then the **PC** section
last. Do not redo the model research — the tables below are current as of 2026-09-16 and the
decisions are the owner's. Propose, measure, then write; do not pull models or edit config first.

---

## Decisions that hold on both machines

| decision | choice | why |
|---|---|---|
| Editor integration | Continue (VS Code extension, `config.yaml` format) | Already installed and configured; `config.yaml` replaced `config.json` and is current in Continue 2.x. |
| Model server | Ollama | Continue's `provider: ollama`; one server serves chat, autocomplete and embeddings. |
| Chat / edit / apply model | A **coder-tuned, non-thinking** model | Edit and apply roles need the model to emit code, not a reasoning trace. Thinking models (qwen3.6 coding variants) need thinking disabled for these roles. |
| Chat model shape | Mixture-of-experts where memory allows | Only the active experts are read per token, so a 30B MoE with 3B active decodes ~3.5× faster than a dense 14B on the same hardware while scoring higher. Measured, see below. |
| Autocomplete model | `qwen2.5-coder:1.5b-base` | Fill-in-the-middle **base** model. Continue's docs: chat models "will often perform poorly even with extensive prompting" at FIM. Latency wins here; 1.5B is Continue's own pick, 3b-base the next step up. |
| Embeddings + indexing | `nomic-embed-text`, indexing **on**, `.continueignore` excludes the data dirs | Enables `@codebase`. Without the ignore file, `.venv/`, `experiment_results/` and `HTML Extracts/` are most of the embedding work for nothing. |
| Context length | 32768 set in `config.yaml` as `contextLength` | Ollama's server default is **4096**, a few hundred lines of code. Continue forwards `contextLength` as `num_ctx` — **verified**: `ollama ps` shows 32768 in the CONTEXT column after a chat. |
| Server tuning | flash attention on, KV cache `q8_0`, keep-alive 1h | q8_0 halves KV memory at negligible quality cost and requires flash attention. Keep-alive stops the 20 GB model unloading between prompts (load was 8.6 s dense, 9 ms warm). |
| Neural Engine (Mac) | Not used | Only reachable via Core ML, no server for Continue, ~18 tok/s on 8B, 2–4k context cap. Its win is watts, not speed. Ollama, llama.cpp and MLX all run on the GPU via Metal. |

**Measurement protocol** (same on both machines, so the numbers are comparable):

```bash
ollama run <model> --verbose "Write a Python function that parses a CSV header row into a dict of column name to index, with type hints and a docstring." >/dev/null
```

Record `eval rate` (decode tokens/s), `prompt eval rate`, and `load duration`. Then send one chat
message in Continue and run `ollama ps`: the row must show the model at `100% GPU` and the
CONTEXT column must read `32768`. If it reads `4096`, `contextLength` is not being forwarded.

---

## Mac — Apple M4 MacBook Air, 32 GB unified memory

Ollama 0.34.1 as `Ollama.app`, Continue 2.0.0. Ollama's server log reports **25.0 GiB** usable by
the GPU (macOS keeps the rest), so that is the real budget, not 32.

### Models

| role | model | on disk |
|---|---|---|
| chat / edit / apply | `qwen3-coder:30b` (30B MoE, 3B active, Q4_K_M, non-thinking, 256K native ctx) | 19 GB |
| autocomplete | `qwen2.5-coder:1.5b-base` | 1 GB |
| embed | `nomic-embed-text` | 0.3 GB |
| kept for A/B only | `qwen2.5-coder:14b` (the previous chat model) | 9 GB |

### Measured, 2026-09-16, same prompt

| | `qwen2.5-coder:14b` (dense, before) | `qwen3-coder:30b` (MoE, after) |
|---|---|---|
| decode | **10.05 tok/s** | **34.85 tok/s** |
| prompt eval | 72.6 tok/s | 65.9 tok/s |
| load | 8.6 s | 9 ms (warm, keep-alive) |
| `ollama ps` | — | 20 GB, 100% GPU, CONTEXT 32768 |

### Chat-model options considered (all 256K native context)

| tag | size | shape | thinking | fits in 25 GiB with autocomplete + 32k KV? | verdict |
|---|---|---|---|---|---|
| `qwen3-coder:30b` | 19 GB | MoE 3B active | no | yes, ~3 GB headroom | **chosen** |
| `qwen3.6:27b-coding-mtp-q4_K_M` | 18 GB | dense + multi-token prediction | yes | yes | candidate to measure later; dense speed on an Air is single digits unless MTP rescues it |
| `qwen3.6:27b-coding-nvfp4` | 20 GB | dense, Ollama MLX backend | yes | barely | as above, MLX path |
| `qwen3.6:35b-a3b-coding-nvfp4` | 22 GB | MoE, MLX backend | yes | no, ~1 GB left | memory-pressure territory. Revisit on hardware with more room |
| `qwen3.6:35b-a3b-coding` / `-mtp-q4_K_M` | 23 GB | MoE | yes | no | same |

Ollama 0.34 on macOS bundles an MLX backend (the `nvfp4`, `mxfp8`, `mlx-bf16` tags). Not yet
measured against the llama.cpp path here; worth one run if a qwen3.6 candidate is tried.

### Where the config lives

- `~/.continue/config.yaml` — full contents below.
- `~/.continue/.continuerc.json` — `{"disableIndexing": false}`.
- `~/.continue/.continueignore` — global ignore, contents below.
- `~/Library/LaunchAgents/com.ollama.env.plist` — sets the env at login, then launches the app.

**Why a LaunchAgent and not `.zshrc`:** `Ollama.app` is launched by launchd, not by a shell, so it
never reads shell rc files. `launchctl setenv` puts variables into launchd's environment, which the
app inherits *if it starts afterwards*, so the plist does both in order. Load it **without sudo**
(`sudo` into the `gui/<uid>` domain fails with `Bootstrap failed: 5: Input/output error`, and even
when the setenv half lands the `open -a` half does not):

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ollama.env.plist
```

Check: `launchctl getenv OLLAMA_KV_CACHE_TYPE` prints `q8_0`, and the newest
`~/.ollama/logs/server*.log` contains `OLLAMA_FLASH_ATTENTION:true OLLAMA_KV_CACHE_TYPE:q8_0`.
If Ollama ever starts twice after login, the app has also registered itself as a background login
item: turn that off in System Settings → General → Login Items & Extensions.

#### `~/.continue/config.yaml` (Mac)

```yaml
name: Local Config
version: 1.0.0
schema: v1

models:
  - name: Qwen3 Coder 30B (MoE)
    provider: ollama
    model: qwen3-coder:30b
    roles:
      - chat
      - edit
      - apply
    defaultCompletionOptions:
      # Sent to Ollama as num_ctx. Without this Ollama's default is 4096,
      # which is a few hundred lines of code. Verify with `ollama ps` (CONTEXT column).
      contextLength: 32768
      maxTokens: 4096
      temperature: 0.3
      # Seconds. Overrides the server-side OLLAMA_KEEP_ALIVE per request.
      keepAlive: 3600

  - name: Qwen 2.5 Coder 1.5B (Autocomplete)
    provider: ollama
    model: qwen2.5-coder:1.5b-base
    roles:
      - autocomplete
    defaultCompletionOptions:
      contextLength: 8192
      temperature: 0.25
      keepAlive: 3600
    autocompleteOptions:
      debounceDelay: 100
      maxPromptTokens: 512
      onlyMyCode: true
      useRecentlyEdited: true

  - name: Nomic Embeddings
    provider: ollama
    model: nomic-embed-text
    roles:
      - embed

rules:
  - Use Python 3.12+. Prefer Protocol-based dependency injection over inheritance.
  - Use fail-fast validation that raises exceptions rather than silently correcting invalid input.
  - Follow existing naming conventions in the codebase before introducing new patterns.
  - When explaining code or suggesting alternatives, briefly note why — not just what.
  - Prefer explicit over clever — use readable patterns even when a more compact form exists
```

#### `~/.continue/.continueignore` (both machines)

```
.venv/
node_modules/
__pycache__/
.mypy_cache/
.ruff_cache/
.pytest_cache/
experiment_results/
HTML Extracts/
*.pdb
*.cif
*.csv
```

#### `~/Library/LaunchAgents/com.ollama.env.plist` (Mac)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ollama.env</string>

    <!-- Runs once at login. Puts the vars into launchd's environment, THEN starts
         Ollama.app so the app inherits them. Ordering matters: launchctl setenv only
         affects processes launched after it, so Ollama.app must not also be a
         Login Item (it could win the race and start without the vars). -->
    <key>ProgramArguments</key>
    <array>
        <string>/bin/sh</string>
        <string>-c</string>
        <string>launchctl setenv OLLAMA_FLASH_ATTENTION 1; launchctl setenv OLLAMA_KV_CACHE_TYPE q8_0; launchctl setenv OLLAMA_KEEP_ALIVE 1h; open -a Ollama</string>
    </array>

    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
```

---

## PC — RTX 5070 Ti (16 GB VRAM), Windows + WSL Ubuntu

**Not started.** What is different, and what has to be decided before anything is pulled:

1. **16 GB of VRAM is a wall, not a shared pool.** `qwen3-coder:30b` at Q4 is 19 GB and does not
   fit on the card. Ollama will split it between GPU and system RAM; for an MoE that can still be
   fast (the experts that miss the GPU are the cold ones), but it is a measurement, not an
   assumption. Candidates, in the order to try:
   - `qwen3-coder:30b` with partial offload — run the protocol, read the `PROCESSOR` column of
     `ollama ps` (it reports the CPU/GPU split), and keep it only if decode is clearly above the
     dense alternative.
   - `qwen2.5-coder:14b` at Q4 (9 GB) fits entirely with room for KV and autocomplete. Known
     quantity: 10 tok/s on the Mac; the 5070 Ti should be several times that.
   - `qwen3.6:27b-coding-*` at Q4 is 18 GB, same problem as the 30b, and dense, so offload hurts
     more. Skip unless the 30b measurement surprises.
   - `qwen3-coder:30b-a3b-q8_0` (32 GB) is out.
2. **Where Ollama runs.** Native Windows Ollama uses CUDA directly and is the simple path. Continue
   then needs to reach it: if VS Code opens the repo through the WSL remote, Continue runs inside
   WSL and `localhost:11434` is *WSL's* localhost, not Windows'. Either enable
   `networkingMode=mirrored` in `%UserProfile%\.wslconfig`, or set `OLLAMA_HOST=0.0.0.0` on the
   Windows side and point Continue's `apiBase` at the Windows host address. Decide this first; it
   changes the config.
3. **Env vars are Windows user environment variables**, not a plist: `OLLAMA_FLASH_ATTENTION=1`,
   `OLLAMA_KV_CACHE_TYPE=q8_0`, `OLLAMA_KEEP_ALIVE=1h`, then quit Ollama from the tray and relaunch.
   Also **`OLLAMA_MODELS`**: the Windows default is `C:\Users\<user>\.ollama\models`, and this
   week's theme is SSD cleanup, so decide which drive the 20–30 GB of weights should live on before
   the first pull.
4. **`.continueignore`** is the same list as the Mac's, but the `HTML Extracts/` and
   `experiment_results/` paths are relative to the workspace root, so it works unchanged under
   `/mnt/f/...`.
5. **Run the measurement protocol** and add a PC column to the table above. That is the deliverable
   for the PC session, along with the PC's `config.yaml` pasted under a `#### config.yaml (PC)`
   heading in this doc.
