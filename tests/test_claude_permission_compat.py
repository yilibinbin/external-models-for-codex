import json
import os
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "claude-for-codex"
BACKEND = PLUGIN / "scripts" / "lib" / "claude-backend.mjs"
COMPANION = PLUGIN / "scripts" / "claude-companion.mjs"
MCP_GIT = PLUGIN / "scripts" / "lib" / "mcp-git.mjs"
NATIVE_REVIEW = PLUGIN / "scripts" / "lib" / "claude-native-review.mjs"


def node_eval(source, env=None, cwd=ROOT):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=cwd,
        env={**os.environ, **(env or {})},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_env_override_filters_to_known_deny_candidates():
    source = f"""
      import {{ configuredWriteDenyTools }} from {json.dumps(BACKEND.as_uri())};
      console.log(JSON.stringify(configuredWriteDenyTools({{
        CLAUDE_FOR_CODEX_DENY_TOOLS: "Edit,Write,Bash,NotATool,multiedit,EDIT"
      }})));
    """
    assert json.loads(node_eval(source)) == ["Edit", "Write", "Bash", "MultiEdit"]


def test_env_override_can_omit_multiedit_without_adding_unknowns():
    source = f"""
      import {{ configuredWriteDenyTools, formatDenyToolsForCli }} from {json.dumps(BACKEND.as_uri())};
      const tools = configuredWriteDenyTools({{
        CLAUDE_FOR_CODEX_DENY_TOOLS: "Edit,Write,Bash,UnknownTool"
      }});
      console.log(formatDenyToolsForCli(tools));
    """
    assert node_eval(source) == "Edit,Write,Bash"


def test_env_override_rejects_empty_effective_deny_list():
    source = f"""
      import {{ configuredWriteDenyTools }} from {json.dumps(BACKEND.as_uri())};
      try {{
        configuredWriteDenyTools({{ CLAUDE_FOR_CODEX_DENY_TOOLS: "UnknownTool,FutureWrite" }});
        console.log("unexpected-ok");
      }} catch (error) {{
        console.log(error.message);
      }}
    """
    output = node_eval(source)
    assert "did not match any supported write-deny tools" in output
    assert "Edit, Write, MultiEdit, Bash" in output


def test_unknown_deny_parser_accepts_only_pre_model_permission_failure():
    source = f"""
      import {{ parseUnknownDenyToolFailure }} from {json.dumps(BACKEND.as_uri())};
      const cases = [
        parseUnknownDenyToolFailure({{stdout: "", stderr: 'Permission deny rule "MultiEdit" matches no known tool'}}),
        parseUnknownDenyToolFailure({{stdout: "", stderr: 'Permission deny rule "MultiEdit" matches no known tool — check for typos.'}}),
        parseUnknownDenyToolFailure({{stdout: 'Permission deny rule "MultiEdit" matches no known tool — check for typos.\\n', stderr: ""}}),
        parseUnknownDenyToolFailure({{stdout: "Not logged in · Please run /login\\n", stderr: 'Permission deny rule "MultiEdit" matches no known tool — check for typos.'}}),
        parseUnknownDenyToolFailure({{stdout: "You've hit your session limit · resets 5:50am (Asia/Shanghai)\\n", stderr: 'Permission deny rule "MultiEdit" matches no known tool — check for typos.'}}),
        parseUnknownDenyToolFailure({{stdout: "model answer", stderr: 'Permission deny rule "MultiEdit" matches no known tool'}}),
        parseUnknownDenyToolFailure({{stdout: 'Permission deny rule "MultiEdit" matches no known tool\\nusage: input_tokens=1', stderr: ""}}),
        parseUnknownDenyToolFailure({{stdout: "", stderr: 'usage: input_tokens=1\\nPermission deny rule "MultiEdit" matches no known tool'}}),
        parseUnknownDenyToolFailure({{stdout: "", stderr: 'Permission deny rule "UnknownTool" matches no known tool'}}),
        parseUnknownDenyToolFailure({{stdout: "", stderr: 'Permission rule "MultiEdit" is not recognized'}})
      ];
      console.log(JSON.stringify(cases));
    """
    assert json.loads(node_eval(source)) == ["MultiEdit", "MultiEdit", "MultiEdit", "MultiEdit", "MultiEdit", None, None, None, None, None]


def test_cli_retry_omits_unknown_deny_candidate(tmp_path):
    fake_claude = tmp_path / "claude"
    log_file = tmp_path / "claude-args.jsonl"
    fake_claude.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env node
            const fs = require("fs");
            const args = process.argv.slice(2);
            fs.appendFileSync({json.dumps(str(log_file))}, JSON.stringify(args) + "\\n");
            const denyIndex = args.indexOf("--disallowedTools");
            const deny = denyIndex >= 0 ? args[denyIndex + 1] : "";
            if (deny.split(",").includes("MultiEdit")) {{
              console.error('Permission deny rule "MultiEdit" matches no known tool');
              process.exit(1);
            }}
            process.stdout.write("ALLOW\\n");
            """
        ),
        encoding="utf8",
    )
    fake_claude.chmod(0o755)

    result = subprocess.run(
        [
            "node",
            str(COMPANION),
            "review",
            "quick permission compatibility smoke",
        ],
        cwd=tmp_path,
        env={**os.environ, "CLAUDE_CODE_PATH": str(fake_claude)},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "ALLOW" in result.stdout
    assert 'rejected deny rule "MultiEdit"' in result.stderr
    invocations = [json.loads(line) for line in log_file.read_text(encoding="utf8").splitlines()]
    deny_lists = [
        args[args.index("--disallowedTools") + 1]
        for args in invocations
        if "--disallowedTools" in args
    ]
    assert deny_lists[0] == "Edit,Write,MultiEdit,Bash"
    assert deny_lists[-1] == "Edit,Write,Bash"
    tools_lists = [
        args[args.index("--tools") + 1]
        for args in invocations
        if "--tools" in args
    ]
    allowed_lists = [
        args[args.index("--allowedTools") + 1]
        for args in invocations
        if "--allowedTools" in args
    ]
    assert len(set(tools_lists)) == 1
    assert len(set(allowed_lists)) == 1
    assert all("--strict-mcp-config" in args for args in invocations)


def test_cli_retry_omits_unknown_deny_candidate_from_stdout(tmp_path):
    fake_claude = tmp_path / "claude"
    log_file = tmp_path / "claude-args.jsonl"
    fake_claude.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env node
            const fs = require("fs");
            const args = process.argv.slice(2);
            fs.appendFileSync({json.dumps(str(log_file))}, JSON.stringify(args) + "\\n");
            const denyIndex = args.indexOf("--disallowedTools");
            const deny = denyIndex >= 0 ? args[denyIndex + 1] : "";
            if (deny.split(",").includes("MultiEdit")) {{
              console.log('Permission deny rule "MultiEdit" matches no known tool — check for typos.');
              process.exit(1);
            }}
            process.stdout.write("ALLOW\\n");
            """
        ),
        encoding="utf8",
    )
    fake_claude.chmod(0o755)

    result = subprocess.run(
        [
            "node",
            str(COMPANION),
            "review",
            "quick stdout permission compatibility smoke",
        ],
        cwd=tmp_path,
        env={**os.environ, "CLAUDE_CODE_PATH": str(fake_claude)},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "ALLOW" in result.stdout
    assert 'rejected deny rule "MultiEdit"' in result.stderr
    invocations = [json.loads(line) for line in log_file.read_text(encoding="utf8").splitlines()]
    deny_lists = [
        args[args.index("--disallowedTools") + 1]
        for args in invocations
        if "--disallowedTools" in args
    ]
    assert deny_lists == ["Edit,Write,MultiEdit,Bash", "Edit,Write,Bash"]


def test_cli_retry_never_invokes_without_disallowed_tools(tmp_path):
    fake_claude = tmp_path / "claude"
    log_file = tmp_path / "claude-args.jsonl"
    fake_claude.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env node
            const fs = require("fs");
            const args = process.argv.slice(2);
            fs.appendFileSync({json.dumps(str(log_file))}, JSON.stringify(args) + "\\n");
            const denyIndex = args.indexOf("--disallowedTools");
            if (denyIndex < 0) {{
              process.stdout.write("UNSAFE EMPTY DENY LIST\\n");
              process.exit(0);
            }}
            const firstDeny = args[denyIndex + 1].split(",").filter(Boolean)[0];
            console.error(`Permission deny rule "${{firstDeny}}" matches no known tool`);
            process.exit(1);
            """
        ),
        encoding="utf8",
    )
    fake_claude.chmod(0o755)

    result = subprocess.run(
        [
            "node",
            str(COMPANION),
            "review",
            "quick permission compatibility smoke",
        ],
        cwd=tmp_path,
        env={**os.environ, "CLAUDE_CODE_PATH": str(fake_claude)},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )
    assert result.returncode != 0
    assert "UNSAFE EMPTY DENY LIST" not in result.stdout
    invocations = [json.loads(line) for line in log_file.read_text(encoding="utf8").splitlines()]
    assert invocations
    assert all("--disallowedTools" in args for args in invocations)
    assert len(invocations) == 4


def test_cli_failure_does_not_echo_untrusted_stdout(tmp_path):
    fake_claude = tmp_path / "claude"
    fake_claude.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env node
            process.stdout.write("partial model output that should not be logged\\n");
            process.exit(1);
            """
        ),
        encoding="utf8",
    )
    fake_claude.chmod(0o755)

    result = subprocess.run(
        ["node", str(COMPANION), "review", "quick failure stdout smoke"],
        cwd=tmp_path,
        env={**os.environ, "CLAUDE_CODE_PATH": str(fake_claude)},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )
    assert result.returncode == 1
    assert "partial model output" not in result.stderr
    assert "claude --print failed" in result.stderr


def test_sdk_retry_omits_unknown_deny_candidate_and_keeps_allowlist_invariant(tmp_path):
    fake_sdk = tmp_path / "fake-sdk.mjs"
    log_file = tmp_path / "sdk-calls.jsonl"
    fake_sdk.write_text(
        textwrap.dedent(
            f"""\
            import fs from "node:fs";
            export function query(input) {{
              fs.appendFileSync({json.dumps(str(log_file))}, JSON.stringify({{
                allowedTools: input.allowedTools,
                disallowedTools: input.disallowedTools,
                strictMcpConfig: input.strictMcpConfig
              }}) + "\\n");
              if (input.disallowedTools.includes("MultiEdit")) {{
                throw new Error('Permission deny rule "MultiEdit" matches no known tool — check for typos.');
              }}
              return {{ result: "SDK OK" }};
            }}
            """
        ),
        encoding="utf8",
    )

    source = f"""
      import {{ runSdkPrompt }} from {json.dumps(BACKEND.as_uri())};
      const result = await runSdkPrompt("prompt", {{}}, {{ cwd: {json.dumps(str(tmp_path))} }});
      console.log(JSON.stringify(result));
    """
    output = json.loads(node_eval(source, env={"CLAUDE_FOR_CODEX_SDK_MODULE": str(fake_sdk)}))
    assert output["status"] == 0
    assert output["stdout"] == "SDK OK"
    invocations = [json.loads(line) for line in log_file.read_text(encoding="utf8").splitlines()]
    assert [call["disallowedTools"] for call in invocations] == [
        ["Edit", "Write", "MultiEdit", "Bash"],
        ["Edit", "Write", "Bash"],
    ]
    assert len({tuple(call["allowedTools"]) for call in invocations}) == 1
    assert all(call["strictMcpConfig"] is True for call in invocations)


def test_sdk_retry_parses_prefixed_multiline_unknown_deny_error(tmp_path):
    fake_sdk = tmp_path / "fake-sdk.mjs"
    log_file = tmp_path / "sdk-calls.jsonl"
    fake_sdk.write_text(
        textwrap.dedent(
            f"""\
            import fs from "node:fs";
            export function query(input) {{
              fs.appendFileSync({json.dumps(str(log_file))}, JSON.stringify(input.disallowedTools) + "\\n");
              if (input.disallowedTools.includes("MultiEdit")) {{
                throw new Error([
                  "SDK wrapper preface",
                  "provider diagnostic",
                  "runtime diagnostic",
                  'Permission deny rule "MultiEdit" matches no known tool — check for typos.'
                ].join("\\n"));
              }}
              return {{ result: "SDK OK" }};
            }}
            """
        ),
        encoding="utf8",
    )

    source = f"""
      import {{ runSdkPrompt }} from {json.dumps(BACKEND.as_uri())};
      const result = await runSdkPrompt("prompt", {{}}, {{ cwd: {json.dumps(str(tmp_path))} }});
      console.log(JSON.stringify(result));
    """
    output = json.loads(node_eval(source, env={"CLAUDE_FOR_CODEX_SDK_MODULE": str(fake_sdk)}))
    assert output["status"] == 0
    invocations = [json.loads(line) for line in log_file.read_text(encoding="utf8").splitlines()]
    assert invocations == [
        ["Edit", "Write", "MultiEdit", "Bash"],
        ["Edit", "Write", "Bash"],
    ]


def test_sdk_native_retry_updates_agent_disallowed_tools(tmp_path):
    fake_sdk = tmp_path / "fake-sdk.mjs"
    log_file = tmp_path / "sdk-native-calls.jsonl"
    fake_sdk.write_text(
        textwrap.dedent(
            f"""\
            import fs from "node:fs";
            export function query(input) {{
              const firstAgent = Object.values(input.agents || {{}})[0] || {{}};
              fs.appendFileSync({json.dumps(str(log_file))}, JSON.stringify({{
                topLevel: input.disallowedTools,
                agent: firstAgent.disallowedTools,
                allowedTools: input.allowedTools,
                strictMcpConfig: input.strictMcpConfig
              }}) + "\\n");
              if ((firstAgent.disallowedTools || []).includes("MultiEdit")) {{
                throw new Error('Permission deny rule "MultiEdit" matches no known tool — check for typos.');
              }}
              return {{ result: "NATIVE SDK OK" }};
            }}
            """
        ),
        encoding="utf8",
    )

    source = f"""
      import {{ runSdkNativeReview }} from {json.dumps(BACKEND.as_uri())};
      import {{ buildNativeReviewAgents }} from {json.dumps(NATIVE_REVIEW.as_uri())};
      const agents = buildNativeReviewAgents([{{ name: "security", description: "security", prompt: "review security" }}]);
      const result = await runSdkNativeReview("prompt", {{}}, {{ cwd: {json.dumps(str(tmp_path))}, agents }});
      console.log(JSON.stringify(result));
    """
    output = json.loads(node_eval(source, env={"CLAUDE_FOR_CODEX_SDK_MODULE": str(fake_sdk)}))
    assert output["status"] == 0
    assert output["stdout"] == "NATIVE SDK OK"
    invocations = [json.loads(line) for line in log_file.read_text(encoding="utf8").splitlines()]
    assert [call["topLevel"] for call in invocations] == [
        ["Edit", "Write", "MultiEdit", "Bash"],
        ["Edit", "Write", "Bash"],
    ]
    assert [call["agent"] for call in invocations] == [
        ["Edit", "Write", "MultiEdit", "Bash", "Agent"],
        ["Edit", "Write", "Bash", "Agent"],
    ]
    assert len({tuple(call["allowedTools"]) for call in invocations}) == 1
    assert all(call["strictMcpConfig"] is True for call in invocations)


def test_release_check_uses_policy_markers_not_literal_deny_string():
    assert "readOnlyIsolationChecksFromSource" in (PLUGIN / "scripts" / "lib" / "release-check.mjs").read_text(encoding="utf8")
    text = (PLUGIN / "scripts" / "lib" / "release-check.mjs").read_text(encoding="utf8")
    assert "configuredWriteDenyTools(process.env)" in text
    assert "parseUnknownDenyToolFailure" in text
    assert '"Edit,Write,MultiEdit,Bash"' not in text


def test_git_mcp_subprocesses_have_timeout():
    text = MCP_GIT.read_text(encoding="utf8")
    assert "GIT_TIMEOUT_MS" in text
    assert "timeout: GIT_TIMEOUT_MS" in text
    assert 'killSignal: "SIGKILL"' in text
