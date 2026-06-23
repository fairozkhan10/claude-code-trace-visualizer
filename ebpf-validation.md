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
