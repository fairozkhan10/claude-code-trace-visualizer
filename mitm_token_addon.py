"""mitmproxy addon: extract real wire token counts from Anthropic /v1/messages.

The independent token ground-truth eBPF could not give (claude.exe statically
links BoringSSL, so AgentSight's SSL uprobe can't reassemble response bodies).
A MITM proxy terminates TLS itself, so we read the decrypted body directly.

Critical trap: /v1/messages responses are streaming SSE (text/event-stream),
NOT one JSON body. Usage is split across events:
  - message_start  -> usage.input_tokens, cache_read_input_tokens,
                      cache_creation_input_tokens (the prompt side)
  - message_delta  -> usage.output_tokens (the decode side, final cumulative)
So we parse the SSE event stream line-by-line; json.loads on the whole body fails.

Writes one JSON line per /v1/messages response to $MITM_TOKEN_OUT
(default /tmp/readnet-out/wire-tokens.jsonl). Never logs prompt/response text or
any account identifier — only the model id and the usage integers.
"""
import json
import os

OUT = os.environ.get("MITM_TOKEN_OUT", "/tmp/readnet-out/wire-tokens.jsonl")


def _parse_sse_usage(body: str):
    """Return (model, usage_dict) summed from an SSE /v1/messages stream."""
    model = None
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        etype = obj.get("type")
        if etype == "message_start":
            msg = obj.get("message", {})
            model = msg.get("model", model)
            u = msg.get("usage", {})
            # prompt-side counts arrive once, in message_start
            for k in ("input_tokens", "cache_read_input_tokens",
                      "cache_creation_input_tokens"):
                if u.get(k) is not None:
                    usage[k] = u[k]
            if u.get("output_tokens"):           # sometimes a partial seed here
                usage["output_tokens"] = u["output_tokens"]
        elif etype == "message_delta":
            u = obj.get("usage", {})
            # output_tokens in message_delta is the final cumulative count
            if u.get("output_tokens") is not None:
                usage["output_tokens"] = u["output_tokens"]
    return model, usage


def response(flow):
    if "api.anthropic.com" not in flow.request.pretty_host:
        return
    if "/v1/messages" not in flow.request.path:
        return
    try:
        body = flow.response.get_text(strict=False) or ""
    except Exception:
        return
    model, usage = _parse_sse_usage(body)
    rec = {
        "path": flow.request.path.split("?")[0],
        "status": flow.response.status_code,
        "model": model,
        **usage,
    }
    with open(OUT, "a") as f:
        f.write(json.dumps(rec) + "\n")
