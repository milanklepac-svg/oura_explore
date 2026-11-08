"""Controller regressions use invented tickets, temporary Git repos and mocks."""
import argparse
import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
if not (ROOT / "scripts/team.py").exists():
    ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("team_controller", ROOT / "scripts/team.py")
team = importlib.util.module_from_spec(spec)
spec.loader.exec_module(team)


class TeamTests(unittest.TestCase):
    def git(self, root, *args):
        return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.DEVNULL).decode().strip()

    def repository(self, folder):
        root = Path(folder) / "candidate"
        root.mkdir()
        self.git(root, "init", "-q")
        self.git(root, "config", "user.name", "Synthetic Test")
        self.git(root, "config", "user.email", "test@example.com")
        (root / ".github/workflows").mkdir(parents=True)
        (root / ".github/workflows/ci.yml").write_text("name: synthetic trusted workflow\n")
        (root / "backend").mkdir()
        (root / "backend/allowed.py").write_text("value = 1\n")
        (root / "backend/other.py").write_text("value = 1\n")
        self.git(root, "add", ".")
        self.git(root, "commit", "-qm", "synthetic base")
        return root

    def state(self, root):
        return {"id": "PC-0001", "title": "Synthetic task", "body": "Invented criteria",
                "url": "https://github.com/example/private/issues/1", "branch": "feat/PC-0001-synthetic",
                "base": self.git(root, "rev-parse", "HEAD"), "workspace": str(root),
                "role": "backend_developer", "allowed_paths": ["backend/allowed.py"],
                "status": "ready", "attempts": 0}

    @contextlib.contextmanager
    def run_context(self, folder, state, invoke):
        cfg = dict(team.config(), max_attempts_per_ticket=2, max_parallel_runs=2)
        with patch.object(team, "STATE", Path(folder) / "state"), patch.object(team, "config", return_value=cfg), \
             patch.object(team, "load", return_value=state), patch.object(team, "invoke", side_effect=invoke), \
             patch.object(team, "checks", return_value="Synthetic checks passed") as checks, \
             patch.object(team, "github") as github:
            yield checks, github

    def test_bundled_cli_is_found_without_a_terminal_path_alias(self):
        with patch.object(team.shutil, "which", return_value=None), patch.object(team.Path, "is_file", return_value=True):
            self.assertTrue(team.codex_binary().endswith("/MacOS/codex"))
        with patch.object(team.shutil, "which", return_value=None), patch.object(team.Path, "is_file", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "CLI not found"):
                team.codex_binary()

    def test_exact_container_scope_never_allows_private_root_files(self):
        allowed = ['compose.yaml', 'compose.test.yaml', '.env.example']
        self.assertEqual(team.validate_paths(allowed), allowed)
        for path in ['.env', '.secrets', '.env.example/secret', 'compose.yaml/secret', '../compose.yaml', '/compose.yaml', 'README.md']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                team.validate_paths([path])

    def test_scope_requires_path_boundary(self):
        team.enforce_paths(["backend/app/a.py"], ["backend/app"])
        with self.assertRaises(RuntimeError):
            team.enforce_paths(["backend/application.py"], ["backend/app"])
        with self.assertRaises(ValueError):
            team.validate_paths(["backend/../private"])

    def test_changed_paths_handles_spaces_and_rejects_renames(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.repository(folder)
            (root / "backend/space name.py").write_text("synthetic = True\n")
            self.assertEqual(team.changed_paths(root), ["backend/space name.py"])
            (root / "backend/space name.py").unlink()
            self.git(root, "mv", "backend/allowed.py", "backend/renamed.py")
            with self.assertRaises(RuntimeError):
                team.changed_paths(root)

    def test_attempt_limit_stops_before_agent_or_github(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.repository(folder)
            state = self.state(root)
            state["attempts"] = 2
            def unexpected(*args):
                self.fail("Agent should not run after attempt limit")
            with self.run_context(folder, state, unexpected) as (checks, github):
                with self.assertRaisesRegex(RuntimeError, "Attempt limit"):
                    team.run(argparse.Namespace(ticket="PC-0001", dry_run=False))
                checks.assert_not_called()
                github.assert_not_called()

    def test_developer_commit_cannot_hide_out_of_scope_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.repository(folder)
            state = self.state(root)
            def invoke(current, role, evidence=""):
                if role == "backend_developer":
                    (root / "backend/other.py").write_text("value = 99\n")
                    self.git(root, "add", ".")
                    self.git(root, "commit", "-qm", "unexpected agent commit")
                    (root / "backend/allowed.py").write_text("value = 2\n")
                return {"verdict": "pass"}
            with self.run_context(folder, state, invoke) as (checks, github):
                with self.assertRaises(RuntimeError):
                    team.run(argparse.Namespace(ticket="PC-0001", dry_run=False))
                checks.assert_not_called()
                github.assert_not_called()
                self.assertEqual(state["status"], "needs_attention")

    def test_qa_mutation_cannot_publish_checked_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.repository(folder)
            state = self.state(root)
            def invoke(current, role, evidence=""):
                (root / "backend/allowed.py").write_text("value = " + ("2" if role == "backend_developer" else "3") + "\n")
                return {"verdict": "pass"}
            with self.run_context(folder, state, invoke) as (checks, github):
                with self.assertRaises(RuntimeError):
                    team.run(argparse.Namespace(ticket="PC-0001", dry_run=False))
                checks.assert_called_once()
                github.assert_not_called()
                self.assertEqual(self.git(root, "rev-parse", "HEAD"), state["base"])

    def test_agent_environment_does_not_forward_api_key(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "synthetic-only-key", "DATABASE_URL": "synthetic-db"}):
            env = team.clean_environment()
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("DATABASE_URL", env)

    def test_completed_cli_output_is_drained_before_accepting_report(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "reports/PC-0001-backend_developer.json"
            event = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 50001, "output_tokens": 1}})
            process = Mock(returncode=0, stdin=io.StringIO(), stdout=io.StringIO(event + "\n"))
            process.poll.return_value = 0
            selector = Mock()
            selector.select.return_value = [(Mock(fileobj=process.stdout), 1)]
            def start(*args, **kwargs):
                report.write_text(json.dumps({"verdict": "pass"}))
                return process
            with patch.object(team, "STATE", Path(folder)), patch.object(team, "brief", return_value="Synthetic task"), \
                 patch.object(team, "codex_binary", return_value="codex"), \
                 patch.object(team.subprocess, "run", return_value=Mock(returncode=0, stdout="ChatGPT", stderr="")), \
                 patch.object(team.selectors, "DefaultSelector", return_value=selector), \
                 patch.object(team.subprocess, "Popen", side_effect=start):
                with self.assertRaisesRegex(RuntimeError, "budget"):
                    team.invoke({"id": "PC-0001", "workspace": folder}, "backend_developer")

    def test_prepare_uses_remote_main_ahead_of_local_checkout(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            writer = self.repository(folder)
            self.git(writer, "branch", "-M", "main")
            remote = folder / "origin.git"
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            self.git(writer, "remote", "add", "origin", str(remote))
            self.git(writer, "push", "-q", "origin", "main")
            self.git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
            source = folder / "source"
            subprocess.run(["git", "clone", "-q", str(remote), str(source)], check=True)
            (writer / "backend/allowed.py").write_text("value = 2\n")
            self.git(writer, "add", ".")
            self.git(writer, "commit", "-qm", "synthetic remote update")
            self.git(writer, "push", "-q", "origin", "main")
            expected = self.git(writer, "rev-parse", "HEAD")
            cfg = dict(team.config(), repository="example/private", main_branch="main")
            original = team.command
            def local_command(args, cwd=None, **kwargs):
                return original(args, cwd=source if cwd is None else cwd, **kwargs)
            issue = {"title": "Synthetic task", "body": "Invented criteria", "state": "open",
                     "html_url": "https://github.com/example/private/issues/1"}
            with patch.object(team, "ROOT", source), patch.object(team, "STATE", source / ".team"), \
                 patch.object(team, "config", return_value=cfg), patch.object(team, "command", side_effect=local_command), \
                 patch.object(team, "github") as github:
                github.return_value.request.return_value = issue
                team.prepare(argparse.Namespace(ticket="PC-0001", role="backend_developer", paths=["backend/allowed.py"]))
                workspace = source / ".team/workspaces/PC-0001"
                self.assertEqual(self.git(workspace, "rev-parse", "HEAD"), expected)
                self.assertEqual((workspace / "backend/allowed.py").read_text(), "value = 2\n")


    @contextlib.contextmanager
    def merge_context(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.repository(folder)
            state = self.state(root)
            (root / "backend/allowed.py").write_text("value = 2\n")
            self.git(root, "add", ".")
            self.git(root, "commit", "-qm", "synthetic candidate")
            sha = self.git(root, "rev-parse", "HEAD")
            tree = self.git(root, "rev-parse", "HEAD^{tree}")
            cfg = dict(team.config(), repository="example/private", main_branch="main",
                       merge_timeout_seconds=2, merge_poll_seconds=1, auto_merge=False)
            state.update(status="pr_open", sha=sha, repository=cfg["repository"], main_branch="main",
                         pr_number=1, pr="https://github.com/example/private/pull/1",
                         checks={"status": "pass", "sha": sha, "tree": tree, "base": state["base"],
                                 "command": "scripts/check_local.py"},
                         qa={"status": "pass", "sha": sha, "tree": tree, "base": state["base"]})
            repository = {"full_name": "example/private", "private": True}
            run = {"id": 7, "run_attempt": 1, "workflow_id": 5, "path": ".github/workflows/ci.yml",
                   "event": "pull_request", "head_sha": sha, "head_branch": state["branch"],
                   "repository": dict(repository), "head_repository": dict(repository),
                   "status": "completed", "conclusion": "success", "pull_requests": [
                       {"number": 1, "head": {"sha": sha}, "base": {"sha": state["base"]}}]}
            responses = {"": repository,
                         "/git/ref/heads/main": {"object": {"sha": state["base"]}},
                         "/pulls/1": {"number": 1, "state": "open", "draft": False,
                                      "merged": False, "mergeable": True, "html_url": state["pr"],
                                      "head": {"sha": sha, "ref": state["branch"], "repo": dict(repository)},
                                      "base": {"sha": state["base"], "ref": "main", "repo": dict(repository)}},
                         "/actions/workflows/ci.yml": {"id": 5, "path": ".github/workflows/ci.yml", "state": "active"},
                         f"/actions/workflows/5/runs?event=pull_request&head_sha={sha}&per_page=100": {
                             "total_count": 1, "workflow_runs": [run]},
                         "/actions/runs/7/attempts/1/jobs?per_page=100": {"total_count": 1, "jobs": [
                             {"id": 9, "run_id": 7, "name": "test", "head_sha": sha,
                              "status": "completed", "conclusion": "success"}]}}
            for name in ("LOCAL_COACH_HOSTED_CI_ENABLED", "LOCAL_COACH_BILLING_ZERO_VERIFIED"):
                responses["/actions/variables/" + name] = {"name": name, "value": "true"}
            gh = Mock()
            def request(method, path, body=None):
                if method == "PUT":
                    self.assertEqual(path, "/pulls/1/merge")
                    self.assertEqual(body, {"merge_method": "squash", "sha": sha})
                    return {"merged": True, "sha": "a" * 40}
                self.assertEqual(method, "GET")
                return responses[path]
            gh.request.side_effect = request
            with patch.object(team, "STATE", Path(folder) / "state"), \
                 patch.object(team, "config", return_value=cfg), patch.object(team, "load", return_value=state), \
                 patch.object(team, "github", return_value=gh), patch.object(team, "invoke") as invoke:
                team.save(state)
                yield state, cfg, gh, responses
                invoke.assert_not_called()

    def assert_no_merge(self, gh):
        self.assertFalse(any(call.args[0] == "PUT" for call in gh.request.call_args_list))

    def test_lock_validation_precedes_any_file_access(self):
        with tempfile.TemporaryDirectory() as folder:
            state_dir = Path(folder) / "not-created"
            with patch.object(team, "STATE", state_dir):
                for name in ("", "../victim", "PC-0000", "PC-0001/x", "slot-2", "slot-01", "merge/../x"):
                    with self.subTest(name=name), self.assertRaises(ValueError):
                        with team.lock(name):
                            self.fail("Invalid lock must not open")
                    self.assertFalse(state_dir.exists())

    def test_valid_lock_preserves_contents_and_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(team, "STATE", Path(folder)):
            target = Path(folder) / "PC-0001.lock"
            target.write_text("synthetic sentinel")
            with team.lock("PC-0001"):
                self.assertEqual(target.read_text(), "synthetic sentinel")
                with self.assertRaises(RuntimeError):
                    with team.lock("PC-0001"):
                        self.fail("Concurrent ticket lock acquired")
            (Path(folder) / "merge.lock").symlink_to(target)
            with self.assertRaises(OSError):
                with team.lock("merge"):
                    self.fail("Symlink lock acquired")
            self.assertEqual(target.read_text(), "synthetic sentinel")

    def test_merge_success_uses_expected_sha_and_persists_evidence(self):
        with self.merge_context() as (state, cfg, gh, responses):
            team.merge(argparse.Namespace(ticket=state["id"]))
            self.assertEqual(state["status"], "merged")
            self.assertEqual(state["merge_sha"], "a" * 40)
            self.assertEqual(state["merge_ci"], {"run_id": 7, "run_attempt": 1, "job_id": 9})
            saved = json.loads((team.STATE / "tickets/PC-0001.json").read_text())
            self.assertEqual(saved, state)
            puts = [call for call in gh.request.call_args_list if call.args[0] == "PUT"]
            self.assertEqual(len(puts), 1)

    def test_empty_actions_pr_association_cannot_prove_tested_base(self):
        with self.merge_context() as (state, cfg, gh, responses):
            listing = next(v for v in responses.values() if "workflow_runs" in v)
            # An old run of the same branch/head could have tested another base.
            listing["workflow_runs"][0]["pull_requests"] = []
            with self.assertRaisesRegex(RuntimeError, "unrelated"):
                team.merge(argparse.Namespace(ticket=state["id"]))
            self.assert_no_merge(gh)

    def test_merge_serializes_before_contacting_github(self):
        with self.merge_context() as (state, cfg, gh, responses), team.lock("merge"):
            with self.assertRaises(RuntimeError):
                team.merge(argparse.Namespace(ticket=state["id"]))
            gh.request.assert_not_called()

    def test_local_evidence_missing_or_stale_stops_before_github(self):
        for kind, field in (("checks", "status"), ("checks", "sha"), ("checks", "base"),
                            ("checks", "tree"), ("checks", "command"), ("qa", "status"),
                            ("qa", "sha"), ("qa", "base"), ("qa", "tree")):
            with self.subTest(kind=kind, field=field), self.merge_context() as (state, cfg, gh, responses):
                state[kind].pop(field)
                with self.assertRaises(RuntimeError):
                    team.merge(argparse.Namespace(ticket=state["id"]))
                gh.request.assert_not_called()
                self.assertEqual(state["merge_status"], "needs_attention")

    def test_dirty_or_different_checkout_cannot_merge(self):
        for committed in (False, True):
            with self.subTest(committed=committed), self.merge_context() as (state, cfg, gh, responses):
                workspace = Path(state["workspace"])
                (workspace / "backend/allowed.py").write_text("value = 3\n")
                if committed:
                    self.git(workspace, "add", ".")
                    self.git(workspace, "commit", "-qm", "synthetic unchecked update")
                with self.assertRaises(RuntimeError):
                    team.merge(argparse.Namespace(ticket=state["id"]))
                gh.request.assert_not_called()

    def test_workflow_changes_require_separate_review(self):
        with self.merge_context() as (state, cfg, gh, responses):
            workspace = Path(state["workspace"])
            (workspace / ".github/workflows/ci.yml").write_text("name: synthetic changed workflow\n")
            self.git(workspace, "add", ".")
            self.git(workspace, "commit", "-qm", "synthetic workflow edit")
            state["sha"] = self.git(workspace, "rev-parse", "HEAD")
            for kind in ("checks", "qa"):
                state[kind].update(sha=state["sha"], tree=self.git(workspace, "rev-parse", "HEAD^{tree}"))
            with self.assertRaisesRegex(RuntimeError, "trusted CI workflow"):
                team.merge(argparse.Namespace(ticket=state["id"]))
            gh.request.assert_not_called()

    def test_private_repository_main_and_pr_guards(self):
        cases = [("", ("private",), False), ("", ("full_name",), "example/other"),
                 ("/git/ref/heads/main", ("object", "sha"), "b" * 40),
                 ("/pulls/1", ("head", "sha"), "b" * 40),
                 ("/pulls/1", ("head", "ref"), "unrelated"),
                 ("/pulls/1", ("head", "repo", "full_name"), "example/other"),
                 ("/pulls/1", ("base", "sha"), "b" * 40),
                 ("/pulls/1", ("base", "ref"), "unrelated"),
                 ("/pulls/1", ("base", "repo", "private"), False),
                 ("/pulls/1", ("state",), "closed"), ("/pulls/1", ("draft",), True),
                 ("/pulls/1", ("mergeable",), False)]
        for path, keys, value in cases:
            with self.subTest(path=path, keys=keys), self.merge_context() as (state, cfg, gh, responses):
                target = responses[path]
                for key in keys[:-1]:
                    target = target[key]
                target[keys[-1]] = value
                with self.assertRaises(RuntimeError):
                    team.merge(argparse.Namespace(ticket=state["id"]))
                self.assert_no_merge(gh)

    def test_both_ci_variables_must_be_true(self):
        for name in ("LOCAL_COACH_HOSTED_CI_ENABLED", "LOCAL_COACH_BILLING_ZERO_VERIFIED"):
            for value in (None, "false", "True"):
                with self.subTest(name=name, value=value), self.merge_context() as (state, cfg, gh, responses):
                    responses["/actions/variables/" + name]["value"] = value
                    with self.assertRaises(RuntimeError):
                        team.merge(argparse.Namespace(ticket=state["id"]))
                    self.assert_no_merge(gh)

    def test_trusted_workflow_and_exact_test_job_are_required(self):
        cases = [("workflow", "path", ".github/workflows/unrelated.yml"),
                 ("workflow", "state", "disabled_manually"),
                 ("run", "path", ".github/workflows/unrelated.yml"),
                 ("run", "workflow_id", 99), ("run", "head_branch", "unrelated"),
                 ("run", "pull_requests", [{"number": 99}]),
                 ("run", "pull_requests", [{"number": 1, "head": {"sha": "b" * 40}, "base": {"sha": "b" * 40}}]), ("run", "repository", {"private": False}),
                 ("run", "conclusion", "failure"), ("run", "conclusion", "cancelled"),
                 ("run", "conclusion", "skipped"), ("job", "name", "unrelated"),
                 ("job", "head_sha", "b" * 40), ("job", "run_id", 99),
                 ("job", "run_attempt", 2), ("job", "status", "in_progress"),
                 ("job", "conclusion", "skipped"), ("job", "conclusion", "cancelled"),
                 ("job", "conclusion", "failure")]
        for kind, key, value in cases:
            with self.subTest(kind=kind, key=key, value=value), self.merge_context() as (state, cfg, gh, responses):
                targets = {"workflow": responses["/actions/workflows/ci.yml"],
                           "run": next(v["workflow_runs"][0] for v in responses.values() if "workflow_runs" in v),
                           "job": responses["/actions/runs/7/attempts/1/jobs?per_page=100"]["jobs"][0]}
                targets[kind][key] = value
                with self.assertRaises(RuntimeError):
                    team.merge(argparse.Namespace(ticket=state["id"]))
                self.assert_no_merge(gh)

    def test_missing_stale_or_unrelated_runs_time_out_without_merge(self):
        for kind in ("missing", "stale", "unrelated"):
            with self.subTest(kind=kind), self.merge_context() as (state, cfg, gh, responses):
                listing = next(v for v in responses.values() if "workflow_runs" in v)
                if kind == "missing":
                    listing.update(total_count=0, workflow_runs=[])
                elif kind == "stale":
                    listing["workflow_runs"][0]["head_sha"] = "b" * 40
                else:
                    listing["workflow_runs"][0]["event"] = "workflow_dispatch"
                with patch.object(team.time, "monotonic", side_effect=[0, 0, 0, 3]), \
                     patch.object(team.time, "sleep"), self.assertRaisesRegex(RuntimeError, "timed out"):
                    team.merge(argparse.Namespace(ticket=state["id"]))
                self.assert_no_merge(gh)

    def test_pending_ci_is_polled_deterministically_then_merged(self):
        with self.merge_context() as (state, cfg, gh, responses):
            run = next(v["workflow_runs"][0] for v in responses.values() if "workflow_runs" in v)
            run.update(status="in_progress", conclusion=None)
            def complete(seconds):
                run.update(status="completed", conclusion="success")
            with patch.object(team.time, "sleep", side_effect=complete) as sleep:
                team.merge(argparse.Namespace(ticket=state["id"]))
            sleep.assert_called_once()
            self.assertEqual(state["status"], "merged")

    def test_latest_run_failure_and_incomplete_listing_cannot_use_old_green(self):
        for incomplete in (False, True):
            with self.subTest(incomplete=incomplete), self.merge_context() as (state, cfg, gh, responses):
                listing = next(v for v in responses.values() if "workflow_runs" in v)
                listing["total_count"] = 2
                if not incomplete:
                    newer = dict(listing["workflow_runs"][0], id=8, conclusion="failure")
                    listing["workflow_runs"].append(newer)
                with self.assertRaises(RuntimeError):
                    team.merge(argparse.Namespace(ticket=state["id"]))
                self.assert_no_merge(gh)

    def test_mutation_during_final_verification_or_api_error_stops(self):
        for kind in ("base", "api", "merge_rejected"):
            with self.subTest(kind=kind), self.merge_context() as (state, cfg, gh, responses):
                original = gh.request.side_effect
                count = 0
                def request(method, path, body=None):
                    nonlocal count
                    if kind == "api":
                        raise RuntimeError("Synthetic API unavailable")
                    if path == "/git/ref/heads/main":
                        count += 1
                        if count == 2 and kind == "base":
                            return {"object": {"sha": "b" * 40}}
                    if method == "PUT" and kind == "merge_rejected":
                        return {"merged": False}
                    return original(method, path, body)
                gh.request.side_effect = request
                with self.assertRaises(RuntimeError):
                    team.merge(argparse.Namespace(ticket=state["id"]))
                if kind != "merge_rejected":
                    self.assert_no_merge(gh)
                self.assertNotEqual(state["status"], "merged")

    def test_run_keeps_default_no_merge_and_explicit_opt_in(self):
        for flag, automatic in ((False, False), (True, False), (False, True)):
            with self.subTest(flag=flag, automatic=automatic), self.merge_context() as (state, cfg, gh, responses), \
                 patch.object(team, "merge_checked") as integrate:
                cfg["auto_merge"] = automatic
                args = argparse.Namespace(ticket=state["id"], dry_run=False, merge=flag)
                if flag or automatic:
                    team.run(args)
                    integrate.assert_called_once_with(state, cfg)
                else:
                    with self.assertRaisesRegex(RuntimeError, "already open"):
                        team.run(args)
                    integrate.assert_not_called()
                gh.request.assert_not_called()

    def test_new_pr_records_exact_candidate_and_only_explicitly_merges(self):
        for opt_in in (False, True):
            with self.subTest(opt_in=opt_in), tempfile.TemporaryDirectory() as folder:
                root = self.repository(folder)
                state = self.state(root)
                def invoke(current, role, evidence=""):
                    if role == "backend_developer":
                        (root / "backend/allowed.py").write_text("value = 2\n")
                    return {"verdict": "pass"}
                original = team.command
                def command(args, cwd=None, **kwargs):
                    if args[:3] == ["git", "config", "--get"]:
                        return "synthetic-helper"
                    if "push" in args:
                        return ""
                    return original(args, cwd=root if cwd is None else cwd, **kwargs)
                with self.run_context(folder, state, invoke) as (checks, gh), \
                     patch.object(team, "command", side_effect=command), patch.object(team, "merge_checked") as integrate:
                    gh.return_value.request.return_value = {
                        "number": 1, "html_url": "https://github.com/example/private/pull/1"}
                    team.run(argparse.Namespace(ticket=state["id"], dry_run=False, merge=opt_in))
                    sha = self.git(root, "rev-parse", "HEAD")
                    tree = self.git(root, "rev-parse", "HEAD^{tree}")
                    self.assertEqual(state["sha"], sha)
                    for kind in ("checks", "qa"):
                        self.assertEqual(state[kind]["status"], "pass")
                        self.assertEqual(state[kind]["sha"], sha)
                        self.assertEqual(state[kind]["tree"], tree)
                        self.assertEqual(state[kind]["base"], state["base"])
                    self.assertEqual(state["checks"]["command"], "scripts/check_local.py")
                    if opt_in:
                        integrate.assert_called_once()
                    else:
                        integrate.assert_not_called()

    def test_blocked_agent_report_still_persists_usage(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "reports/PC-0001-backend_developer.json"
            event = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 12, "output_tokens": 3}})
            process = Mock(returncode=0, stdin=io.StringIO(), stdout=io.StringIO(event + "\n"))
            process.poll.return_value = 0
            def start(*args, **kwargs):
                report.write_text(json.dumps({"verdict": "blocked"}))
                return process
            state = {"id": "PC-0001", "workspace": folder}
            with patch.object(team, "STATE", Path(folder)), patch.object(team, "brief", return_value="Synthetic task"), \
                 patch.object(team, "codex_binary", return_value="codex"), \
                 patch.object(team.subprocess, "run", return_value=Mock(returncode=0, stdout="ChatGPT", stderr="")), \
                 patch.object(team.selectors, "DefaultSelector"), patch.object(team.subprocess, "Popen", side_effect=start):
                with self.assertRaisesRegex(RuntimeError, "blockers"):
                    team.invoke(state, "backend_developer")
                saved = json.loads((Path(folder) / "tickets/PC-0001.json").read_text())
                self.assertEqual(saved["usage"]["backend_developer"], {"input_tokens": 12, "output_tokens": 3})


if __name__ == "__main__":
    unittest.main()
