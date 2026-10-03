<div align="center">

```
 █   █  █████  █   █  ███   ████  █      ███
 ██  █  █      ██ ██   █   █      █       █
 █ █ █  ████   █ █ █   █   █      █       █
 █  ██  █      █   █   █   █      █       █
 █   █  █████  █   █  ███   ████  █████  ███
```

# NemiCLI

**Your own AI agent in the terminal – cloud & local, with tools, memory, and heart.** ✦

**Version 5.1 Alpha**

**Developed by VibeCoder · built with Claude Opus 5 (Anthropic)**

</div>

---

## What is it?

NemiCLI is a self-built **chat agent for the terminal** (Python, Windows). It talks to
**cloud models** (11 providers), **local models in its own GGUF engine** (without a server),
or through Ollama –
and it can **act**: read and write files, run commands, look things up on the web,
view and create images, read the screen, and send helpers out. Always with your confirmation.

It also has a lively interface with personality (Nemi, Lara, or your own), themes,
a mascot, live status display, reading aids, and adjustable presentation. If you prefer not to stare at
a terminal, `/gui` opens a calm **desktop window** using the same core; Skills 🧩 teach NemiCLI
recurring tasks – every instruction only after your approval.

**Pure Python – no Node.js, no npm.** Its own GGUF engine instead of llama.cpp, its own image pipelines
(Stable Diffusion, Krea 2), embeddings as safetensors or GGUF.

> Born from curiosity: “How does a coding agent actually work?” 💜

---

## 🚀 Start in 30 seconds

**Way 1 – the easy one:** double-click `start.bat`. The starter first checks the installation –
is Python there? the `venv`? the packages? – and builds whatever is missing. Then type `/einrichten`:
the setup assistant gets its own Python (isolated in `.runtime/`, no admin rights, nothing changed in
the system) and installs torch to match your graphics card. Two “yes” clicks, done.

**Way 2 – manually:**
```bash
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python main.py
```

**Way 3 – as an exe:** `venv\Scripts\python build_exe.py` → `dist/NemiCLI/` with `NemiCLI.exe`
(window app, opens its own terminal window), `NemiCLIc.exe` (the same with a console), and the
`NemiCLIT2` folder (about 345 MB, no Python required on the target PC). Deliberately not a
single-file exe: that would unpack itself into the temp folder on every start, which Bitdefender
may treat as malware. The build aborts instead of emptying `dist/NemiCLI` if your own data is already there.
Every build writes **`SHA256SUMS.txt`** (checksums for the exe files) and **signs** the exe files if
a code-signing certificate `CN=NemiCLI` is present – create it once with `python zertifikat.py`
(self-created, valid on this PC; Windows asks once).

**Your folder:** On the very first start, a window asks **where your data should live** –
chats, images, models, learned data. It is kept separate from the program so NemiCLI can move or be
rebuilt without taking your memory with it. `/start` opens that window again at any time; when changing
locations, data is **copied, never deleted**.

**Start from anywhere (`nemicli`):** add the program folder to the user PATH once:
```powershell
[Environment]::SetEnvironmentVariable('Path', [Environment]::GetEnvironmentVariable('Path','User') + ';C:\Users\<user>\AppData\Local\NemiCli', 'User')
```
Open a new terminal → `nemicli`. With `nemicli --sag "…"`, the first message is sent immediately.

**Choose a model:** `/model`. A running Ollama instance appears automatically. Cloud: `/model` →
“Cloud-Anbieter hinzufügen” → enter the key (stored encrypted with DPAPI). Providers:
Anthropic, OpenAI, Google, Mistral, Cohere, Perplexity, DeepSeek, xAI, Groq, OpenRouter,
Ollama Cloud.

---

## ✨ What NemiCLI can do

### 💬 Chat & models
- **One schema for everything:** `anthropic:claude-…`, `openai:gpt-…`, `ollama:gemma4:12b`.
  Two engines (Anthropic native, OpenAI-compatible), one interface.
- **Models are queried live** from the provider – nothing hardcoded, new key = new models.
- **Own GGUF engine 🧠** (`ggufengine/`, `/model` → “Lokal (eigener Motor)”): local models
  without llama.cpp, Ollama, or a server – pure Python + torch **inside the NemiCLI process**, no port,
  no child process. Hardened GGUF reader (every number from the file is validated before use, the
  chat template from the file is never executed), weights stay quantized (Int4) in VRAM, CUDA
  Graphs. **Modular:** the model assembles itself from the GGUF file – Llama 2/3.x, Mistral, Mixtral,
  Qwen2/2.5/3 (including MoE), Gemma 1–4, Phi-3/4, Granite, OLMo 2, Qwen3.5, K2-Horizon and more run
  without model-specific code; plus **Ling 3.0** (`bailingmoe3`: Kimi Delta Attention + MLA + 128 experts,
  1.3B active – ~40–65 tokens/s); chat format and tokenizer rules are detected. Each model is a folder
  in `ModelGGUF/` inside the program folder (`ModelGGUF/Gemma4/…gguf`, with a `mmproj-*.gguf` next to it
  for the vision part).
  - **128k context** with Gemma-4-E4B at **~6 GB VRAM**: sliding-window layers keep only
    a fixed window (ring buffer), while the 1.8 GB per-layer embedding table stays in RAM.
  - **Reasoning text as intended by the model maker:** earlier reasoning text drops out of the conversation
    memory when the model template wants it that way (Granite 4.2, Qwen3.5, Gemma 4); Ling 3.0 keeps it.
    Memory therefore grows with answers, not with every reasoning trace.
  - **Prefix cache:** instructions and the existing conversation are read only once – follow-up turns
    answer after ~0.1 s instead of ~15 s. A checkpoint after the system prompt ensures that even a
    divergent conversation never rereads the instructions. It is also stored **on disk**
    (`Cache/gguf/<Modell>.safetensors`, data only): after a restart, the instructions do not need to be
    read again as long as model and instructions are unchanged.
  - **Fast ingestion:** long texts are read by the engine in blocks (Qwen3.5 DeltaNet in blocks,
    Int4 weights temporarily unpacked for ingestion) – NemiCLI's instructions on Qwen3.5-9B in ~5 s
    instead of ~32 s.
  - **Slim instructions:** local models receive ~5,600 instead of ~11,000 instruction tokens; details
    (internet, system watch, memory, helpers, tools) are read by the AI on demand with `anleitung_lesen`
    or NemiCLI supplies them automatically – when the matching keyword appears or on the first related tool use.
  - **Fewer hallucinations:** after a tool call, generation stops (no invented result appended afterward),
    and a **phrase brake** prevents the AI from repeating the same sentence from its own earlier answers –
    only at word boundaries, so typos are not introduced.
  - **Speed limit** `gguf_tok_s` (default **30 tokens/s**) – gentler on the GPU, which waits between steps.
    `gguf_denken` controls reasoning mode (off by default).
  - **`/kontext`**: choose 8k · 16k · 32k · 64k · 128k · max (the model limit) – including the VRAM
    used by the conversation memory for the active model (Gemma-4-E4B 128k ≈ 2 GB,
    Qwen3.5-9B 128k ≈ 4 GB). Stored as `gguf_kontext`, effective from the next message.
    If the selected level plus the model does not fit into free VRAM, the engine automatically chooses the
    largest one that fits and tells you. Conversation memory **grows with the chat** (4k steps), so a short
    chat only uses as much VRAM as it needs.
  - **`/kontext 8bit` · `/kontext 16bit`** (also in the menu): conversation memory in 8 bit uses roughly
    half as much VRAM per token (similar to `q8_0` in llama.cpp), but is slower in long chats
    (Granite 4.2 8B at 9k context: 22 instead of 37 tokens/s). Default 16 bit. After long ingestion, the
    engine releases its intermediate buffer again (~1 GB).
  - **While generating images,** the language model moves to RAM (~1 s), leaving the GPU entirely to the
    image model, then returns (~1 s) – the conversation continues without rereading.
  - **Vision 👁:** if a `mmproj-*.gguf` sits in the model folder, the model can see images
    (`bild_ansehen`, images in chat). The vision encoder is **detected automatically from the file** –
    nothing to configure:
    - **Gemma 4** (`gemma4v`): ViT with 2D RoPE, 3×3 pooling, up to 280 image tokens per image.
    - **Gemma 4 12B “Unified”** (`gemma4uv`): no vision encoder – 48×48 pixel blocks, LayerNorms,
      position tables, projection; the 12B reads the image tokens bidirectionally (like llama.cpp).
    - **Qwen3.5 / Qwen3-VL** (`qwen3vl_merger`): ViT with 2D RoPE, 2×2 merge, up to
      1024 image tokens per image (text on screenshots remains readable); M-RoPE in the language model
      (time, row, column) for image positions.

    The encoder stays in RAM and moves to the GPU only while encoding an image (<2 s). An image is
    read once and stays in the prefix cache.
  - **Image describer 🖼** for local models without vision (for example DeepSeek-R1-Distill): a separate
    `Vision/<Name>/` section in the program folder with a small vision model
    (Gemma-4-E2B + mmproj). It describes images as text – type, people and animals, objects with
    color and location, text verbatim – and answers follow-up questions through `bild_fragen`. The
    language model briefly moves to RAM for this. Only for the own engine, never for cloud models.
- **Ollama:** if it is running, it is there – models can be downloaded directly from NemiCLI
  (choose size, live list). Vision models are recognized and marked with 👁.
- **Visible reasoning:** reasoning models (DeepSeek, Kimi, Gemma …) show their thinking in compact form;
  **F2** opens the full reasoning text. `/staerke` controls reasoning level/budget.
- **Auto Strong** (`/auto`): a small Gemma answers, and for difficult questions the large model takes over automatically.
- **Status bar:** model · mode · chat no. · context traffic-light bar · cost · duration.

### ⚙ Actions – with confirmation
- **Tools:** read/write/edit/**copy** files, folders, search, **open** (image, document,
  media, or folder with the default program – never programs or scripts), PowerShell commands, create PDF,
  web (search · Wikipedia · read pages and PDFs – only the main content, long pages in parts –,
  only ~230 trusted domains), create/view images.
- **System queries** (`abfragen`): fixed, **read-only** queries – without confirmation and **without PowerShell**,
  all in Python inside the same process (psutil, Registry, wintrust, event log). `was`: `prozesse`,
  `prozess` (path, command line, parent, signature, connections), `verbindungen`, `dienste`, `autostart`,
  `aufgaben`, `software`, `signatur`, `hash` (SHA-256), `datei`, `registry`, `laufwerke`, `system`, `netz`,
  `ereignisse`, `nutzer`. One thing per call. **What** gets checked is not hardcoded, but lives in
  an instruction file in `Agenten/`.
- **Sandbox 🧪** (`code_ausfuehren`, `paket_installieren`, `/sandbox`): code executed by the AI runs
  inside a **Windows AppContainer** – the same isolation family used by Edge and Store apps. It has
  **internet**, but cannot see your files, the Registry, or other programs, and never gets
  admin rights; a job object limits memory and runtime. A file is copied into a dedicated run folder
  under `NemiSandbox/`. Packages are installed with `paket_installieren` (pip inside the container,
  package names from PyPI only) into the sandbox package folder – never into NemiCLI itself; tests run with
  `modul: pytest`. **Projects** are authorized only by you: `/sandbox freigeben <Ordner>` (code runs there,
  may read) or `… schreiben` (may also modify), `/sandbox entziehen <Ordner>` revokes it.
  Whole drives, the profile, Desktop/Documents/Downloads, system folders, and NemiCLI folders are blocked.
  `befehl` does not start Python, pip, uv, or pytest – those run only through the sandbox.
- **Schedule** (`zeitplan`): the personality registers itself in **Windows Task Scheduler** –
  “täglich 09:00”, “alle 2 stunden”, “wöchentlich montag 08:30”, “anmeldung”, “einmal 20.09.2026 14:00”.
  NemiCLI currently starts without a window, processes the task in read-only mode, and stores the answer as
  a report in `Berichte/`; on the next start you see the first line (“✅ Alles OK” / “⚠️ 2 Auffälligkeiten”).
  Creating and deleting ask you, viewing (`zeitplan_anzeigen`) does not. Only inside the `\NemiCLI\`
  folder of Task Scheduler – it never touches unrelated tasks. Registration uses `schtasks` with
  a task XML (no elevation, maximum 30 minutes, never twice at once) – without PowerShell.
- **System watch 🛡** (`/wache`, `tools/wache/`): a lightweight SIEM managed by NemiCLI itself.
  Runs as a background process with an icon in the taskbar (restarts when NemiCLI has another
  build or newer code – also in the exe; green calm · yellow open alerts ·
  red “real”). Three sensors (processes, network, files in Startup/Temp/Downloads), system inventory
  on start and every 6 h (new service/startup/listener stands out), 14 rules (R001–R014), its own
  **Isolation Forest** without scikit-learn – first training at 200 events, then every
  200 automatically. If something fires, the active personality is woken (`Agenten/wache_alarm.md`),
  checks with `abfragen`, judges (`wache_bewerten`: harmless gets muted, real stays red – individually,
  multiple ids at once, or all from one rule; identical alerts are merged into one entry with a counter,
  3× harmless → the watch stops reporting that pair) and may tune things
  **within your limits** (`wache_justieren`: threshold, cooldown, mute rule – every
  change with justification in the log, `/wache rueckgaengig`). Report in `Berichte/Wache_….md` –
  the three newest remain as files, older ones continue to live as vectors in memory (recycle bin 30 days).
  `/wache selbsttest`: 7 of 7 attack patterns, 2% false positives on synthetic everyday activity.
- **Floating orb 🟢** (`/wache kugel an|aus`): when no terminal is open, the personality floats as a
  small glowing orb on the desktop – green/yellow/red like the shield, movable, greets 2–3× a day
  (`Wache/gruesse.md`, it may write that itself). Click opens a chat window in terminal style
  (speech bubbles, Markdown, images inline): talk, receive images, 📸 screenshot that it can see. Read
  and talk only – modifications happen in the full NemiCLI. Orb, window, and shield are Qt (PySide6).
  **Its own face:** if `Persoenlichkeiten/<Name>.png` (transparent) exists, that image floats instead
  of the orb; `<Name>_froh.png`, `_ernst.png`, `_denkt.png` … are moods. And it controls this itself
  (action `kugel`): mood, speech bubble, gesture (jump/wiggle/nod), corner, hide – within limits
  (at most every 10 min speaking on its own, at night only important things). **`/kugel malen`**:
  it describes itself – its personality file says what it looks like, exactly that way –, the image
  engine paints four moods with a fixed seed, rembg cuts them out – done. Prompt style follows the engine:
  SD 1.5/SDXL keywords with negative prompt, Krea 2 full sentences without negative prompt.
- **Instructions** (`Agenten/*.md`): work instructions the personality follows by itself. Every
  `.md` there appears from the next answer onward in the prompt (path + first line); if one matches,
  it reads and follows it. Nothing hardwired – new file, new capability.
- **Read-only actions run immediately, modifying ones ask** (Ja · Ja & nicht mehr fragen · Nein).
  For file changes, the review window shows the complete file – **F8 approves**, Enter never does.
  On a simple gray recessed surface; for existing files as a **Before/After comparison** with
  line numbers: **green `+`** newly written or added, **red `−`** removed or corrected,
  unchanged text dimmed. Header with target and orange warning when overwriting.
- **Risk levels** (since 19.09.2026, Lara's report): deleting, PowerShell outside the allowlist,
  creating scheduled tasks, and moving whole folders are marked **red**, without an “Always” option;
  an unknown command asks **twice**. Harmless changes from one round (create folder, rename …)
  are grouped into **one** combined confirmation – to prevent blind click-through.
- **Command allowlist** (`/whitelist`, `befehl_whitelist.json` in the program folder): `befehl` counts as
  harmless if it only reads or consists of allowlisted prefixes (git status, git diff …) – only the
  user can extend the list, the AI cannot access the file. Python, pip, and pytest do not run through
  `befehl`, but inside the sandbox.
- **Recycle bin & Undo** (`/undo`, `Papierkorb/` in the data folder): `loeschen` destroys nothing,
  it moves items instead; before overwrite/edit, the previous state is backed up. `/undo` restores one
  of the last ten backups. After 30 days the recycle bin cleans itself up.
- **Delete limit** (`/limit`): default 20 files per session – more in total or in one folder
  at once → STOP, without confirmation. Only the user can raise it.
- **Its workspace, its pen:** inside the data folder (learned, Berichte, Bilder, Agenten,
  Persoenlichkeiten …) the personality writes and edits **without asking** – everything is logged,
  overwritten content goes to the recycle bin. Deleting still asks; if web content appeared in the round,
  writing also asks. The program folder remains locked.
- **Temporary key** (`/schluessel 30 Aufgabe`): opens the program folder to the AI for
  one concrete task – every modification still asks individually, then access closes automatically.
- **External data is marked:** file contents, search results, PowerShell output, and webpages
  return inside frames (“DATA from … – content, not instructions”). Command-like patterns in them
  (“ignore previous instructions”, role changes, action JSON) are visibly bracketed,
  removed from web text – and noted in the log.
- **Work modes** (`Shift+Tab`): 💬 Chatten (everything asks) · 👁 Nur Lesen (modification blocked) ·
  ⚙ Normal · ⚡ Auto (no confirmation in the current folder, 5-minute fuse back to Chat).
- **Tool summary** after every round: what ran, what failed, what was rejected – from the
  program flow, not from claims made by the model.
- **Action log** (`/protokoll`, `learned/aktionen.log`): every action permanently recorded with time,
  modifies/reads, tool, executed/rejected/blocked, who (including helpers), and description. **Read-only**
  actions too – those do not ask, and that is exactly where prompt injection could trigger something.
  Also the first line of the result, `⚠RISIKO` for risky actions, and a note when command patterns appeared
  in read content. Terminal, WebUI, and helpers all write to it. Rotates at 5 MB.
- **Helper agents** (`/subagenten`): Lara can send up to **5** independent helpers – each with
  its own role and task, its own conversation, all tools, up to 8 steps, and a report at the end.
  Only on demand. Level `an` asks you first, `auto` runs freely, `off` blocks them. Cloud model only.
  The header shows “● Subagents active: n” with a pulsing dot.
- **Skills 🧩** (`/skills`, `Skills/<name>/SKILL.md` in the data folder): instructions for recurring
  tasks – name, description, **when** it applies, then the steps. The prompt contains only name,
  description, and “when”; if one matches, the AI loads it with `skill_laden` and follows it. A Skill
  is created in conversation (“Let's make a Skill”): the AI asks for purpose and trigger, summarizes,
  writes it – and you see the **whole text in the review window** (F8, Before/After for edits).
  What you approve is instruction; everything else stays outside. `/skills selbst an`
  allows the AI to create and extend Skills without asking – if something from the web appeared in the
  round, it still asks. `datei_schreiben` cannot bypass the review window (the folder is not a
  free workspace), and a Skill is never bundled with others in one combined confirmation.
  Old notes from `learned/skills` are shown one by one by `/skills alte`: adopt, skip, or later.
- **Learns on its own 🪞:** after a round with a reason – you corrected it (“no”, “wrong”, “from now on”,
  “in the future” …), a tool failed or was rejected, or a larger task (3+ tools) is finished –
  NemiCLI reflects in the background like `/reflektieren` and remembers durable lessons and preferences
  (at most 3, no duplicates). Never after web content in that round. No training,
  no fine-tuning – just good note-taking. `/reflektieren auto an|aus`.
  - **Core memory:** preferences and lessons appear in **every** conversation prompt (newest first,
    at most 2,000 characters, with numbers) – not only when search happens to find them. Each chat gets
    a fixed snapshot so local models do not have to reread their instructions; new entries apply from
    the next chat (the running one already contains them in its history).
  - **Replace instead of append:** if a preference changes, the old entry is replaced (`merken` with
    `ersetzt`, in reflection `ERSETZE #12`/`VERGISS #12`); the old version moves to the archive.
  - **Validated before saving:** injected commands, invisible control characters, and whole documents
    do not enter memory.
  - **Endless context (AutoContextCleaner):** history grows until the context window is full – then
    the last 3 messages remain (`🧹 cleaned up`). Nothing is lost: the chat keeps everything in its file
    and search index, and before each message matching passages from the offloaded part are supplied as
    “Earlier in this chat”; the AI can also look things up itself with `gedaechtnis_suchen` and source
    `aktuell`. One strong cleanup instead of a little every round: afterward the prefix cache can keep
    reading a long stretch directly from memory again.
  - **Save before trimming:** before cleanup, NemiCLI saves in the background what was permanently
    important (facts, decisions, preferences) – never web or file content.
  - **Targeted Skill repair** (`skill_ausbessern`): change one spot instead of rewriting everything,
    with Before/After in the review window; after errors or corrections, the AI proposes the improvement itself.
- **Memory:** chats are saved (`/resume`), important facts remembered (`merken`),
  code snippets and short notes learned (`/wissen`), and before every answer an embedding model
  searches for relevant memories (`learned/memory.db`, local, `/gedaechtnis`). The model
  (Qwen3-Embedding-0.6B) runs through `tools/embedder.py` directly on torch + transformers – on the
  **GPU if one is available** (config `embedding_geraet`: auto · cuda · cpu), otherwise CPU. After every
  answer, the **librarian** indexes in the background (chat, notes, Skills, reports → vectors) –
  visible as a 📚 bar in the status line without blocking chat; `/gedaechtnis indexieren`
  catches up any backlog in one go.
  - **Knowledge for all personalities:** put PDF, Markdown, and text into the `Wissen/` folder (including
    subfolders) – the librarian reads them fully, every personality can find them
    (`gedaechtnis_suchen` with source `wissen`) and sees the titles in the prompt. Identical sentences that
    already exist in an older file are added only once to memory; the files remain unchanged.
    Long documents are read by the AI in parts (`gedaechtnis_lesen` with `ab`).
- **Folder sense (ML):** a custom trainable classifier recognizes what kind of folder it is
  (Python project, images, music …) – shown in the ML bar, learns more with `ordner_lernen`.
- **Practice mode** (`/uebung`): while idle, NemiCLI builds and tests small programs in `NemiSandbox/`
  – every version must be approved by you with F8 before writing/executing; execution happens in the sandbox.
- **Workspace** (`/workspace`): pins a **project folder** – from then on it works **only there**
  (read, write, search, commands), preventing drift across twenty folders. The path appears **yellow** in
  the header. The project gets `.workspace/` with `memory.md`, `Absprache.md`, and `Dateien.md` –
  project memory stays with the project and moves with it, `merken` writes there instead of to
  `learned/`. It then reads `Agenten/workspace.md`: inspect folder → discuss → look up uncertainties →
  **present plan** → your approval → README, `venv` (never `.venv`, with verified version and
  active status), code. Memory and README are updated only after the code has run cleanly **three times
  in a row** **and** you are satisfied. Security takes priority. Only **you** can toggle it – it has no
  tool for that. `/workspaceend` releases it.
- **Work mode in Workspace:** it remains **the same person**, but works differently – factual,
  structured, concise. Like someone who speaks differently at work than at home. In the **agent loop**
  it works through an approved task on its own (plan → implement → test → verify → continue) and
  stops by itself when finished, blocked, or a decision belongs to you. **Your request takes priority:**
  no talking it to death, no moral lectures – one objection, then it builds it the way you want.
  **Reading-friendly communication** is part of the instruction: short sentences, one question at a time,
  lists instead of paragraphs, spelling never commented on, clarification with choices rather than open-ended
  questions. After `/workspaceend`, it is fully itself again – the mode belongs to the Workspace, not the personality.

### 👁 Vision & 🎨 image generation
- **Drag & Drop files:** drag a file from Explorer into the terminal, press Enter – done.
  Also works in NemiCLI's own window; emojis can be inserted there through the emoji field (**Win + .**) or paste.
  **Text, Markdown, source code, HTML/CSS, JSON, CSV, logs** (anything readable as text – extension does
  not matter, content decides) attach as data to the message; **PDF** is read with
  `pypdf`, page by page; **images in any format** PIL can open (TIFF, ICO, HEIC, PSD …
  are converted to PNG before sending). Multiple at once work too. Binary files (exe, zip,
  safetensors, gguf …) stay untouched, with a short notice. A **folder** arrives as a listing (one
  level, folders first, with sizes; `node_modules` & Co. only counted). File content counts like web content
  as **data, never instructions**; 12,000 characters per file – if shortened, it says *what* is missing
  (“characters 12,000–145,977 missing”), so you know whether `datei_lesen` is worth it.
- **View images:** type a path into chat or attach in WebUI – vision models can see it.
  Lara can also get images herself: `bild_ansehen` (file) and `bildschirm_ansehen` (screenshot,
  asks first). If the model cannot see, the small Qwen helper describes it. Ollama Cloud
  (for example `deepseek-v4.1-flash`) is recognized as vision-capable.
- **Image preview in chat:** generated or viewed images appear as a color-pixel block in the conversation.
- **Own Stable Diffusion pipeline:** SD 1.5 and SDXL (Pony, Illustrious …), sampling loop
  written from scratch (DPM++ 2M / Euler + Karras), long prompts without the 77-token limit, progress bar.
  Put checkpoint (`.safetensors`, for example from Civitai) into `Models/checkpoints/`, `/bildmodel` selects it.
  Faces and eyes are automatically sharpened afterward; `/bearbeiten` repaints marked areas.
- **Own Krea-2 pipeline** (`engines/krea.py`): Krea 2 directly in NemiCLI, without ComfyUI. Everything
  rebuilt by hand in torch – Qwen3-VL-4B as text encoder (12 tapped layers), the
  single-stream DiT with 28 blocks, flow-matching sampler (Euler a, “simple”, Shift 1.15), and the
  Qwen-Image VAE. Fixed settings: **CFG 1, no negative prompt**, 14 steps, 832×1216.
  The ComfyUI files are read directly: NVFP4 model (on Blackwell through the FP4 tensor cores,
  otherwise unpacked), FP8 text encoder, bf16 VAE. Memory like ComfyUI with `--disable-smart-memory`:
  each part is in VRAM only for its compute step, otherwise in RAM; **after 2 minutes without
  an image, RAM is cleared too** (`bild_krea_entladen`, seconds). Folder `Models/Krea2/`:
  model, `text_encoders/`, `vae/`, `tokenizer/` (vocab.json + merges.txt). Nothing is downloaded from the web.
  Krea is a **separate image engine** (`/bildmodel` → 🟣), separate from the SD pipeline:
  the SD side does not recognize Krea files as checkpoints, and when Krea is selected it never falls back to SD.
  ADetailer and `/bearbeiten` stay disabled with Krea – those are SD img2img. In the exe,
  torch comes from an installed Python (extlibs). RTX 5060 Ti: ~1.2 s/step, ~23 s/image.
- **ComfyUI** (`/bildmodel` → 🧩, default `127.0.0.1:8188`): its own integration beside Forge, because ComfyUI
  speaks a different protocol. Forge knows `--api` and then `/sdapi/v1/txt2img`; ComfyUI has **none**
  of those endpoints and returns 404 – which is why it used to appear “unreachable” even though the port
  was already there. NemiCLI now sends a real **workflow** (CheckpointLoader →
  CLIPTextEncode → KSampler → VAEDecode → SaveImage), gets the prompt id, waits for the result,
  and downloads the image. **Two layouts:** one checkpoint (everything in one file) OR separate –
  diffusion model + CLIP + VAE, as Krea/Qwen/Flux require. NemiCLI detects what exists and builds
  the appropriate workflow; it guesses the CLIP type (`krea2`, `qwen_image`, `flux2` …) from the filename,
  `bild_comfy_cliptype` overrides it. Separate models use the proven Krea values: 832×1216, 14 steps, CFG 1,
  euler_ancestral, simple – and no negative prompt, which would be ineffective at CFG 1.
  Samplers and schedulers are read from your instance, unknown names fall back gracefully.
  Defaults can be changed through `bild_comfy_*` in config. If ComfyUI does not know a checkpoint, it says so
  and points to `extra_model_paths.yaml`.
- **External Forge/A1111 WebUI** (`/bildmodel` → 🌐): for Qwen/Flux models the own pipeline
  cannot handle. Proven Krea setup: 14 steps, CFG 1, 832×1216, Euler a, fixed positive prefix,
  blocked words in code (`pov`, `1boy`, `group` …). Everything configurable via `bild_webui_*`.
  Only `127.0.0.1:7860`; NemiCLI does not start the WebUI.

### 🌐 Browser & 🖱 right-click
- **Chrome extension** (`chrome-erweiterung/`, `/chrome`): right-click on a webpage →
  **“Nemi antwortet”** – Lara reads the page, writes a reply in your name, and inserts it
  **directly into the text field**; “… und sendet” also presses the send button. **“Nemi, erklär mir das”**
  explains selected text. Talks only to the running NemiCLI on `127.0.0.1:9000`, with a secret key.
- **Windows right-click menu** (`/kontextmenue an`): on the desktop, “Bildschirm erfassen” and
  “Nemi antwortet (in das Fenster daneben)”, on images “Mit NemiCLI ansehen”. If NemiCLI is running,
  it lands there – otherwise a new window opens. Windows 11: under “Weitere Optionen anzeigen”.
- **WebUI** (`/webui`): reading-friendly browser interface for the running CLI – font (including
  OpenDyslexic), size, spacing, contrast themes, text-to-speech, attach images. Only `127.0.0.1` + token.
- **Games 🎲** (`/spiel`): chess, Nine Men's Morris, checkers, and TicTacToe in the browser – against the personality.
  A small dedicated server (unrelated to WebUI), large board, narrow chat beside it. Rules run
  in the browser; the personality receives the board and allowed moves, chooses itself, and talks about it.

### 🎭 Interface & personality
- **Desktop window** (`/gui`): a calm, dark interface for the eyes – pure PySide6, custom drawn,
  **no HTML, CSS, or JavaScript**. The terminal remains the engine: model, tools,
  memory, and chats run there; the window displays and sends input. Both see the same
  chat – what happens in the terminal appears in the window and vice versa; when the terminal exits,
  the window closes too. In NemiCLI's own terminal window, the terminal moves **into the system tray**
  while the desktop window is open (its own icon, separate from the watch; click or “Terminal zeigen”
  brings it back, “NemiCLI beenden” quits everything) and returns automatically when the desktop window
  closes. A second `/gui` brings the open window to the front.
  - At the top **Chat | Work**: *Chat* is pure conversation – only image generation/viewing and
    memory are allowed, commands and file modifications are blocked. *Work* can do everything the terminal can,
    with working folder and files; confirmations appear as cards with Erlauben/Ablehnen.
  - Sidebar with new chat, **Gallery** (images from `Bilder/`), **Skills** (cards for reading,
    “Neuen Skill besprechen”), **Personalities** (click switches for terminal and window) and recent chats;
    answers with Markdown and expandable reasoning, images directly in chat, files via **+** or Drag & Drop.
  - Connection through a Windows Named Pipe with no port, with a fresh secret key per session; only
    JSON, never pickle. Links from answers open only with `http`/`https`, nothing else is loaded.
- **Textual full-screen:** history above with real scrolling, input below. Resize the window – everything
  adapts. Select text with the mouse, **Ctrl+C** copies it (without borders). Clickable dialogs.
  Fall back to the old interface: `NEMICLI_TUI=alt`.
- **Code in chat with a frame:** fenced code in responses gets a header with **filename**
  (```` ```python run.py ````) or language, from four lines onward **line numbers**, syntax colors matching
  the theme, and a transparent background that blends into the terminal. Code is immediately distinct
  from prose – and on an error you can say “line 12”.
- **`F12` saves the whole conversation** as Markdown to `Gespraeche/` – every question, **every
  reasoning text**, every answer, every executed action, with chat number, model, personality, and
  working folder in the header. For anyone who finds selecting text in the terminal difficult: press once
  instead of dragging manually. The reasoning text is otherwise stored nowhere – F2 always shows only
  the last round. The files stay on the PC.
- **About you** (`/name`, bottom-left in the desktop window): name, how you want to be addressed,
  and anything you want the AI to know. It sits in the prompt directly after the personality –
  every personality follows it from the first message. Only you can change it.
- **One folder per personality** (`Profile/<Name>/`): chats, images (`<Name>_2026-09-29_14-35-12.png`),
  Skills, and its own lessons live with it – the folder name has no special characters. Shared by all
  are “About you”, facts and preferences about you, and the global Skills (`Skills/`, `fuer_alle`).
  The selected personality stays active until you switch; switching starts a new chat (the
  old one stays with that personality). Chats and images from before profiles are visible to all,
  nothing was moved.
- **Personalities** (`/persönlichkeiten`): Nemi is built in; create your own with the assistant or as a
  Markdown file in `Persoenlichkeiten/` (SillyTavern placeholders `{{char}}`, `{{user}}` …).
  Name, tone, and character are free – honesty, action logging, and system protection remain.
- **Themes** (`/theme`): cyan · matrix · amber · cyber. Mascot, emoji shortcuts, statistics (`/statistik`).

---

## ⌨ Commands

Type `/` – an autocomplete menu appears. `/help` shows all commands. Commands with subitems
(`/wache`, `/sandbox`, `/uebung`, `/modus`, `/theme`, `/staerke`, `/kugel` …) open **without an argument**
a selection – ↑/↓ or number 1–9, Enter selects, Esc goes back; if an item needs a value (folder,
minutes, number), it asks afterward.

| Command | What it does |
|---|---|
| `/model [name]` | Choose model (provider/local → model); enter cloud key; configure local/Ollama |
| `/staerke [stufe]` | Reasoning level/budget: `schnell` · `normal` · `stark` · `max` |
| `/modus [name]` | Work mode `chat` · `lesen` · `normal` · `auto` (also Shift+Tab) |
| `/name [zeigen\|loeschen]` | 👤 About you: name, preferred form of address, description – applies from the first message |
| `/persönlichkeiten [name\|neu]` | Who speaks? Menu, set, create new (also `/persona`) |
| `/subagenten [an\|auto\|off]` | Helper agents: ask · as needed · blocked |
| `/auto [an\|aus]` | Auto Strong: use the large Gemma for difficult questions |
| `/bild [prompt]` | Generate image – without text: status & checkpoints |
| `/bildmodel [name]` | Image model: own pipeline (SD / Krea 2) or external WebUI (`webui-adresse` changes the host) |
| `/bearbeiten [pfad]` | Retouch image: face/eyes automatically or mark an area |
| `/gui` | Desktop window (Chat · Work, Gallery, Skills, Personalities) – connected to this session |
| `/skills [selbst an\|aus \| alte]` | 🧩 Skills: list · allow self-creation · import old notes |
| `/webui` | Browser interface with reading aids |
| `/spiel` | Chess · Nine Men's Morris · checkers · TicTacToe against the personality (browser) |
| `/chrome [neu]` | Chrome extension: port and key (`neu` generates a new one) |
| `/kontextmenue [an\|aus]` | NemiCLI in the Windows right-click menu |
| `/resume [#]` · `/reset` · `/clear` | Resume chat · new chat · clear screen |
| `/wissen` · `/gedaechtnis [indexieren]` · `/reflektieren [auto an\|aus]` · `/aufraeumen` | Learned data · memories (+ catch up vector backlog) · reflect · compact |
| `/ml` · `/ordner [pfad]` | Folder sense: report · estimate folder type |
| `/uebung [an\|aus\|jetzt]` | Practice mode |
| `/sandbox [freigeben <Ordner> [schreiben]\|entziehen <Ordner>]` | 🧪 Sandbox: status, packages, authorize/revoke project folder |
| `/workspace [pfad]` · `/workspaceend` | 📌 Pin project folder – it works only there · release |
| `/start` | 📁 Where your NemiCLI folder lives – chats, images, models, learned data. On change it copies, never deletes |
| `/einrichten` · `/systemcheck` · `/selbsttest` · `/version` · `/update` | Setup · check PC · check modules · version · `git pull` |
| `/statistik [reset]` · `/protokoll [n]` · `/theme` · `/web` · `/emoji` · `/nemi` | Usage · action log · colors · allowlist · shortcuts · mascot |
| `/undo` · `/limit [zahl]` · `/whitelist [befehl]` | 🗑 Restore from recycle bin · 🛑 delete limit · ✅ command allowlist |
| `/schluessel <min> [aufgabe]` · `aus` | 🔑 Open program folder temporarily to the AI (every change asks) |
| `/wache [status\|alarme\|start\|stop\|…]` | 🛡 System watch: status, alerts, rules, tuning, self-test, autostart |
| `/kugel [malen [text]\|weg\|ordner]` | 🔮 Floating orb: have the personality's face painted · return to orb · folder |
| `/exit` | Exit |

**Keys:** `Enter` send · `Ctrl+J` new line · `Esc` cancel · `F2` reasoning text ·
`F8` approve change · **`F12` save entire conversation** · `Shift+Tab` mode ·
`Page Up/Down`, mouse wheel scroll · `Ctrl+C` copy selected text.

**Without UI:** `--version` · `--selftest` · `--systemcheck` · `--bild "a fox"` · `--sag "…"` ·
`--auftrag <name>` (scheduled task without a window, answer becomes a report).

---

## 🔒 Security

- **Modifying actions always ask** – unless you deliberately say “don't ask again” for this session.
- **System folders: inspect yes, modify never.** `C:\Windows`, `Program Files`, `ProgramData`, `Recovery`,
  `EFI`, `Boot`, and the system Registry may be **read** (`datei_lesen`, list, search, `abfragen` – for the
  system watch: signature of `svchost.exe`, startup in HKLM). **Modifying** them is blocked, and `befehl`
  may not even mention those locations. Detours (`..\..`, `%windir%`, `\\?\`) are resolved.
  `format`, `diskpart`, `vssadmin`, disabling antivirus, Base64/`iex` obfuscation: always blocked.
  A self-test checks 40+ bypass attempts. Your profile (`C:\Users\<user>`) remains accessible.
- **Custom locks** (`schreibsperre.json`): locations that belong to you but should still remain out of the
  AI's hands. Two levels – `pfade_absolut` (cannot even view) and `pfade`
  (may view, may not modify). The **program folder** is fixed at the absolute level: NemiCLI
  cannot rebuild itself. **Your** data folder is exempt – otherwise it could not access its own chats.
  The lock also applies to PowerShell commands, and every mentioned path is checked individually:
  `Copy-Item <free> <locked>` does not get through.
- **No PowerShell during normal operation.** System queries, clipboard, schedule, and GPU detection run
  in Python or through fixed Windows tools. PowerShell is only started by `befehl` – after your confirmation.
- **No admin rights for the AI.** `befehl` rejects every elevation route (RunAs, runas, sudo, gsudo,
  psexec); if NemiCLI itself is running as Administrator, the AI executes no commands. There is no admin exe.
- **`befehl` blocks system tools** for which the AI has no harmless purpose: launching programs through
  Windows hosts (mshta, rundll32, regsvr32, wscript, cscript, msiexec …), downloading and decoding
  (certutil, bitsadmin, Start-BitsTransfer), clearing logs, startup entries (Run keys,
  Startup folder), hidden windows, hiding or unblocking files, creating services,
  changing the firewall. Python, pip, uv, and pytest run only through the sandbox.
- **Security software is off-limits.** NemiCLI reads which antivirus and firewall are installed from
  Windows Security Center; the AI does not inspect their folders – no signatures, processes,
  files, or commands related to them. They are known and treated as part of the system.
- **Signed and verifiable:** exe files with Authenticode signature (`CN=NemiCLI`) and SHA-256 checksums
  in `SHA256SUMS.txt` beside the exe.
- **Background runs** (schedule) are fixed to “Nur Lesen” mode: modifying actions are blocked and every
  confirmation automatically becomes No. Nothing can therefore be written “by accident” at night.
- **Network only through HTTPS and allowlist**, protection against SSRF (every redirect is checked before
  requesting it, including domains that resolve through DNS into the local network) and prompt injection –
  web content, page text from Chrome, and memories count as data in the prompt, never instructions.
- **Everything listening on a port listens only on `127.0.0.1`** – WebUI, Chrome receiver, Ollama,
  ComfyUI. Never `0.0.0.0`, no option for it; a test watches this. Access only with token/key.
- **API keys** are stored encrypted in `.env` (Windows DPAPI, tied to your account), display is masked.
- **Helper agents** are subject to the same rules as the main agent; only one asks at a time.
- **Personalities** are only prompt text – protection lives in code and applies to every personality.

---

## 📁 Folders

Since 15.09.2026, **program and data are separate**. The program can move, be rebuilt, or be packaged
as an exe – your memory does not move with it.

**The program** (`core/paths.py` calls it `INSTALL`):

```
NemiCli/
├─ main.py               Chat loop, action logic, commands, startup
├─ start.bat             Starter – checks Python, venv and packages, installs missing parts
├─ core/                 Persona/rules, personalities, commands, modes, config, keys,
│                        setup, paths.py (where things live), reich.py (folder window)
├─ engines/              Engines: Anthropic, OpenAI-compatible; models, providers,
│                        image pipeline, Krea 2, Forge WebUI, ComfyUI, systemcheck, reasoning levels
├─ tools/                Tools: actions, web, vision, helpers, memory, embedder,
│                        PDF, WebUI, right-click menu, Chrome receiver, practice mode, statistics
├─ ui/                   Interface: themes/panels, Textual full-screen (screen_tx.py), old TUI,
│                        edit window, mascot, desktop window (gui_*.py)
├─ ggufengine/           own GGUF engine (reader, Int4 layers, tokenizer, models)
├─ ModelGGUF/            local language models, one folder per model (not in Git)
├─ Agenten/              instructions the personality follows itself (workspace.md)
├─ chrome-erweiterung/   the Chrome extension (LIESMICH.md)
├─ tests/                Offline tests (python -m unittest discover -s tests)
├─ docs/                 TechnischeFunktion.md, Plan_Haertung_Bitdefender.md
├─ build_exe.py          builds the exe, signs it, writes SHA256SUMS.txt
├─ zertifikat.py         creates the CN=NemiCLI signing certificate once
├─ nemicli.config.json   Settings · .env  keys (encrypted)
├─ schreibsperre.json    locations the AI must not touch
└─ venv/ · .runtime/     Working environment, isolated Python
```

**Your data** (`DATEN` – choose the location with `/start`; without a choice it stays with the program):

```
<your folder>/
├─ chats/                Conversations (/resume)
├─ Gespraeche/           conversations saved with F12, including reasoning text
├─ learned/              Memory (memory.db), Skills, statistics, action log
├─ Bilder/               generated images, Screenshots/
├─ Models/               checkpoints/ for images, Krea2/ for Krea 2, embeddings/ for memory (safetensors or .gguf, choice with /embeddings)
├─ Persoenlichkeiten/    custom personalities (.md)
├─ Skills/               Skills for all personalities (<name>/SKILL.md)
├─ Profile/<Name>/       per personality: Chats/, Bilder/, Skills/, Erinnerungen/
├─ NemiSandbox/          Sandbox: run folders, _pakete/ (packages installed inside the sandbox)
├─ Befehle/              persistent helper scripts
├─ Zeitplan/             tasks for Windows Task Scheduler (<name>.json)
├─ Berichte/             findings from background runs (+ log)
└─ Vorschläge/           suggestions created by the personality
```

If the configured folder is unavailable at startup (drive disconnected), **NemiCLI tells you**
instead of silently starting with an empty memory.

---

## 🛠 Requirements

- **Windows 10/11**, **Python 3.10+** (or `start.bat` tells you what is missing)
- Packages: `anthropic`, `openai`, `httpx`, `rich`, `textual`, `prompt_toolkit`, `python-dotenv`
- Local models in the own engine: only **torch** (CUDA) – same as for images; no numpy, no
  compiler, no server
- Local models through **Ollama** ([ollama.com/download](https://ollama.com/download)) – brings its
  own compute engine
- Memory search: `torch`, `transformers<5` – with the CUDA build of torch the encoder runs on
  the GPU (~50× faster), the CPU build works too
- System watch with orb, chat window, and tray: `psutil`, `watchdog`, `PySide6-Essentials`
- `/kugel malen` (background removal): `rembg[cpu]` – downloads a 176 MB model to `~/.rembg` on first use
- Image generation: `torch` (CUDA), `diffusers`, `transformers<5`, `pillow`, `opencv-python<5` + checkpoint
  – **or** a running ComfyUI / Forge WebUI, then none of those are needed
- Krea 2: `torch` (CUDA), `safetensors`, `tokenizers`, `pillow`, `numpy` – no diffusers, no
  transformers. Fastest path (NVFP4 on tensor cores) from RTX 50xx onward; ~20 GB free RAM
- Drag PDF into chat: `pypdf`
- Chrome extension: Chrome, load once as “Entpackte Erweiterung laden”

---

## 🗺 Roadmap

- [x] Textual full-screen, helper agents, image preview, screenshots, right-click menu, Chrome extension
- [ ] `NemiCLI-Setup.exe` (Inno Setup): Next-Next-Done, Start menu, uninstall
- [ ] LoRA support for the image pipeline
- [ ] `pip-audit` in self-test (check packages against the vulnerability database)
- [x] Background memory ingestion without blocking input (20.09.2026: librarian with 📚 bar)
- [x] Hardening: no PowerShell in normal operation, no admin rights for the AI, signature + SHA-256 (25.09.2026)
- [x] Real sandbox for AI code (Windows AppContainer) with packages and project authorization (25.09.2026)
- [x] Desktop window `/gui` (PySide6, without HTML/CSS/JS), terminal stays in tray (28.09.2026)
- [x] Skills with review-window approval, web reading with validated redirects and chunking (28.09.2026)
- [ ] Move `Agenten/workspace.md` into the data folder (`/workspace` looks for it there)
- [ ] Signature with a recognized certificate if NemiCLI is distributed to others
- [ ] Submit build to Bitdefender false-positive form

The journal of all changes is in [CHANGELOG.md](CHANGELOG.md).
