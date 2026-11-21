"""The tool registry: what it accepts, how it matches a project and what it writes for each host."""

import contextlib
import copy
import io
import json
import re
import tempfile
import tomllib
import unittest
from pathlib import Path

from openguildkit.catalog import CatalogError, load_catalog
from openguildkit.cli import main
from openguildkit.tools import (
    CATEGORIES, _validate_tool, apply_connections, check_tool_links, choose_server, connection_status, detect_tools,
    load_mock, load_tools, match_tools, mock_ids, plan_connections, read_manifest, remove_connections,
)

TOOLS = load_tools()
REMOTE = {
    "id": "acme-remote", "name": "Acme MCP", "publisher": "vendor", "maintainer": "Acme", "transport": "streamable-http",
    "url": "https://mcp.acme.example/mcp", "auth": "oauth", "env": [], "status": "ga",
    "capabilities": "Read tickets.", "source": "https://docs.acme.example/mcp", "verified": "2026-11-21",
}
TOOL = {
    "id": "acme", "title": "Acme", "vendor": "Acme", "summary": "A tracker.", "homepage": "https://acme.example",
    "brains": ["qa"], "servers": [REMOTE],
}


def run(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(arg) for arg in args])
    return code, out.getvalue(), err.getvalue()


class RegistryTests(unittest.TestCase):
    def test_the_bundled_registry_is_complete_and_linked_to_the_brains(self):
        check_tool_links(TOOLS, load_catalog()["boards"])
        self.assertGreaterEqual(len(TOOLS), 130)
        self.assertEqual({tool["category"] for tool in TOOLS.values()} - set(CATEGORIES), set())
        for name in ("jira", "factorial", "gryzzly", "snyk", "obscura", "storybook", "documentalist"):
            self.assertIn(name, TOOLS)
        self.assertEqual(TOOLS["gryzzly"]["servers"], [])
        self.assertIn("api", TOOLS["gryzzly"])

    def test_every_server_names_its_source_and_never_a_credential(self):
        for tool in TOOLS.values():
            for server in tool["servers"]:
                label = f"{tool['id']}/{server['id']}"
                self.assertTrue(server["source"].startswith("https://"), label)
                self.assertRegex(server["verified"], r"^\d{4}-\d{2}-\d{2}$", label)
                text = json.dumps(server)
                self.assertNotRegex(text, r"(?i)(sk|pk|ghp|xox[bp])[-_][A-Za-z0-9]{12,}", label)
                self.assertNotRegex(server.get("url", ""), r"(?i)[?&][^=]*(token|key|secret)=", label)
                for name in server["env"]:
                    self.assertRegex(name, r"^[A-Z][A-Z0-9_]+$", label)

    def test_one_server_shared_by_several_tools_keeps_one_endpoint(self):
        self.assertEqual(TOOLS["jira"]["servers"][0]["id"], TOOLS["confluence"]["servers"][0]["id"])
        self.assertEqual(TOOLS["jira"]["servers"][0]["url"], TOOLS["confluence"]["servers"][0]["url"])

    def test_entries_with_credentials_or_undeclared_placeholders_are_refused(self):
        cases = {
            "credential in its query": {"url": "https://mcp.acme.example/mcp?api_key=abc"},
            "https URL": {"url": "http://mcp.acme.example/mcp"},
            "declare exactly the placeholders": {"url": "https://{subdomain}.acme.example/mcp"},
            "verified is the date": {"verified": "yesterday"},
            "list of unique names": {"env": ["acme token"]},
            "transport is one of": {"transport": "websocket"},
        }
        for message, change in cases.items():
            tool = copy.deepcopy(TOOL)
            tool["servers"][0].update(change)
            with self.assertRaisesRegex(CatalogError, message):
                _validate_tool(tool, "testing")
        local = copy.deepcopy(TOOL)
        local["servers"][0] = {
            **{key: value for key, value in REMOTE.items() if key != "url"}, "transport": "stdio", "command": "docker",
            "args": ["run", "-e", "ACME_TOKEN=abc123"], "package": {"registry": "docker", "name": "acme/mcp"},
        }
        with self.assertRaisesRegex(CatalogError, "must come from the environment"):
            _validate_tool(local, "testing")
        local["servers"][0]["args"] = ["run", "-e", "ACME_TOKEN", "--site=<your-site>"]
        with self.assertRaisesRegex(CatalogError, "placeholders as"):
            _validate_tool(local, "testing")

    def test_a_loopback_endpoint_is_accepted_for_servers_that_run_on_the_workstation(self):
        tool = copy.deepcopy(TOOL)
        tool["servers"][0]["url"] = "http://127.0.0.1:3845/mcp"
        _validate_tool(tool, "testing")

    def test_a_project_adds_its_own_tools_and_cannot_redefine_a_bundled_one(self):
        with tempfile.TemporaryDirectory() as directory:
            extra = Path(directory)
            (extra / "testing.json").write_text(json.dumps({"id": "testing", "title": "Ours", "tools": [TOOL]}), encoding="utf-8")
            self.assertIn("acme", load_tools(extra))
            clash = {**TOOL, "id": "jira"}
            (extra / "testing.json").write_text(json.dumps({"id": "testing", "title": "Ours", "tools": [clash]}), encoding="utf-8")
            with self.assertRaisesRegex(CatalogError, "already defined"):
                load_tools(extra)


class MatchingTests(unittest.TestCase):
    def test_names_written_by_a_team_resolve_to_registry_ids(self):
        matched, unknown = match_tools(["Jira Cloud", "github", "Factorial HR", "Our intranet", "GA4", "jira"], TOOLS)
        self.assertEqual(matched, ["jira", "github", "factorial", "google-analytics"])
        self.assertEqual(unknown, ["Our intranet"])

    def test_the_repository_shows_which_tools_it_uses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".github" / "workflows").mkdir(parents=True)
            (root / ".github" / "workflows" / "ci.yml").write_text("on: push\n", encoding="utf-8")
            (root / "package.json").write_text(json.dumps({"devDependencies": {"@playwright/test": "^1.50.0", "storybook": "^10"}}), encoding="utf-8")
            (root / "api").mkdir()
            (root / "api" / "requirements.txt").write_text("sentry-sdk==2.0\n# comment\nfastapi\n", encoding="utf-8")
            found = {item["tool"]: item["signals"] for item in detect_tools(root, TOOLS)}
        self.assertEqual(found["github"], [".github/workflows/*.yml"])
        self.assertIn("npm:@playwright/test", found["playwright"])
        self.assertIn("npm:storybook", found["storybook"])
        self.assertEqual(found["sentry"], ["pypi:sentry-sdk"])
        self.assertNotIn("jira", found)

    def test_the_vendor_remote_server_comes_first_and_community_servers_need_consent(self):
        self.assertEqual(choose_server(TOOLS["github"])["id"], "github-remote")
        self.assertIsNone(choose_server(TOOLS["redmine"]))
        self.assertEqual(choose_server(TOOLS["redmine"], community=True)["publisher"], "community")
        with self.assertRaisesRegex(ValueError, "community server"):
            choose_server(TOOLS["redmine"], TOOLS["redmine"]["servers"][0]["id"])
        self.assertIsNone(choose_server(TOOLS["gryzzly"], community=True))


class PlanTests(unittest.TestCase):
    def test_a_plan_names_what_each_person_provides_and_writes_no_value(self):
        plan = plan_connections(TOOLS, ["jira", "confluence", "github/github-local", "gryzzly"], "claude-code")
        jira, github, gryzzly = plan
        self.assertEqual(jira["covers"], ["jira", "confluence"])
        self.assertEqual(jira["entry"], {"type": "http", "url": TOOLS["jira"]["servers"][0]["url"]})
        self.assertEqual(github["entry"]["env"], {"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_PERSONAL_ACCESS_TOKEN}"})
        self.assertEqual(github["needs"]["env"], ["GITHUB_PERSONAL_ACCESS_TOKEN"])
        self.assertIn("no MCP server on record", gryzzly["skipped"])
        self.assertIn("API:", gryzzly["skipped"])

    def test_codex_entries_forward_variable_names_and_bearer_tokens_by_name(self):
        local = plan_connections(TOOLS, ["github/github-local"], "codex")[0]["entry"]
        self.assertEqual(local["env_vars"], ["GITHUB_PERSONAL_ACCESS_TOKEN"])
        self.assertNotIn("env", local)
        remote = plan_connections(TOOLS, ["linear"], "codex", token_env={"linear": "LINEAR_API_KEY"})[0]
        self.assertEqual(remote["entry"], {"url": "https://mcp.linear.app/mcp", "bearer_token_env_var": "LINEAR_API_KEY"})
        claude = plan_connections(TOOLS, ["linear"], "claude-code", token_env={"linear": "LINEAR_API_KEY"})[0]
        self.assertEqual(claude["entry"]["headers"], {"Authorization": "Bearer ${LINEAR_API_KEY}"})

    def test_settings_fill_the_endpoint_and_refuse_anything_that_looks_like_a_secret(self):
        missing = plan_connections(TOOLS, ["zendesk"], "claude-code")[0]
        self.assertEqual(missing["needs"]["settings"], ["subdomain"])
        filled = plan_connections(TOOLS, ["zendesk"], "claude-code", settings={"zendesk": {"subdomain": "acme"}})[0]
        self.assertEqual(filled["entry"]["url"], "https://acme.zendesk.com/mcp")
        with self.assertRaisesRegex(ValueError, "no setting"):
            plan_connections(TOOLS, ["zendesk"], "claude-code", settings={"zendesk": {"token": "abc"}})
        with self.assertRaisesRegex(ValueError, "plain value"):
            plan_connections(TOOLS, ["zendesk"], "claude-code", settings={"zendesk": {"subdomain": "a b;rm"}})
        with self.assertRaisesRegex(ValueError, "names an environment variable"):
            plan_connections(TOOLS, ["linear"], "codex", token_env={"linear": "lin_api_abc123"})

    def test_mock_mode_replaces_every_server_with_a_local_one(self):
        plan = plan_connections(TOOLS, ["jira", "gryzzly"], "codex", mock=True, mocks=mock_ids(), log="calls.jsonl")
        self.assertEqual(plan[0]["entry"], {"command": "openguildkit", "args": ["tools", "mock", "jira", "--log", "calls.jsonl"]})
        self.assertIn("No mock", plan[1]["skipped"])


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)
        self.state = self.root / ".openguildkit"
        self.state.mkdir()

    def test_claude_code_file_keeps_the_persons_own_servers(self):
        (self.root / ".mcp.json").write_text(json.dumps({"mcpServers": {"mine": {"command": "my-server"}}}), encoding="utf-8")
        plan = plan_connections(TOOLS, ["jira", "snyk"], "claude-code")
        result = apply_connections(self.root, self.state, "claude-code", plan)
        self.assertEqual(result["applied"], ["atlassian-rovo", "snyk-cli-mcp"])
        servers = json.loads((self.root / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
        self.assertEqual(set(servers), {"mine", "atlassian-rovo", "snyk-cli-mcp"})
        first = (self.root / ".mcp.json").read_bytes()
        apply_connections(self.root, self.state, "claude-code", plan)
        self.assertEqual((self.root / ".mcp.json").read_bytes(), first)
        remove_connections(self.root, self.state, "claude-code", ["jira"])
        servers = json.loads((self.root / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
        self.assertEqual(set(servers), {"mine", "snyk-cli-mcp"})

    def test_a_server_the_person_already_defined_is_never_overwritten(self):
        (self.root / ".mcp.json").write_text(json.dumps({"mcpServers": {"atlassian-rovo": {"type": "http", "url": "https://other.example/mcp"}}}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "already defines atlassian-rovo"):
            apply_connections(self.root, self.state, "claude-code", plan_connections(TOOLS, ["jira"], "claude-code"))
        self.assertEqual(read_manifest(self.state)["connections"], [])

    def test_codex_block_is_valid_toml_beside_the_persons_configuration(self):
        config = self.root / ".codex" / "config.toml"
        config.parent.mkdir()
        config.write_text('web_search = "disabled"\n\n[mcp_servers.mine]\ncommand = "my-server"\n', encoding="utf-8")
        plan = plan_connections(TOOLS, ["github/github-local", "linear"], "codex", token_env={"linear": "LINEAR_API_KEY"})
        apply_connections(self.root, self.state, "codex", plan)
        parsed = tomllib.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(parsed["web_search"], "disabled")
        self.assertEqual(set(parsed["mcp_servers"]), {"mine", "github-local", "linear-remote"})
        self.assertEqual(parsed["mcp_servers"]["github-local"]["env_vars"], ["GITHUB_PERSONAL_ACCESS_TOKEN"])
        first = config.read_text(encoding="utf-8")
        apply_connections(self.root, self.state, "codex", plan)
        self.assertEqual(config.read_text(encoding="utf-8"), first)
        remove_connections(self.root, self.state, "codex", ["github", "linear"])
        self.assertEqual(config.read_text(encoding="utf-8"), 'web_search = "disabled"\n\n[mcp_servers.mine]\ncommand = "my-server"\n')
        config.write_text(first + '\n[mcp_servers.linear-remote]\ncommand = "x"\n', encoding="utf-8")
        with self.assertRaises(ValueError):
            apply_connections(self.root, self.state, "codex", plan)

    def test_missing_settings_block_the_write(self):
        with self.assertRaisesRegex(ValueError, "Settings are missing"):
            apply_connections(self.root, self.state, "claude-code", plan_connections(TOOLS, ["zendesk"], "claude-code"))
        self.assertFalse((self.root / ".mcp.json").exists())

    def test_status_reports_drift_and_variables_missing_on_this_workstation(self):
        apply_connections(self.root, self.state, "claude-code", plan_connections(TOOLS, ["github/github-local"], "claude-code"))
        status = connection_status(self.root, self.state, environment={})
        self.assertEqual((status[0]["configured"], status[0]["missing_env"]), (True, ["GITHUB_PERSONAL_ACCESS_TOKEN"]))
        self.assertEqual(connection_status(self.root, self.state, environment={"GITHUB_PERSONAL_ACCESS_TOKEN": "x"})[0]["missing_env"], [])
        (self.root / ".mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")
        self.assertFalse(connection_status(self.root, self.state, environment={})[0]["configured"])

    def test_nothing_written_holds_a_secret_value(self):
        for host in ("claude-code", "codex"):
            wanted = [tool_id for tool_id, tool in TOOLS.items() if choose_server(tool) and not choose_server(tool).get("settings")]
            apply_connections(self.root, self.state, host, plan_connections(TOOLS, wanted, host))
        for path in (self.root / ".mcp.json", self.root / ".codex" / "config.toml", self.state / "tools.json"):
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"(?i)(password|secret)\s*[\"']?\s*[:=]\s*[\"'][^$\"']", path.name)
            for value in re.findall(r"Bearer ([^\"]+)", text):
                self.assertRegex(value, r"^\$\{[A-Z0-9_]+\}$")
        tomllib.loads((self.root / ".codex" / "config.toml").read_text(encoding="utf-8"))


class CommandTests(unittest.TestCase):
    def test_list_show_and_call_work_without_a_project(self):
        with tempfile.TemporaryDirectory() as directory:
            code, out, _ = run("--project", directory, "tools", "list", "--category", "time-tracking")
            self.assertEqual(code, 0)
            self.assertIn("gryzzly", out)
            self.assertIn("no MCP server", out)
            code, out, _ = run("--project", directory, "tools", "show", "snyk")
            self.assertIn("Verified 2026-11-21 on https://", out)
            code, _, err = run("--project", directory, "tools", "show", "nope")
            self.assertEqual(code, 1)
            self.assertIn("Unknown tool", err)

    def test_call_lists_a_mocks_tools_and_answers_one_call(self):
        with tempfile.TemporaryDirectory() as directory:
            code, out, _ = run("--project", directory, "tools", "call", "jira")
            self.assertEqual(code, 0)
            self.assertIn("search_issues", out)
            code, out, _ = run("--project", directory, "tools", "call", "jira", "search_issues", "--args", '{"query": "export"}')
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["issues"][0]["key"], "SUP-142")
            code, out, _ = run("--project", directory, "tools", "call", "jira", "get_issue", "--args", '{"key": "NOPE-1"}')
            self.assertEqual(code, 1)
            self.assertIn("not found", out)
            code, _, err = run("--project", directory, "tools", "call", "jira", "search_issues", "--args", "{not json")
            self.assertEqual(code, 1)
            self.assertIn("--args is not valid JSON", err)

    def test_every_bundled_mock_loads_and_names_a_registry_tool(self):
        ids = mock_ids()
        self.assertGreaterEqual(len(ids), 15)
        for tool_id in ids:
            self.assertIn(tool_id, TOOLS, tool_id)
            mock = load_mock(tool_id)
            text = json.dumps(mock)
            for address in re.findall(r"[\w.+-]+@[\w.-]+", text):
                self.assertTrue(address.endswith("example.test"), f"{tool_id}: {address}")


if __name__ == "__main__":
    unittest.main()
