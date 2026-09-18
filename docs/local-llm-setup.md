# Local LLM setup — Ollama + Continue in VS Code, per machine

**Status: both machines done and measured — Mac 2026-09-16, PC 2026-09-18.** This doc exists
because the Continue config lives in `~/.continue`, the Ollama env lives in launchd or Windows user
variables, and the assistant's memory is per machine. None of it syncs. The repo is the only channel
between the two machines, so the reasoning, the config, and the measurements live here.

The two machines ended up with different setups on purpose. On the Mac, chat and autocomplete share
25 GiB of unified memory. On the PC, a 16 GB card cannot hold both at once, and **autocomplete is
the point** — chat is occasional. Read the shared sections, then the section for the machine you
are on.

If you are an AI assistant revisiting either machine: the decisions are the owner's and the numbers
are measured. Propose, measure, then write; do not pull models or edit config first. Claims below
about what Continue does were checked in its installed `extension.js` (2.0.0) — after an upgrade,
re-check them there rather than trusting this file or Continue's docs.

---

## Decisions that hold on both machines

| decision | choice | why |
|---|---|---|
| Editor integration | Continue (VS Code extension, `config.yaml` format) | Already installed and configured; `config.yaml` replaced `config.json` and is current in Continue 2.x. |
| Model server | Ollama | Continue's `provider: ollama`; one server serves chat, autocomplete and embeddings. |
| Chat / edit / apply model | Thinking **off** for these roles | Edit and apply need the model to emit code, not a reasoning trace. A thinking model is fine with `reasoning: false` in `config.yaml`, which Continue sends to Ollama as `think: false` (verified in its code). gpt-oss can only turn thinking *down*, not off. Mac: coder-tuned `qwen3-coder`. PC: see its section. |
| Chat model shape | Mixture-of-experts where memory allows | Only the active experts are read per token, so a 30B MoE with 3B active decodes ~3.5× faster than a dense 14B on the same hardware while scoring higher. It also spills gracefully: on the PC an MoE with 42% in system RAM still decodes at 105 tok/s, a dense model with 21% there at 15. Measured, see both sections. |
| Autocomplete model | A `qwen2.5-coder` **base** model; size per machine (Mac 1.5B, PC 7B) | Fill-in-the-middle **base** model. Continue's docs: chat models "will often perform poorly even with extensive prompting" at FIM. The newer Qwen generations (3-Coder, 3.5, 3.6) have no small sizes and no `insert` capability in Ollama, which is what Continue checks. The Mac picks latency; the PC can afford the 7B, which measured better on this repo's code. |
| Embeddings + indexing | `nomic-embed-text`, indexing **on**, `.continueignore` excludes the data dirs (Mac). PC: indexing off. | Enables `@codebase`. Without the ignore file, `.venv/`, `experiment_results/` and `HTML Extracts/` are most of the embedding work for nothing. The PC's chat is occasional, so it was not set up there. |
| Context length | 32768 set in `config.yaml` as `contextLength` | Continue's own default is **also 32768** (`DEFAULT_CONTEXT_LENGTH` in its code), sent as `num_ctx` unless the model's Ollama params set one — so the line makes the value visible rather than changing it. Ollama's *server* default (4096 on the Mac; the PC app's slider value) only reaches clients that send no `num_ctx`, such as `ollama run`. Verified: `ollama ps` shows 32768 in the CONTEXT column after a chat. |
| Server tuning | flash attention on, KV cache `q8_0`, keep-alive 1h | q8_0 halves KV memory at negligible quality cost and requires flash attention. Keep-alive stops the 20 GB model unloading between prompts (load was 8.6 s dense, 9 ms warm). Continue sends its own `keep_alive` with every request (the config's `keepAlive`, or 30 min), so the server's 1h only covers other clients. |
| Neural Engine (Mac) | Not used | Only reachable via Core ML, no server for Continue, ~18 tok/s on 8B, 2–4k context cap. Its win is watts, not speed. Ollama, llama.cpp and MLX all run on the GPU via Metal. |

### Continue 2.0.0, as its code behaves

Checked in the installed `extension.js` on 2026-09-18; the Mac runs the same version. Where this
contradicts Continue's docs or an older note in this file, the code wins.

**What reaches Ollama:**

- `contextLength` → `num_ctx`. Unset, Continue sends **32768** unless the model's Ollama params set
  `num_ctx`.
- `temperature` / `topP` / `topK` unset → **not sent**, so the model's own Ollama params apply
  (`ollama show <tag> --parameters` lists them — usually its maker's recommended values). A
  `DEFAULT_TEMPERATURE = 0.5` exists in the code, but only in a helper nothing calls.
- `reasoning` → `think`.
- `keepAlive` (seconds) → `keep_alive`, **1800** when unset. Every request restarts the countdown;
  a model that Ollama unloads to make room for another is unloaded regardless.
- `numGpu` → `num_gpu`.
- Fill-in-the-middle is used only when the model's template contains `.Suffix` — the same thing
  `ollama show` lists as the `insert` capability.
- Next Edit runs only for models whose name contains `mercury-coder` or `instinct`. With any other
  autocomplete model the toggle is inert, and "(NE)" in the status bar is only a label.
- `package.json` declares `extensionKind: ["ui", "workspace"]`, so when Continue is installed
  locally it runs on the local machine even in a remote (WSL) window.

**Autocomplete options** (`autocompleteOptions` in `config.yaml`):

| option | default | what it does |
|---|---|---|
| `debounceDelay` | 350 | ms after the last keystroke before asking the model. Both machines use 50. |
| `maxPromptTokens` | 1024 | Total prompt budget, split three ways by the next two options. Both machines use 512. |
| `prefixPercentage` | 0.3 | Share for code **above** the cursor — ~154 tokens, 10–15 lines, at 512. |
| `maxSuffixPercentage` | 0.2 | Cap for code **below** the cursor. The remainder goes to context snippets. |
| `modelTimeout` | 150 | **Misnamed.** Once one non-blank line has arrived *and* this many ms have passed since streaming began, stop and show the lines so far. Never drops a completion; shortens multi-line ones. |
| `onlyMyCode` | true | Go-to-definition snippets only from the workspace, not from `site-packages`. |
| `useRecentlyEdited`, `useRecentlyOpened`, `useImports` | true | Snippet sources: recent edits, recently opened files, definitions of imported symbols. |
| `useCache` | true | Reuse a completion when the same prefix comes back, e.g. after a backspace. |
| `transform` | true | Post-processing: stop at an unmatched bracket, don't repeat code already below the cursor. |
| `template` | per model | The FIM prompt format. Auto-detected for Qwen; leave it. |

**Measurement protocol, Mac:**

```bash
ollama run <model> --verbose "Write a Python function that parses a CSV header row into a dict of column name to index, with type hints and a docstring." >/dev/null
```

Record `eval rate` (decode tokens/s), `prompt eval rate`, and `load duration`. Then send one chat
message in Continue and run `ollama ps`: the row must show the model at `100% GPU` and the
CONTEXT column must read `32768`. If it reads `4096`, `contextLength` is not being forwarded.

**Measurement harness, PC.** Same prompt, but through Ollama's HTTP API rather than `ollama run`:
the same fields, exact, in requests shaped like Continue's. Chat — per model, with nothing else
loaded, three requests: the prompt above cold, the same prompt warm, and a ~7K-token prompt
(`app/graphql/context.py` + "Summarize what this file does in three sentences."):

```json
POST /api/chat
{"model": "<tag>", "messages": [{"role": "user", "content": "<prompt>"}], "stream": false,
 "keep_alive": "1h", "think": false, "options": {"num_ctx": 32768, "num_predict": 1024}}
```

`think` is sent only to thinking models that can turn it off. Rates come from the response
(`eval_count / eval_duration`, `prompt_eval_count / prompt_eval_duration`, `load_duration`); the
GPU/CPU split from `GET /api/ps` (`size_vram / size`).

Autocomplete — Continue's FIM request, **scored against this repo**: 30 cursor positions (fixed
seed) in `app/` and `scripts/` `.py` files, each 40–70% of the way into a line of at least 30
characters. Prefix = the text before the cursor, suffix = the following lines, ground truth = the
rest of the line; a hit is the response's first line equal to it.

```json
POST /api/generate
{"model": "<tag>", "prompt": "<prefix>", "suffix": "<suffix>", "stream": false,
 "keep_alive": "1h", "options": {"num_ctx": 4096, "num_predict": 64, "temperature": 0}}
```

Time to first token = `prompt_eval_duration` + one token's share of `eval_duration`. The scripts
were session scratch and are not in the repo; the request bodies above are the whole method.

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

The `contextLength` comment in this file overstates it: Continue's own default is also 32768 (see
*Continue 2.0.0, as its code behaves*), so that line makes the value explicit rather than raising it
from 4096. Recorded as it is on the machine.

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

## PC — RTX 5070 Ti (16 GB), Ryzen 7 7800X3D, Windows + WSL Ubuntu

Measured 2026-09-18. RTX 5070 Ti, 16 GB (Ollama reports 15.9 GiB, ~14.7 available; the desktop
holds 1.2–2.2 GB of it depending on what is open), driver 616.92, Ollama's CUDA 13 path. Ryzen 7
7800X3D, 32 GB DDR5-6000. **Ollama 0.34.2, native Windows** (not in WSL); Continue 2.0.0.

**Autocomplete is the point here; chat is occasional** — prompt-engineering practice, and a local
fallback if hosted tokens get expensive. That ordering decided everything below.

### What the pre-measurement to-do list turned into

1. **16 GB is a wall** — confirmed, and it bit harder than expected: chat and autocomplete cannot
   share the card (below).
2. **Where Ollama runs — moot.** Continue is installed on the Windows side and declares
   `extensionKind: ["ui", "workspace"]`, so VS Code runs it on Windows even in a WSL window, and
   `localhost:11434` is Windows' localhost. No `.wslconfig` mirrored networking and no
   `OLLAMA_HOST=0.0.0.0` (which would also have put Ollama on the LAN). `%UserProfile%\.continue` is
   the live config.
3. **Env vars** — Windows user variables, set with `setx` (below). One surprise: the server-wide
   default context was **131072**, and it came from the **Ollama app's Settings → Context length
   slider**, not an env var. Now 32k.
4. **`OLLAMA_MODELS`** — left at the default, `%UserProfile%\.ollama\models`. C: is a Samsung 980
   PRO NVMe with ~900 GB free. **Not O:** it looks empty and roomy, but it is a 2.5" SATA laptop
   HDD (ST1000LM024), where a 20 GB model would take minutes to load.
5. **`.continueignore`** — moot for now: indexing is off on the PC.

What the PC had been running before this, since July: Devstral Small 2 (24B dense) for chat,
`qwen2.5-coder:7b` — the **instruct** tag — for autocomplete, no `contextLength`, 5-minute
keep-alive, f16 KV cache. The measurements show what that cost.

### Chat models: speed

One model loaded at a time, 32K context, VS Code closed (desktop 1.2–1.5 GB). Decode is the warm
run; "reads" is prompt processing on the ~7K-token prompt.

| model | shape | in memory | on GPU | decode, short prompt | decode at ~7K context | reads ~7K tokens | cold load |
|---|---|---|---|---|---|---|---|
| `gpt-oss:20b` | MoE 21B, MXFP4 | 12.8 GB | 100% | **152 tok/s** | 143 | 0.8 s | 10.2 s |
| `qwen3.6:35b-a3b-coding` | MoE 35B, 3B active | 22.6 GB | 58% | 105 | 79 | 3.8 s | 29.8 s |
| `qwen3-coder:30b` | MoE 30B, 3B active | 20.6 GB | 71% | 77 | 69 | 2.6 s | 17.3 s |
| `qwen3.5:9b-q8_0` | dense 9.7B, Q8 | 9.6 GB | 100% | 76 | 75 | 1.2 s | 6.0 s |
| `qwen2.5-coder:14b` | dense 14B | 12.2 GB | 100% | 76 | 64 | 2.2 s | 5.6 s |
| `devstral-small-2` | dense 24B | 17.7 GB | 79% | **15** | 12 | 4.1 s | 10.0 s |

- **How a model spills decides its speed.** llama.cpp's fit step (`common_params_fit` in the
  server log) keeps every layer of an MoE on the GPU and moves only expert weights to system RAM —
  `qwen3.6` logs "offloaded 42/42 layers" at 58% GPU. A dense model loses whole layers instead
  (Devstral: 33/41), and every token then reads them from RAM at ~1/14 of the card's bandwidth.
  Hence 105 tok/s against 15.
- **The spill costs reading, not writing:** the spilled MoEs read a long prompt at 1.8–2.6K tok/s
  against 3–8K for the all-GPU models, because a 7K-token prompt touches nearly every expert.
- **gpt-oss always thinks** — 1,200–3,200 characters of reasoning before answering the short
  prompt. Fastest here, but that is 2–5 s before every answer, edits included.
- `qwen3.6:35b-a3b-coding`'s params include `draft_num_predict 2`, which suggests the plain tag
  already does speculative decoding (the Mac's options table lists `-mtp` as a separate tag). The likely
  reason it outruns `qwen3-coder` with less on the GPU; not verified.
- Ollama's `nvfp4` tags are listed as **MLX/NVFP4** — the Mac backend — so the 5070 Ti's native
  FP4 is not reachable through them.
- The one model both machines ran, `qwen3-coder:30b`: 34.9 tok/s on the Mac (all on GPU), 77.4
  here with 29% in system RAM.

### Autocomplete models: latency and accuracy

Continue's FIM request, 30 cursor positions from this repo (method under *Measurement harness,
PC*), ~400 tokens before the cursor and ~100 after:

| base model | first token, median / p90 | per token | ~30-token completion | exact rest-of-line | VRAM | cold load |
|---|---|---|---|---|---|---|
| `qwen2.5-coder:1.5b-base` | 25 / 36 ms | 2.8 ms | ~106 ms | 6 / 30 | ~1.1 GB | 2.1 s |
| `qwen2.5-coder:3b-base` | 41 / 58 ms | 4.2 ms | ~162 ms | 6 / 30 | ~2.1 GB | 2.3 s |
| **`qwen2.5-coder:7b-base`** | 74 / 96 ms | 6.9 ms | ~275 ms | **9 / 30** | ~4.6 GB | 3.0 s |

About half the samples fell in docstrings and comments, where no model reproduces the prose
verbatim; every hit was a code line. The 7B's extra hits are the ones `mypy --strict` cares about —
`MetricCatalog | None = None` in `app/graphql/context.py` (the 1.5B and 3B wrote
`MetricCatalog = None`) and the full `AsyncSession, async_sessionmaker, create_async_engine` import
in `app/database.py`. The 3B bought nothing over the 1.5B.

**Prompt budget, re-run with Continue's real split** (7B, same 30 positions): at
`maxPromptTokens: 512` the model gets ~154 tokens above the cursor — **8 / 30**, 48 ms to first
token. At 1024, ~307 tokens — 9 / 30, 75 ms. One hit is noise; the 27 ms is not. Stays at 512.

### Chat and autocomplete cannot share the card

Pairs loaded in both orders, read from `ollama ps`:

| loaded first | then | result |
|---|---|---|
| `qwen3.6` (chat) | 1.5B autocomplete (from Continue) | **both stay** — the only pairing that did |
| 1.5B or 3B autocomplete | `qwen3.6` | autocomplete unloaded |
| 7B autocomplete | `qwen3.6` | 7B unloaded |
| 7B autocomplete | `qwen3.5:9b` | 7B unloaded |
| `qwen3.5:9b` | 7B autocomplete | 9B unloaded |

Ollama makes room by unloading idle models, and `keepAlive` does not protect against that. A model
that arrives second stays alongside only if it fits in what is left by Ollama's own, slightly
generous, size estimate — and with ~2 GB of desktop on 16 GB, nothing bigger than the 1.5B does.
So with the 7B, each chat session costs a swap:

- **starting a chat** loads the chat model: ~6 s for `qwen3.5:9b`, 18–30 s for `qwen3.6`;
- **going back to typing** reloads autocomplete: ~3 s, nearer 4 while you keep typing, because
  each keystroke cancels Continue's request and the cancel aborts the load, which then restarts.
  The old setup's log shows the same effect at its worst: ten aborted loads in 13 seconds.

Accepted, because chat is occasional. Chat speed is unaffected — the chat model gets its usual
split once autocomplete is out.

**`numGpu` does not rescue it.** Continue forwards `numGpu` as Ollama's `num_gpu` (verified), which
could have capped the chat model's GPU layers to leave room for autocomplete. With `qwen3.6` at
`num_gpu` 32 and 24 the runner crashed on load — `CUDA error: shared object initialization failed`
(`ggml-cuda.cu:108`), exit `0xc0000409`, which is how Windows reports a deliberate abort, not a
security event. A later load at default settings, all 42 layers on the GPU, crashed the same way:
**3 of ~8 `qwen3.6` loads crashed on 2026-09-18**, all after the first `num_gpu` attempt, trigger
unknown. No other model crashed. The config's comment says to retry once.

**Considered, not built:** a second Ollama server on another port serving only autocomplete (via a
per-model `apiBase`), so chat could never unload it. It adds a second server to start at login, and
it would squeeze the chat model into less VRAM — the wrong direction for the model that crashes.

### VS Code settings that mattered more than any model

These live in the **synced** user `settings.json`, so the Mac has them too.

- `"editor.inlineSuggest.minShowDelay": 1000` → **`0`**. It held every inline suggestion for at
  least a second after typing, so no model's speed was visible behind it — probably set to quiet
  Copilot. The largest single latency fix of the session.
- `"github.copilot.nextEditSuggestions.enabled"` → **`false`**, so Copilot's next-edit suggestions
  stop sending edits to GitHub. Copilot's inline completions were already off
  (`"github.copilot.enable": {"*": false, ...}`).
- `"continue.enableNextEdit": true` — left as is, and inert with a Qwen base model (see
  *Continue 2.0.0, as its code behaves*).

**`modelTimeout: 400` is on trial.** The owner's read: at the default 150, a suggestion appeared
"right there", ~0.2 s after typing stopped; 400 sits on the bubble of "is it thinking, or does it
not know what to say?" Longer multi-line suggestions against that pause — tune by feel.

### Where the config lives (PC)

- `%UserProfile%\.continue\config.yaml` — the **Windows** side, because Continue runs there.
  Contents below.
- `%UserProfile%\.continue\.continuerc.json` — `{"disableIndexing": true}`. The `.continueignore`
  beside it is empty.
- Windows user environment variables — the three below.
- The Ollama app's **Settings → Context length: 32k** (was 128k). It sets the server's
  `OLLAMA_CONTEXT_LENGTH`; Continue overrides it per request, but `ollama run` and scripts use it.

```powershell
setx OLLAMA_FLASH_ATTENTION 1
```

```powershell
setx OLLAMA_KV_CACHE_TYPE q8_0
```

```powershell
setx OLLAMA_KEEP_ALIVE 1h
```

Then quit Ollama from the tray and start it again **from the Start menu**. `setx` updates the
registry and Explorer, not the terminal it ran in, so an Ollama started from that terminal starts
without the values, silently — the Windows cousin of the Mac's launchd ordering problem. Check:

```powershell
(Select-String -Path "$env:LOCALAPPDATA\Ollama\server.log" -Pattern 'server config' | Select-Object -Last 1).Line -split ' ' | Select-String 'CONTEXT_LENGTH|FLASH|KEEP_ALIVE|KV_CACHE'
```

It must print `OLLAMA_CONTEXT_LENGTH:32768`, `OLLAMA_FLASH_ATTENTION:true`,
`OLLAMA_KEEP_ALIVE:1h0m0s` and `OLLAMA_KV_CACHE_TYPE:q8_0`.

#### `%UserProfile%\.continue\config.yaml` (PC)

As on disk, 2026-09-18:

```yaml
name: Local Agent
version: 1.0.0
schema: v1

# PC: RTX 5070 Ti, 16 GB. Measured 2026-09-18 (docs/local-llm-setup.md, PC section).
# AUTOCOMPLETE IS THE POINT of this setup; chat is occasional.
#
# The card cannot hold a chat model and the 7B autocomplete model at once: loading
# either pushes the other out. Starting a chat costs a chat-model load; going back
# to typing costs a ~3 s autocomplete reload.
#
# Sampling: temperature/topP/topK are set only where the model's Ollama params
# don't already carry its maker's values (`ollama show <model> --parameters`).

models:
  # Use case not settled: prompt-engineering practice, and a local fallback if
  # hosted tokens get expensive. Default chat: loads in ~6 s, fits the card entirely.
  - name: "Qwen3.5 9B · chat, use case TBD (prompt practice / fallback)"
    provider: ollama
    model: qwen3.5:9b-q8_0
    roles:
      - chat
      - edit
      - apply
    defaultCompletionOptions:
      # Thinking model; Continue sends reasoning: false to Ollama as think: false.
      # Its shipped params are Qwen's thinking-mode values; these are non-thinking.
      reasoning: false
      temperature: 0.7
      topP: 0.8
      contextLength: 32768
      maxTokens: 4096
      keepAlive: 3600

  # Same open question, for heavier prompts. Loads in 18-30 s (42% of it runs from
  # system RAM), and crashed on 3 of ~8 loads on 2026-09-18 with a CUDA error in
  # Ollama 0.34.2's llama.cpp. Retry once if a first message fails.
  - name: "Qwen3.6 35B-A3B · heavier chat, use case TBD (slow load, has crashed)"
    provider: ollama
    model: qwen3.6:35b-a3b-coding
    roles:
      - chat
      - edit
      - apply
    defaultCompletionOptions:
      reasoning: false
      temperature: 0.7
      topP: 0.8
      contextLength: 32768
      maxTokens: 4096
      keepAlive: 3600

  - name: Qwen 2.5 Coder 7B base (Autocomplete)
    provider: ollama
    model: qwen2.5-coder:7b-base
    roles:
      - autocomplete
    autocompleteOptions:
      debounceDelay: 50
      maxPromptTokens: 512
      onlyMyCode: true
      useRecentlyEdited: true
      modelTimeout: 400
    defaultCompletionOptions:
      temperature: 0.2
      maxTokens: 128
      # A prompt is at most maxPromptTokens + maxTokens (640 tokens), so 4096
      # is ample, and a smaller window reserves less VRAM.
      contextLength: 4096
      # Seconds after the LAST completion request; each request restarts it.
      # Being pushed out by a chat model ignores this.
      keepAlive: 3600

  - name: Nomic Embeddings
    provider: ollama
    model: nomic-embed-text
    roles:
      - embed

rules:
  - Use Python 3.12+. Prefer Protocol-based dependency injection over inheritance.
  - Use fail-fast validation that raises exceptions rather than silently correcting invalid input.
  - Follow existing naming conventions in the codebase before introducing new patterns.
  - When explaining code or suggesting alternatives, briefly note why, not just what.
  - Prefer explicit over clever — use readable patterns even when a more compact form exists
```

### Models on disk (PC)

| model | size | why it is kept |
|---|---|---|
| `qwen2.5-coder:7b-base` | 4.7 GB | autocomplete |
| `qwen3.5:9b-q8_0` | 10 GB | default chat |
| `qwen3.6:35b-a3b-coding` | 22 GB | heavier chat |
| `gpt-oss:20b` | 13 GB | not in the config; a second model family for prompt-engineering practice — fastest here and fits the card, but always thinks |
| `qwen2.5-coder:1.5b-base` | 1 GB | light autocomplete fallback |
| `nomic-embed-text` | 0.3 GB | embeddings, for if indexing is turned on |

Removed after measuring: `devstral-small-2`, `qwen3-coder:30b`, `qwen2.5-coder:14b`,
`qwen2.5-coder:7b` (the old instruct autocomplete) and `qwen2.5-coder:3b-base`.

### Open

- `modelTimeout`: 150 or 400, by feel.
- Indexing is off and `.continueignore` is empty. To use `@codebase`, set `disableIndexing` to
  `false` in `.continuerc.json`, copy the Mac's ignore list, and expect `nomic-embed-text` (0.3 GB)
  to join the VRAM contest.
- The `qwen3.6` crash — if it keeps happening, drop the entry or check a newer Ollama.
- Local Next Edit (Continue's `instinct` model) — the Copilot-NES-style experience without the
  round trip. Not explored.
