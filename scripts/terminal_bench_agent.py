"""Custom terminal-bench agent: Claude Code authenticated via OAuth (Pro plan).

The built-in ClaudeCodeAgent hard-requires ANTHROPIC_API_KEY. We don't have an
API key — we have a $20 Pro OAuth token. Claude Code accepts that token via the
CLAUDE_CODE_OAUTH_TOKEN env var (verified: claude -p returns inside a container
with only that var set). So we override _env to inject it.

`claude -p` in print mode does not persist a ~/.claude/projects transcript, but it
emits the full stream-json (incl. the final result/usage event) on stdout. We
redirect that to /logs (the dir terminal-bench bind-mounts to the host run's
sessions/ dir) so it survives container teardown, then profile it with
`cc_trace live -`.

Usage:
    export CLAUDE_CODE_OAUTH_TOKEN=$(security find-generic-password \\
        -s "Claude Code-credentials" -w | python3 -c \\
        'import json,sys;print(json.load(sys.stdin)["claudeAiOauth"]["accessToken"])')
    PYTHONPATH=scripts tb run -p <dataset> -t <task> \\
        --agent-import-path terminal_bench_agent:OAuthClaudeCodeAgent
    # then, per task:
    cat <run>/.../sessions/cc.stream.jsonl | python3 -m cc_trace live - -o out.html --json
"""

import inspect
import os
import tempfile
from pathlib import Path

from terminal_bench.agents.installed_agents.claude_code import claude_code_agent
from terminal_bench.agents.installed_agents.claude_code.claude_code_agent import (
    ClaudeCodeAgent,
)
from terminal_bench.terminal.models import TerminalCommand
from terminal_bench.utils.template_utils import render_setup_script


class OAuthClaudeCodeAgent(ClaudeCodeAgent):
    @staticmethod
    def name() -> str:
        return "claude-code-oauth"

    @property
    def _env(self) -> dict[str, str]:
        env = {
            "CLAUDE_CODE_OAUTH_TOKEN": os.environ["CLAUDE_CODE_OAUTH_TOKEN"],
            "FORCE_AUTO_BACKGROUND_TASKS": "1",
            "ENABLE_BACKGROUND_TASKS": "1",
        }
        if self._model_name:
            env["ANTHROPIC_MODEL"] = self._model_name.removeprefix("anthropic/")
        elif "ANTHROPIC_MODEL" in os.environ:
            env["ANTHROPIC_MODEL"] = os.environ["ANTHROPIC_MODEL"]
        return env

    @property
    def _install_agent_script_path(self) -> Path:
        # The .j2 template ships in the original claude_code package dir, not
        # next to this subclass file, so resolve it from there.
        template_path = (
            Path(inspect.getfile(claude_code_agent)).parent
            / "claude-code-setup.sh.j2"
        )
        script_content = render_setup_script(
            template_path, self._get_template_variables()
        )
        temp_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".sh", delete=False
        )
        temp_file.write(script_content)
        temp_file.close()
        os.chmod(temp_file.name, 0o755)
        return Path(temp_file.name)

    def _run_agent_commands(self, instruction: str) -> list[TerminalCommand]:
        # claude -p in print mode does NOT persist a ~/.claude/projects transcript,
        # but it emits the full stream-json (incl. the final result/usage event) on
        # stdout. Redirect that to the host-mounted /agent-logs dir so cc_trace's
        # `live` mode can profile it cleanly (the tmux pane wraps at 160 cols and
        # corrupts the JSON, so we must capture real stdout, not the pane).
        # /logs is the dir terminal-bench bind-mounts to the host run's sessions/
        # dir (it's where asciinema writes agent.cast), so writes there survive
        # container teardown. /agent-logs is NOT mounted by default.
        base = super()._run_agent_commands(instruction)[0]
        cmd = (
            "mkdir -p /logs; "
            + base.command
            + " > /logs/cc.stream.jsonl 2>/logs/cc.stream.err"
        )
        return [
            TerminalCommand(
                command=cmd,
                min_timeout_sec=base.min_timeout_sec,
                max_timeout_sec=base.max_timeout_sec,
                block=True,
                append_enter=True,
            )
        ]
