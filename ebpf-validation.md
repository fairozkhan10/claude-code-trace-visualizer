# eBPF Validation: string-parser vs. kernel ground truth

Validating `cc_trace`'s best-effort inference (it parses the Claude Code
transcript, including Bash command *strings*, to guess file/network I/O) against
**AgentSight**, an eBPF tracer that observes the same run at the syscall + TLS
layer.

## Benchmark run

| | |
|---|---|
| Repo | `mahmoud/boltons` (pure-Python, 30 modules, zero deps) |
| Task | "Find every Python file under `boltons/` that defines a class whose name ends in `Error`; write `filepath: ClassName` lines to `errors_found.md`. Use grep and read the files to confirm." |
| Transcript | `…/projects/-home-renkylo256-boltons/4c1f6e5a-….jsonl` |
| AgentSight DB | `~/boltons/agentsight-20260623-054656.db` |
| Wall clock | parser: 26 s · eBPF capture: 27 s |
| Model (from transcript) | `claude-opus-4-8` |

Result was correct: 6 `*Error` classes across 5 files, written to
`errors_found.md`.

> **Scope of this report.** This is a **single-task pilot (n = 1)** that proves
> the tracing rig works. The only signal *positively* validated against kernel
> ground truth is **task file writes**. File reads, task-initiated network, and
> token usage were **not** validated here (see §5 and the caveats) — do not read
> "the parser is exact" as more than "exact on writes, on one task."

Sources compared:
- **Ours** — `file_access`, `network_activity`, `repeated_work` in `/tmp/ours.json`
- **Truth** — `/tmp/agentsight-audit.json` (process/file/llm audit events)
- **LLM** — `/tmp/agentsight-prompts.json` + `agentsight report token`

---

## 1. Files

### Writes — eBPF gives true ground truth (precision/recall computable)

AgentSight captured **13 raw file-write targets**. All but one are
agent-internal plumbing, not task I/O:

| eBPF write target | task-relevant? |
|---|---|
| `…/boltons/errors_found.md.tmp.3421.6c231e…` (atomic rename → `errors_found.md`) | ✅ yes |
| `…/projects/…/4c1f6e5a-….jsonl` (the transcript itself) | internal |
| `…/.cache/claude-cli-nodejs/…/mcp-logs-claude-design/…jsonl` | internal |
| `/tmp/claude-1001/…/tasks/bn9oapdy0.output` | internal |
| `/dev/tty`, `/dev/null` (×7) | internal |

Our parser reported exactly one write: **`errors_found.md`**.

| Metric (task-level writes) | Value |
|---|---|
| Ground-truth task writes | {`errors_found.md`} |
| Parser writes | {`errors_found.md`} |
| **Precision** | **1/1 = 1.00** |
| **Recall** | **1/1 = 1.00** |

The parser neither missed nor hallucinated a task write. It also correctly
*omitted* every agent-internal write (transcript, mcp-logs, tty/null) — eBPF
over-captures these, the parser models only task-level I/O. (eBPF saw the
`.tmp.<pid>` staging file; the parser reports the logical destination
`errors_found.md`. Same file, different altitude — not an error.)

### Reads — not validatable here (eBPF didn't surface read targets)

The parser inferred **5 source reads** (+ one directory):

```
boltons/socketutils.py   (read ×2)
boltons/urlutils.py
boltons/excutils.py
boltons/iterutils.py
boltons/dictutils.py
boltons/               (the grep target directory)
```

AgentSight's audit captured **file *writes* only** — no read events — and its
`exec` records carry the binary path but **not arguments** (e.g. `grep` is
logged as `/usr/bin/grep`, no pattern/paths). So there is **no syscall-level
read ground truth** to score recall against in this capture.

What we *can* say: the 5 inferred read files are exactly the files containing
the 6 `*Error` classes in the final answer, so they are real and consistent —
no evident hallucination. One soft imperfection: the parser lists `boltons/`
(a **directory**, the grep target) as a `file_access` entry alongside real
files.

---

## 2. Network

| | Ours (parser) | AgentSight (eBPF/TLS) |
|---|---|---|
| Endpoints | **0** | **8** (25 events) |

AgentSight's 8 endpoints, all HTTPS, decrypted via TLS interception:

| Endpoint | calls | nature |
|---|---|---|
| `api.anthropic.com/v1/messages?beta=true` | 5 | model completions |
| `api.anthropic.com/v1/design/mcp` | 5 | MCP (claude-design) |
| `api.anthropic.com/api/event_logging/v2/batch` | 3 | telemetry |
| `http-intake.logs.us5.datadoghq.com/api/v2/logs` | 2 | **3rd-party telemetry** |
| `…/api/claude_cli/bootstrap?…model=claude-opus-4-8` | 1 | startup |
| `…/api/claude_code_grove` | 1 | telemetry |
| `…/api/claude_code_penguin_mode` | 1 | telemetry |
| `…/api/eval/sdk-zAZezfDKGoZuXXKe` | 1 | telemetry |

**Interpretation.** The task itself made *zero* external network calls (it used
local `grep`/`find`/`git`). Every one of the 8 endpoints is the **agent's own
control plane** — model API, MCP, and telemetry — which is invisible to a
transcript parser by construction. So:

- As a measure of **task-initiated** network, the parser's `0` is **correct**
  (precision = N/A, no false positives).
- As a measure of **all** network the process touched, parser recall = **0/8** —
  but this is a category `cc_trace` cannot observe from a transcript, not a parser
  bug.

Headline: eBPF surfaces traffic the transcript can't, including an **external
Datadog logs endpoint** that neither the transcript nor our parser exposes.
That is the clearest standalone value of the eBPF layer here.

---

## 3. Process fan-out (eBPF-only)

The parser sees **logical Bash tool calls**; eBPF sees the **real process tree**:
**43 execs**, including

```
grep ×3, find ×2, xargs ×2, cat ×4, sed ×2, awk, head ×3, base64 ×4,
tr ×2, cut, git ×4, ps, bash ×2, sh ×2, locale, run-parts, claude.exe ×3
```

A single "run a grep pipeline" Bash tool call expands into
`bash → grep | head | sed …` plus locale/`run-parts` shell setup and several
`git` invocations the model never explicitly issued. The transcript parser
cannot and does not see this; it's pure eBPF value for understanding actual
resource use.

---

## 4. Repeated work

The parser flagged **1 redundant call**: `boltons/socketutils.py` read twice
(2 reads, 1 distinct, `exact=true`), redundant fraction 0.125 over 8 tool calls.
eBPF can't corroborate this (no read events captured), so the **transcript is
authoritative** for this signal — it's a parser-only capability, not contradicted
by ground truth.

---

## 5. Model-API calls & tokens

| | Count | Tokens |
|---|---|---|
| **eBPF — `/v1/messages` calls observed** | **5** | — |
| **eBPF — LLM requests fully parsed** (`report prompts`) | 1 | `in=0 out=0 total=0` |
| **eBPF — `report token`** | — | **empty** |
| **Transcript (our parser)** | — | in **6 226** · out **8 884** · cache-read **152 345** · cache-write **31 916** · **$1.587** |

**Token counts: eBPF produced none in this environment.** `claude.exe` is a
**statically-linked BoringSSL** binary (`BoringSSL detected! Attaching by
offset…`); AgentSight reconstructs the **request** side but not the **response**
bodies, where the `usage` block lives — so `input/output_tokens` are `0`,
`status_code` is null, and `report token` is empty. Only **1** of the 5
`/v1/messages` calls was reassembled into a prompt record, and it was the small
`claude-haiku-4-5` **title-generation** side-call, not a main `claude-opus-4-8`
completion.

**Conclusion for tokens — the TLS capture did NOT deliver token visibility.**
This was half the point of the eBPF approach (an *independent* check on the
decode-dominance story), and it failed here. `claude.exe` **statically links
BoringSSL**, so AgentSight's `SSL_read` uprobe could not reassemble the response
bodies that carry the `usage` block: only **1 of 5** `/v1/messages` calls was
partially recovered, and that one was the tiny Haiku **title-generation**
side-call — `0` tokens, no main `claude-opus-4-8` completion. `report token` is
empty.

So the **decode-dominance blind spot remains open**: token/cost numbers in
`/tmp/ours.json` still rest solely on the transcript's **self-report**, with no
independent kernel- or wire-level corroboration. SSL uprobes against a
statically-linked binary are the wrong tool for this. The reliable path to
independent token ground truth is a **MITM proxy** — point
`ANTHROPIC_BASE_URL` at a local `mitmproxy` and read `usage` from the decrypted
HTTP/2 bodies — not uprobes. That should be a **separate** capture, tracked
apart from this eBPF validation.

---

## Summary scorecard

| Signal | Parser vs. eBPF | Verdict |
|---|---|---|
| Task file **writes** | precision 1.00, recall 1.00 | ✅ parser exact |
| Task file **reads** | no eBPF read ground truth | ⚠️ unvalidatable; parser self-consistent |
| **Network** (task) | both 0 external task calls | ✅ agree |
| **Network** (agent infra) | parser 0 / eBPF 8 | eBPF-only (by design) |
| **Process fan-out** | parser ~2 Bash calls / eBPF 43 execs | eBPF-only |
| **Repeated work** | parser 1 redundant read | parser-only (transcript-authoritative) |
| **Token usage** | parser full / eBPF 0 | transcript-only (BoringSSL blocks eBPF) |
| **API call count** | — / eBPF 5 `/v1/messages` | eBPF-only |

**Net (n = 1):** on the one signal eBPF could keep score for here — task file
**writes** — the string-parser is exact (no misses, no hallucinations). That is
the *only* positive validation; reads and task-network were never exercised.
eBPF's unique contributions are the **network endpoint inventory** (incl. a
3rd-party Datadog endpoint) and the **true process tree**. Token/cost accounting
remains **transcript-only and uncorroborated** — eBPF could not capture it
against the static-BoringSSL `claude.exe`, so the decode-dominance check is still
outstanding (needs a proxy).

## Caveats / reproducibility

- eBPF file audit surfaced **writes only**; `exec` events lacked **arguments** —
  both limit how much file-read recall can be scored. A capture that records
  `openat` reads + full `argv` would let us score read precision/recall directly.
- Token capture is blocked by static-BoringSSL response reassembly. Don't chase
  this with uprobes — the reliable path is a **MITM proxy**
  (`ANTHROPIC_BASE_URL` → `mitmproxy`), captured separately from this eBPF run.
- This is **n = 1**. A real precision/recall across all three op types needs a
  task that exercises **reads** and **task-initiated network** (e.g. a `pip
  install` / `curl`), plus an AgentSight config that emits **`openat` read
  events** and **full `argv`** on `exec` — none of which this capture had.
- Run: `./run-both.sh "<task>" ~/boltons` (uses `/usr/bin/sudo.ws` and an
  `env HOME=…` wrapper so the traced Claude finds credentials — see script
  header for why).

---

# Run 2 — full op-type validation (reads + writes + network)

Run 1 only scored **writes** (eBPF emitted no read events, and the task made no
task-initiated network). Run 2 closes both gaps: a fixture task that **reads
known files** *and* **makes a real outbound HTTPS call**, scored for
precision/recall across **all three op types**.

## Benchmark run

| | |
|---|---|
| Repo | `~/parsertest` (fresh fixture: `src/{alpha,beta,gamma,delta}.py`, `README.md`) |
| Task | (1) read `src/alpha.py`, `src/beta.py`, `src/gamma.py` and list each file's functions; (2) `curl -s https://raw.githubusercontent.com/mahmoud/boltons/master/LICENSE -o LICENSE.txt`; (3) write `summary.md` with the function names + first line of `LICENSE.txt`. Explicitly **not** `src/delta.py` (a decoy for read precision). |
| Transcript | `…/projects/-home-renkylo256-parsertest/8b233184-….jsonl` |
| AgentSight DB | `~/parsertest/agentsight-20260623-062938.db` |
| Wall clock | 21 s (eBPF capture) |
| Model | `claude-opus-4-8` |
| Harness | `./run-readnet.sh "<task>" ~/parsertest` |

Result was correct: all 9 functions listed, `LICENSE.txt` downloaded,
`summary.md` written. `src/delta.py` was never touched.

### Ground-truth sources (and a tooling caveat)

| op type | ground truth | how |
|---|---|---|
| **writes** | AgentSight file audit **+** `opensnoop-bpfcc` | AgentSight emits writes; opensnoop catches the rest (incl. curl's) |
| **reads** | `opensnoop-bpfcc -T -e -F` | AgentSight emits **no read events** — opensnoop is the only read ground truth |
| **network** | `opensnoop` (curl ran + wrote file) + downloaded content | see §"Network" below |

`agentsight record --help` confirmed there is **still no read-event / argv
flag**, so reads were captured independently with bcc-tools, attributed by
**process subtree** (traced `claude` pid 5144 + its `curl` child pid 5272) and
**repo path**, exactly as the Run-1 writes analysis filtered agent-internal noise.

> **`execsnoop-bpfcc` does not work on this kernel.** Its BPF program fails to
> compile (`static_assert(sizeof(struct filename) % 64 == 0)` — a bcc/kernel
> struct mismatch), so full-argv ground truth was unavailable. `opensnoop`
> compiles and runs fine. Argv-level cross-checks therefore came from
> AgentSight's **process audit** (command names only: `git ×6, cat ×5, base64
> ×4, grep ×4, claude.exe ×3, head ×3, bash ×2, env ×2`) plus opensnoop's
> per-file, per-pid attribution — sufficient to score all three op types without
> argv.

## Results — precision / recall

| op type | ground truth | parser | TP | FP | FN | **precision** | **recall** |
|---|---|---|---|---|---|---|---|
| **Reads** | alpha, beta, gamma, LICENSE.txt | same 4 | 4 | 0 | 0 | **1.00** | **1.00** |
| **Writes** | summary.md, LICENSE.txt | summary.md | 1 | 0 | 1 | **1.00** | **0.50** |
| **Network** (task) | raw.githubusercontent.com | raw.githubusercontent.com | 1 | 0 | 0 | **1.00** | **1.00** |

### Reads — precision 1.00 / recall 1.00

The parser inferred exactly the four files opened `O_RDONLY` by the `claude`
subtree (`src/alpha.py`, `src/beta.py`, `src/gamma.py`, and `LICENSE.txt` — the
agent read the download back to quote its first line). Crucially it did **not**
report the decoy `src/delta.py`, which opensnoop confirms was never opened: **no
hallucinated read**. This is the first time read recall has been scored against
kernel ground truth, and the string-parser is exact.

### Writes — precision 1.00 / recall 0.50 (one real miss)

The parser caught `summary.md` (the editor write, via its `.tmp.<pid>` atomic
rename) but **missed `LICENSE.txt`** — which `curl -o LICENSE.txt` created
(opensnoop shows `curl` pid 5272 opening it `O_WRONLY|O_CREAT|O_TRUNC`).

This is a **characterizable parser limitation, not a fluke.** `_bash_files`
models writes only via shell **redirects** (`>`, `>>`) and `tee`
(`parser.py:100-103`); it has no handling for commands whose **output flag**
names a file — `curl -o` / `wget -O` / `aria2c -o`, or `git clone <dir>`. So the
curl is recorded on the **network** axis (correctly) but its **file** side
effect is invisible. Note AgentSight's own writes-only audit *also* missed this
write (curl isn't `claude.exe`); only opensnoop caught it — exactly why the
independent read/write capture was added.

### Network — precision 1.00 / recall 1.00

The parser reported one task endpoint, `raw.githubusercontent.com`, from the
Bash command string — correct and complete.

**Why ground truth is opensnoop + content, not AgentSight here.** AgentSight's
TLS uprobe is pinned to `claude.exe` (`--binary-path`), and the eight endpoints
it captured are all `claude.exe`'s own control plane (`api.anthropic.com` ×N,
`http-intake.logs.us5.datadoghq.com`). The **task** call is made by **`curl`,
which links OpenSSL, not claude's BoringSSL**, so AgentSight never sees it. The
call is instead proven independently: `curl` (pid 5272) executed and wrote
`LICENSE.txt`, and that file contains the boltons license
(`Copyright (c) 2013, Mahmoud Hashemi`) — i.e. the HTTPS GET to
`raw.githubusercontent.com` demonstrably succeeded. Parser endpoint matches
ground truth, no false positives.

## Run-2 scorecard

| Signal | precision | recall | Verdict |
|---|---|---|---|
| Task file **reads** | 1.00 | 1.00 | ✅ exact (incl. decoy correctly omitted) |
| Task file **writes** | 1.00 | 0.50 | ⚠️ misses `curl -o`/`wget -O` output files |
| Task **network** | 1.00 | 1.00 | ✅ exact |

**Net (Run 2).** Reads and task-network — both *unvalidated* after Run 1 — are
now scored against kernel ground truth and the parser is **exact** on each. The
one genuine gap is **writes created by a download command's output flag**
(`curl -o`), which the parser attributes to the network axis but not the file
axis; closing it is a small, well-scoped fix to `_bash_files` (treat `-o/-O`
output targets of net commands as writes). Tooling caveat for reproducers:
`opensnoop-bpfcc` is the workable read/write ground truth on this VM;
`execsnoop-bpfcc` will not compile against this kernel.

---

# Token ground-truth (MITM) — the check eBPF couldn't give

Run 1 left **token counts uncorroborated**: `claude.exe` statically links
BoringSSL, so AgentSight's `SSL_read` uprobe could not reassemble response
bodies, and the `usage` block lives in the response. The `/tmp/ours.json` token
numbers rested solely on the transcript's **self-report**, with no independent
wire-level check. A **MITM proxy** terminates TLS itself, so it reads the
decrypted body directly — the right tool where uprobes failed.

## Method

| | |
|---|---|
| Proxy | `mitmdump 8.1.1` on `127.0.0.1:8080`, headless, `block_global=false` |
| Addon | `mitm_token_addon.py` — parses the **SSE** stream of every `api.anthropic.com /v1/messages` response |
| Routing | `HTTPS_PROXY`/`HTTP_PROXY=http://127.0.0.1:8080`, `NODE_EXTRA_CA_CERTS=~/.mitmproxy/mitmproxy-ca-cert.pem` |
| Task | read `alpha/beta/gamma.py`, write per-function descriptions to `descriptions.md`, read it back |

**Claude Code accepted the proxy and the mitmproxy CA — no cert pinning, no
rejection.** Verified first on a trivial `claude -p "hi"` run, which produced a
clean decryptable `/v1/messages` flow with a usage block before the real task.

**The SSE trap (the thing that breaks a naïve `json.loads`).** `/v1/messages`
responses are `text/event-stream`, not one JSON body. Usage is split across
events: the **prompt-side** counts (`input_tokens`, `cache_read_input_tokens`,
`cache_creation_input_tokens`) arrive once in `message_start`; the **decode-side**
`output_tokens` arrives in `message_delta` (final cumulative). The addon walks
the `data:` lines and accumulates per event type. It records **only** model id +
the four usage integers — no prompt/response text, no account identifiers.

## What the wire showed (5 calls)

| # | model | input | output | cache-read | cache-create |
|---|---|---|---|---|---|
| 1 | `claude-haiku-4-5` (title-gen) | 575 | 14 | 0 | 0 |
| 2 | `claude-opus-4-8` | 2310 | 211 | 7891 | 2482 |
| 3 | `claude-opus-4-8` | 417 | 412 | 10373 | 2660 |
| 4 | `claude-opus-4-8` | 2 | 67 | 13033 | 876 |
| 5 | `claude-opus-4-8` | 2 | 156 | 13909 | 443 |

The `claude-haiku-4-5` call is the title-generation side-call — **real wire
traffic that never appears in the transcript** (consistent with Run 1's finding
that the transcript omits it). The four `claude-opus-4-8` calls are the main
agent loop. (mitm proxied 6 `POST /v1/messages` flows; the addon extracted usage
from 5 — the 6th was a connection-reuse retry on the same socket carrying no
distinct completion.)

## Result — wire corroborates the transcript *exactly*, and exposes a parser bug

The four wire opus calls reconcile with the transcript **to the token** — but
only after **deduplicating the transcript by message id**:

| | input | output | cache-read | cache-create |
|---|---|---|---|---|
| **Wire (opus, 4 calls)** | 2 731 | 846 | 45 206 | 6 461 |
| **Transcript, deduped by msg id** | 2 731 | 846 | 45 206 | 6 461 |
| **Parser `token_totals` (`/tmp/ours.json`)** | **9 661** | **1 479** | **68 879** | **13 907** |
| Parser inflation | **3.54×** | 1.75× | 1.52× | 2.15× |

So the **transcript's self-report is wire-accurate** — every per-message `usage`
block matches the decrypted response exactly. But `cc_trace`'s **aggregation
over-counts**: it sums `usage` per transcript *line*, and a single assistant turn
emits **one line per content block** (text + each `tool_use`), every line
repeating the *same* message-level usage. The first opus turn (msg id `gutSWqU8`)
had **4 content blocks → its usage was counted 4×**, inflating input tokens 3.5×.

**Cost.** Parser-reported `$0.620` vs. wire opus cost ≈ **`$0.293`** (≈2.1×
inflated, same root cause). And the independent split kills the "decode-dominant"
framing for this run: **output/decode is only ~22% of opus cost**; cache
creation (`$0.121`) and cache reads (`$0.068`) dominate. This is a
**cache/prefill-dominated** workload, not a decode-dominated one — exactly the
kind of claim that needed an independent source to settle.

## Verdict

- **MITM token capture works** where eBPF/BoringSSL uprobes failed: full
  `usage` recovered from the decrypted SSE stream, Claude Code does not pin certs.
- The transcript's **per-message** token numbers are **exactly correct** (wire-verified).
- `cc_trace`'s **token *totals* are inflated** by double-counting multi-block
  assistant turns — a real, well-scoped parser bug (dedup `usage` by `message.id`
  before summing). The decode-dominance story in `/tmp/ours.json` was an artifact
  of that over-count; on the wire this run is **cache/prefill-dominated**.

Reproduce: `mitmdump -p 8080 --set block_global=false -s mitm_token_addon.py`,
then run `claude` with `HTTPS_PROXY`/`HTTP_PROXY` → `127.0.0.1:8080` and
`NODE_EXTRA_CA_CERTS` → the mitmproxy CA.
