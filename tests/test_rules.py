"""Unit tests for the rules table (airlock/rules.py) and the rules-driven
enforce path. No network: every Jev call is mocked.
"""

import tests  # noqa: F401 -- MUST be the first import. `python3 -m unittest
# discover -s tests` runs with start_dir == top_level_dir, so unittest treats
# `tests/` as a flat directory of top-level modules and never executes
# tests/__init__.py as a package init (name == '.' in TestLoader._find_tests).
# Importing it explicitly, here, first, is what actually runs its HOME/
# AIRLOCK_*-isolating fixture before any airlock.* module resolves a real path.

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import posix_only
from airlock import enforce, rules  # noqa: E402

HOME = os.path.expanduser("~")


def ctx_bash(command, **ti):
    ti["command"] = command
    return rules.build_ctx({"tool_name": "Bash", "tool_input": ti, "cwd": "/tmp"}, "Bash")


def fired(ctx, rule_id=None, overrides=None, windows=None):
    rows = rules.dry_run(ctx, overrides=overrides if overrides is not None else {},
                         windows=windows)
    if rule_id is None:
        return [r for r in rows if r["fires"]]
    return [r for r in rows if r["rule_id"] == rule_id]


class TestShellHelpers(unittest.TestCase):
    def test_split_segments_respects_quotes(self):
        segs = rules.split_segments("echo 'a;b' && cat x | grep y")
        self.assertEqual(segs, ["echo 'a;b'", "cat x", "grep y"])

    def test_split_segments_never_raises_on_unbalanced_quote(self):
        self.assertTrue(rules.split_segments("echo 'unbalanced"))

    def test_program_of_skips_assignments_and_wrappers(self):
        self.assertEqual(rules.program_of("FOO=1 nice -n 10 /usr/bin/pytest -q")[0], "pytest")

    def test_program_of_empty(self):
        self.assertEqual(rules.program_of("")[0], None)


class TestR1Secret(unittest.TestCase):
    RID = "R1-secret-exposure"

    def assert_fires(self, command):
        rows = fired(ctx_bash(command), self.RID)
        self.assertTrue(rows, "R1 did not match: %s" % command)
        self.assertTrue(rows[0]["fires"] in (True, None), command)
        return rows[0]

    def test_secret_store_readers_deny(self):
        for c in (
            "cat ~/.config/jev-kit/env",
            "head -3 /home/user/.config/jev-kit/env",
            "cat ~/.config/airlock/env",
            "head -3 /home/user/.config/airlock/env",
            "less ~/.claude/.credentials.json",
            "cat deploy/server.key",
            "cat ~/.ssh/id_ed25519",
        ):
            row = self.assert_fires(c)
            self.assertEqual(row["fires"], True, c)
            self.assertEqual(row["action"], "deny")

    def test_echo_of_secret_variable_denies(self):
        row = self.assert_fires('echo "$TYPESAFE_API_KEY"')
        self.assertEqual(row["fires"], True)

    def test_unfiltered_env_dump_denies(self):
        for c in ("env", "printenv", "export -p"):
            self.assertEqual(self.assert_fires(c)["fires"], True, c)

    def test_verbose_curl_with_auth_header_denies(self):
        self.assertEqual(
            self.assert_fires("curl -v -H 'Authorization: Bearer x' https://api.typesafe.ai")["fires"],
            True,
        )

    def test_near_misses_do_not_match(self):
        for c in (
            "grep TOKEN_NAME src/config.py",
            '[ -n "$JIRA_API_TOKEN" ] && echo set',
            "cat .env.example",
            "set -a; . ~/.config/jev-kit/env; set +a",
            "set -a; . ~/.config/airlock/env; set +a",
            "printenv PATH",
            "grep -c TYPESAFE ~/.config/jev-kit/env",
            "grep -c TYPESAFE ~/.config/airlock/env",
            "cat ~/.ssh/id_ed25519.pub",
            "ls -la ~/.config/airlock/",
        ):
            self.assertEqual(fired(ctx_bash(c), self.RID), [], c)

    def test_read_tool_on_secret_store_denies(self):
        """Both defaults are protected: the kit-level key file and the
        guard-era one an existing install may still be using."""
        for path in ("%s/.config/jev-kit/env" % HOME,
                     "%s/.config/airlock/env" % HOME):
            c = rules.build_ctx(
                {"tool_name": "Read", "tool_input": {"file_path": path}}, "Read"
            )
            rows = fired(c, self.RID)
            self.assertTrue(rows and rows[0]["fires"] is True, path)

    def test_read_tool_on_ordinary_file_does_not_match(self):
        c = rules.build_ctx({"tool_name": "Read", "tool_input": {"file_path": "/tmp/README.md"}}, "Read")
        self.assertEqual(fired(c, self.RID), [])

    def test_ambiguous_path_asks_jev(self):
        rows = fired(ctx_bash("cat ~/notes/secrets.txt"), self.RID)
        self.assertTrue(rows)
        self.assertIsNone(rows[0]["fires"])  # would have asked

    def test_ambiguous_path_denies_only_on_a_confident_yes(self):
        c = ctx_bash("cat ~/notes/secrets.txt")

        def ask_yes(rule, ctx, match):
            return {"prints_a_secret": {"choice": "yes", "confidence": 0.95,
                                        "probabilities": {"yes": 0.95, "no": 0.04, "unclear": 0.01}}}

        def ask_low_margin(rule, ctx, match):
            return {"prints_a_secret": {"choice": "yes", "confidence": 0.9,
                                        "probabilities": {"yes": 0.52, "no": 0.47, "unclear": 0.01}}}

        def ask_no(rule, ctx, match):
            return {"prints_a_secret": {"choice": "no", "confidence": 0.99,
                                        "probabilities": {"no": 0.99, "yes": 0.01}}}

        self.assertTrue(rules.dry_run(c, ask=ask_yes, overrides={})[0]["fires"])
        self.assertFalse(rules.dry_run(c, ask=ask_low_margin, overrides={})[0]["fires"])
        self.assertFalse(rules.dry_run(c, ask=ask_no, overrides={})[0]["fires"])


class TestOtherRules(unittest.TestCase):
    def test_r2_claude_api_with_purpose_asks(self):
        c = rules.build_ctx({"tool_name": "Skill",
                             "tool_input": {"skill": "claude-api", "args": "what did that cost"}}, "Skill")
        rows = fired(c, "R2-claude-api-skill")
        self.assertTrue(rows and rows[0]["fires"] is None)

    def test_r2_without_purpose_downgrades_to_warn(self):
        c = rules.build_ctx({"tool_name": "Skill", "tool_input": {"skill": "claude-api"}}, "Skill")
        rows = fired(c, "R2-claude-api-skill")
        self.assertEqual(rows[0]["action"], "warn")
        self.assertTrue(rows[0]["fires"])

    def test_r2_other_skill_ignored(self):
        c = rules.build_ctx({"tool_name": "Skill", "tool_input": {"skill": "graphify"}}, "Skill")
        self.assertEqual(fired(c, "R2-claude-api-skill"), [])

    def test_r3_whole_suite_and_near_misses(self):
        for c in ("pytest", "python3 -m pytest", "make -j", "npm test", "cargo build --release"):
            self.assertTrue(fired(ctx_bash(c), "R3-whole-suite-or-uncapped-build"), c)
        for c in ("pytest tests/test_rules.py -q", "pytest -k redact", "make -j2",
                  "cargo build -j2", "npm run lint"):
            self.assertEqual(fired(ctx_bash(c), "R3-whole-suite-or-uncapped-build"), [], c)

    def test_r3_is_warn_only(self):
        self.assertEqual(fired(ctx_bash("pytest"), "R3-whole-suite-or-uncapped-build")[0]["action"], "warn")

    def test_r4_long_work(self):
        for c in ("pnpm install", "npx playwright install chromium", "docker build -t x .", "uv sync"):
            rows = fired(ctx_bash(c), "R4-long-work-bare-shell")
            self.assertTrue(rows and rows[0]["fires"] is True, c)
        for c in ("pip install torch", "git clone https://github.com/x/y"):
            rows = fired(ctx_bash(c), "R4-long-work-bare-shell")
            self.assertTrue(rows and rows[0]["fires"] is None, c)
        for c in ("ls ~/tools", "pip install --help",
                  "tmux new-session -d -s s 'pnpm install </dev/null'"):
            self.assertEqual(fired(ctx_bash(c), "R4-long-work-bare-shell"), [], c)

    def test_r4_run_in_background_is_exempt(self):
        c = ctx_bash("pnpm install", run_in_background=True)
        self.assertEqual(fired(c, "R4-long-work-bare-shell"), [])

    def test_r5_sudo(self):
        for c in ("sudo chown -R alice /home/user/code", "sudo systemctl restart nginx",
                  "sudo vim /etc/ssh/sshd_config", "sudo rm -rf ~/.cache/pip"):
            rows = fired(ctx_bash(c), "R5-sudo")
            self.assertTrue(rows and rows[0]["fires"] is True, c)
        for c in ("sudo apt-get install -y plocate", "sudo apt install ripgrep fd-find",
                  "apt-cache policy ripgrep", "chmod 600 ~/.config/airlock/env"):
            self.assertEqual(fired(ctx_bash(c), "R5-sudo"), [], c)

    R6_ON = {"R6-gui-or-browser": "deny"}

    def test_r6_gui(self):
        """R6's DEFAULT action is `off` on EVERY platform, because most people
        run Claude Code where there is a desktop. It is pinned on here because
        this test is about which commands the pre-filter matches, not about
        whether the machine running the suite has a screen.
        TestR6DefaultsOffEverywhere in test_windows_rules.py owns the default
        itself, and TestHeadlessDetection owns who turns it on."""
        for c in ("xdg-open https://x", "sensible-browser http://localhost:3000", "firefox a.html"):
            self.assertTrue(
                fired(ctx_bash(c), "R6-gui-or-browser", overrides=self.R6_ON, windows=False), c)
        for c in ("chromium --headless=new --dump-dom https://x", "openssl rand -hex 16",
                  "echo 'open http://localhost:3000 yourself'"):
            self.assertEqual(
                fired(ctx_bash(c), "R6-gui-or-browser", overrides=self.R6_ON, windows=False), [], c)

    def test_r6_is_off_by_default_so_nothing_fires(self):
        for c in ("xdg-open https://x", "firefox a.html", "wslview https://x"):
            for win in (False, True):
                self.assertEqual(
                    fired(ctx_bash(c), "R6-gui-or-browser", windows=win), [], (c, win))

    def test_r7_destructive(self):
        for c in ("git push --force origin harden", "git reset --hard origin/main",
                  "git branch -D harden", "git clean -fdx"):
            self.assertTrue(fired(ctx_bash(c), "R7-destructive"), c)
        for c in ("git push origin harden", "git reset HEAD~1", "git branch -d old",
                  "rm build/out.js", "rm -rf /tmp/scratch-xyz"):
            self.assertEqual(fired(ctx_bash(c), "R7-destructive"), [], c)

    def test_r9_commit_secret(self):
        for c in ("git add .env", "git add server.key", "git add credentials.json"):
            self.assertTrue(fired(ctx_bash(c), "R9-commit-secret"), c)
        for c in ("git add .env.example", "git add README.md", "git status"):
            self.assertEqual(fired(ctx_bash(c), "R9-commit-secret"), [], c)

    def test_no_rule_for_an_ordinary_call(self):
        for c in ("ls -la", "git status", "python3 -c 'print(1)'"):
            self.assertEqual(fired(ctx_bash(c)), [], c)
        c = rules.build_ctx({"tool_name": "Write", "tool_input": {"file_path": "/tmp/x.py", "content": "x"}},
                            "Write")
        self.assertEqual(rules.prefilter_matches(c, {}), [])


class TestConfigOverrides(unittest.TestCase):
    def test_off_skips_the_rule_entirely(self):
        c = ctx_bash("xdg-open https://x")
        self.assertEqual(rules.prefilter_matches(c, {"R6-gui-or-browser": "off"}), [])

    def test_action_override_applies(self):
        c = ctx_bash("xdg-open https://x")
        rows = rules.dry_run(c, overrides={"R6-gui-or-browser": "warn"})
        self.assertEqual(rows[0]["action"], "warn")

    def test_load_action_overrides_reads_json(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"R5-sudo": "log", "R-nope": "deny", "R6-gui-or-browser": "bogus"}, f)
            path = f.name
        try:
            self.assertEqual(rules.load_action_overrides(path), {"R5-sudo": "log"})
        finally:
            os.unlink(path)

    def test_missing_config_is_empty(self):
        self.assertEqual(rules.load_action_overrides("/nonexistent/rules.json"), {})


class TestEnforcePath(unittest.TestCase):
    def setUp(self):
        self.logged = []
        patcher = mock.patch("airlock.log.append", side_effect=self.logged.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        # R6 is pinned ON rather than left at its default, because its default
        # is `off` on EVERY platform now and several tests below use `xdg-open`
        # as a convenient code-only deny. Pinning keeps them about the ENFORCE
        # path rather than about this machine's R6 policy.
        ov = mock.patch("airlock.rules.load_action_overrides",
                        return_value={"R6-gui-or-browser": "deny"})
        ov.start()
        self.addCleanup(ov.stop)

    def _payload(self, command, **ti):
        ti["command"] = command
        return {"session_id": "sess-test-1", "cwd": "/tmp", "tool_name": "Bash", "tool_input": ti}

    def test_code_only_deny_emits_and_makes_no_jev_call(self):
        with mock.patch("airlock.client.ask", side_effect=AssertionError("no Jev call expected")), \
             mock.patch.object(enforce, "emit_deny") as emit, \
             mock.patch("airlock.state.was_recently_denied", return_value=False), \
             mock.patch("airlock.state.record_denial"):
            denied = enforce.handle(self._payload("xdg-open https://x"), "Bash")
        self.assertTrue(denied)
        emit.assert_called_once()
        self.assertIn("R6-gui-or-browser", emit.call_args[0][0])
        self.assertTrue(self.logged[-1]["enforced"])

    def test_override_stamp_allows_a_deny(self):
        payload = self._payload("xdg-open https://x", description="print it [jev-ok: user asked for the URL]")
        with mock.patch.object(enforce, "emit_deny") as emit:
            denied = enforce.handle(payload, "Bash")
        self.assertFalse(denied)
        emit.assert_not_called()
        self.assertTrue(self.logged[-1]["override"])

    def test_loop_protection_allows_the_second_identical_deny(self):
        payload = self._payload("sudo systemctl restart nginx")
        with mock.patch("airlock.state.was_recently_denied", return_value=True), \
             mock.patch.object(enforce, "emit_deny") as emit:
            denied = enforce.handle(payload, "Bash")
        self.assertFalse(denied)
        emit.assert_not_called()
        self.assertTrue(self.logged[-1]["loop_allow"])

    def test_warn_never_blocks_and_returns_advice(self):
        with mock.patch.object(enforce, "emit_deny") as deny, \
             mock.patch.object(enforce, "emit_warn") as warn:
            denied = enforce.handle(self._payload("pytest"), "Bash")
        self.assertFalse(denied)
        deny.assert_not_called()
        warn.assert_called_once()
        self.assertIn("R3-whole-suite", warn.call_args[0][0][0])

    def test_no_match_writes_nothing(self):
        with mock.patch("airlock.client.ask", side_effect=AssertionError("no Jev call expected")):
            denied = enforce.handle(self._payload("ls -la"), "Bash")
        self.assertFalse(denied)
        self.assertEqual(self.logged, [])

    def test_jev_failure_fails_open(self):
        with mock.patch("airlock.client.ask", side_effect=RuntimeError("boom")), \
             mock.patch.object(enforce, "emit_deny") as emit:
            denied = enforce.handle(self._payload("cat ~/notes/secrets.txt"), "Bash")
        self.assertFalse(denied)
        emit.assert_not_called()
        self.assertIn("error", self.logged[-1])

    def test_budget_exceeded_never_denies(self):
        answers = {"prints_a_secret": {"choice": "yes", "confidence": 0.99,
                                       "probabilities": {"yes": 0.99, "no": 0.01}}}

        def slow_ask(payload, timeout_s=None):
            import time as _t
            _t.sleep(0.05)
            return {"answers": answers}, 50

        with mock.patch.dict(os.environ, {"AIRLOCK_BUDGET_MS": "1"}), \
             mock.patch("airlock.client.ask", side_effect=slow_ask), \
             mock.patch.object(enforce, "emit_deny") as emit:
            denied = enforce.handle(self._payload("cat ~/notes/secrets.txt"), "Bash")
        self.assertFalse(denied)
        emit.assert_not_called()

    def test_shadow_mode_logs_but_never_emits(self):
        with mock.patch.object(enforce, "emit_deny") as deny, \
             mock.patch.object(enforce, "emit_warn") as warn, \
             mock.patch("airlock.state.was_recently_denied", return_value=False):
            denied = enforce.handle(self._payload("xdg-open https://x"), "Bash", mode="shadow")
        self.assertFalse(denied)
        deny.assert_not_called()
        warn.assert_not_called()
        self.assertTrue(self.logged[-1]["would_enforce"])
        self.assertFalse(self.logged[-1]["enforced"])

    def test_deny_wins_over_warn_when_both_match(self):
        payload = self._payload("sudo pip install torch")
        with mock.patch("airlock.client.ask", side_effect=AssertionError("no Jev call expected")), \
             mock.patch("airlock.state.was_recently_denied", return_value=False), \
             mock.patch("airlock.state.record_denial"), \
             mock.patch.object(enforce, "emit_deny") as deny, \
             mock.patch.object(enforce, "emit_warn") as warn:
            denied = enforce.handle(payload, "Bash")
        self.assertTrue(denied)
        warn.assert_not_called()
        self.assertIn("R5-sudo", deny.call_args[0][0])

    def test_emit_warn_shape_never_denies(self):
        import io
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            enforce.emit_warn(["advice one"])
        out = json.loads(buf.getvalue())
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertNotIn("permissionDecision", out["hookSpecificOutput"])
        self.assertIn("advice one", out["systemMessage"])


if __name__ == "__main__":
    unittest.main()


class QuotedTextAndHeredocTests(unittest.TestCase):
    """Regression for the first live false block (2026-09-19): code rules fired
    on words inside data (a heredoc body, a quoted argument), not on commands."""

    def _matches(self, command):
        from airlock import rules
        ctx = rules.build_ctx({"tool_name": "Bash", "cwd": "/tmp",
                               "tool_input": {"command": command, "description": "t"}})
        return [r.id for r, _m in rules.prefilter_matches(ctx)]

    def test_word_inside_heredoc_body_is_not_a_command(self):
        cmd = "cat > /tmp/note.md <<'EOF'\nR5 covers sudo outside a named package install\nxdg-open is blocked too\ncat ~/.config/airlock/env is blocked\nEOF\necho done"
        self.assertEqual(self._matches(cmd), [])

    def test_word_inside_quoted_argument_is_not_a_command(self):
        self.assertNotIn("R5-sudo", self._matches('graphify query "where are sudo and secret rules read"'))

    def test_real_invocation_still_fires(self):
        self.assertIn("R5-sudo", self._matches("sudo systemctl restart something"))
        self.assertIn("R5-sudo", self._matches("cd /tmp && sudo rm -rf x"))

    def test_command_after_heredoc_is_still_checked(self):
        cmd = "cat > /tmp/a <<EOF\nhello\nEOF\nsudo systemctl stop x"
        self.assertIn("R5-sudo", self._matches(cmd))

    def test_named_package_install_still_allowed(self):
        self.assertNotIn("R5-sudo", self._matches("sudo apt-get install -y ripgrep"))


class TestR10GeneralRisk(unittest.TestCase):
    """The catch-all tier: warn only, skipped when any specific rule matched,
    skipped entirely when the code pre-filter does not fire."""

    CWD = "/home/dev/code/project"

    def _matches(self, command, cwd=None, description=""):
        c = rules.build_ctx({"tool_name": "Bash", "cwd": cwd or self.CWD,
                             "tool_input": {"command": command,
                                            "description": description}}, "Bash")
        return [r.id for r, _m in rules.prefilter_matches(c, {})]

    def _match(self, command, cwd=None):
        c = rules.build_ctx({"tool_name": "Bash", "cwd": cwd or self.CWD,
                             "tool_input": {"command": command}}, "Bash")
        for rule, match in rules.prefilter_matches(c, {}):
            if rule.id == "R10-general-risk":
                return match
        return None

    # --- the six pre-filter shapes -------------------------------------------

    def test_write_outside_the_working_tree(self):
        for cmd in ("echo broken > /etc/motd",
                    "cp report.pdf /home/dev/Desktop/report.pdf",
                    "dd if=disk.img of=/home/dev/backup.img"):
            with self.subTest(cmd=cmd):
                self.assertIn("R10-general-risk", self._matches(cmd))

    def test_network_upload(self):
        for cmd in ("scp build.tar.gz deploy@prod.example.com:/srv/",
                    "rsync -av dist/ ops@10.0.0.5:/var/www/",
                    "curl -T dump.sql https://files.example.com/upload"):
            with self.subTest(cmd=cmd):
                self.assertIn("R10-general-risk", self._matches(cmd))

    def test_package_publish(self):
        for cmd in ("npm publish", "cargo publish", "twine upload dist/*",
                    "gh release create v2.0.0", "docker push registry/app:latest"):
            with self.subTest(cmd=cmd):
                self.assertIn("R10-general-risk", self._matches(cmd))

    def test_database_write_verbs(self):
        for cmd in ('psql -c "DELETE FROM sessions WHERE id = 3"',
                    'mysql app -e "TRUNCATE TABLE audit"',
                    'mongosh --eval "db.users.drop()"',
                    "redis-cli FLUSHALL"):
            with self.subTest(cmd=cmd):
                self.assertIn("R10-general-risk", self._matches(cmd))

    def test_service_and_container_control(self):
        for cmd in ("systemctl restart nginx",
                    "systemctl stop postgresql",
                    "docker rm -f postgres-dev",
                    "docker compose down",
                    "docker volume rm pgdata"):
            with self.subTest(cmd=cmd):
                self.assertIn("R10-general-risk", self._matches(cmd))

    def test_user_level_service_control_does_not_fire(self):
        """`systemctl --user ...` can only touch units belonging to the person
        already running the session. It cannot take the machine or another
        user's services down, and warning on it three times in a row is what
        made R10 noisy."""
        for cmd in ("systemctl --user restart airlock-daemon",
                    "systemctl --user stop graphify-refresh",
                    "systemctl --user daemon-reload",
                    "systemctl --user start plocate-home.service"):
            with self.subTest(cmd=cmd):
                self.assertNotIn("R10-general-risk", self._matches(cmd))

    def test_read_only_docker_does_not_fire(self):
        """A docker command that only reads changes nothing. The grouped verbs
        carry their real verb in the next word, so matching on the group alone
        (`docker system`, `docker compose`) over-warned."""
        for cmd in ("docker ps -a", "docker logs airlock", "docker inspect pg",
                    "docker images", "docker system df", "docker compose ps",
                    "docker compose logs -f", "docker volume ls",
                    "docker network ls", "podman ps"):
            with self.subTest(cmd=cmd):
                self.assertNotIn("R10-general-risk", self._matches(cmd))

    def test_mass_file_operations_high_in_the_tree(self):
        for cmd in ("rm -rf /home/dev/*/node_modules",
                    "chmod -R 777 /home/dev/*"):
            with self.subTest(cmd=cmd):
                self.assertIn("R10-general-risk", self._matches(cmd))

    def test_find_delete_matches_the_prefilter(self):
        """`find` is search-like, so the legacy tool-choice guard claims this
        call first and the fallback correctly stands down. The pre-filter
        itself still recognises it, which is what matters if that rule is ever
        switched off."""
        c = rules.build_ctx({"tool_name": "Bash", "cwd": self.CWD,
                             "tool_input": {"command": "find /home/dev -name '*.log' -delete"}},
                            "Bash")
        self.assertIsNotNone(rules.prefilter_general_risk(c))
        self.assertNotIn("R10-general-risk", self._matches("find /home/dev -name '*.log' -delete"))

    # --- what must NOT fire ---------------------------------------------------

    def test_ordinary_work_does_not_fire(self):
        for cmd in ("ls -la", "git status", "npm run build",
                    "cat notes.txt > out.txt",
                    "python3 -m unittest tests.test_rules",
                    "curl -s https://api.example.com/health",
                    "scp deploy@prod.example.com:/srv/log.txt .",
                    "psql -c \"SELECT count(*) FROM jobs\"",
                    "docker ps", "gh release list",
                    "rsync -av dist/ /tmp/staging/"):
            with self.subTest(cmd=cmd):
                self.assertNotIn("R10-general-risk", self._matches(cmd))

    def test_a_write_inside_the_working_tree_does_not_fire(self):
        self.assertNotIn("R10-general-risk",
                         self._matches("echo x > %s/build/out.txt" % self.CWD))

    def test_a_write_into_temp_does_not_fire(self):
        self.assertNotIn("R10-general-risk", self._matches("echo x > /tmp/scratch"))

    # --- the fallback contract ------------------------------------------------

    def test_skipped_when_a_specific_rule_already_matched(self):
        ids = self._matches("sudo systemctl restart nginx")
        self.assertIn("R5-sudo", ids)
        self.assertNotIn("R10-general-risk", ids)

    def test_it_is_warn_and_only_warn(self):
        rule = rules.RULES_BY_ID["R10-general-risk"]
        self.assertEqual(rule.action, "warn")
        self.assertTrue(rule.fallback)

    def test_the_prefilter_always_asks(self):
        match = self._match("npm publish")
        self.assertIsNotNone(match)
        self.assertTrue(match.ask)

    # --- the questions and the verdict ---------------------------------------

    def test_questions_are_one_score_and_one_noul(self):
        c = rules.build_ctx({"tool_name": "Bash", "cwd": self.CWD,
                             "tool_input": {"command": "npm publish"}}, "Bash")
        match = self._match("npm publish")
        state, qs = rules.questions_general_risk(c, match)
        self.assertEqual(qs["risk"]["type"], "score")
        # An ordered list, lowest first: the API returns a float index into
        # it and rejects a dict with HTTP 422 (measured against jev-1.13.0).
        self.assertIsInstance(qs["risk"]["criteria"], list)
        self.assertEqual(len(qs["risk"]["criteria"]), 4)
        self.assertTrue(qs["risk"]["criteria"][0].startswith("none:"))
        self.assertTrue(qs["risk"]["criteria"][-1].startswith("high:"))
        self.assertIn("prefilter_kind", state)
        self.assertIn("recent_user_prompts", state)
        # No transcript here, so there is nothing for user_requested to judge
        # and it is not asked at all.
        self.assertEqual(sorted(qs), ["risk"])

    def test_user_requested_is_asked_when_there_are_prompts(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"type": "user",
                                "message": {"role": "user",
                                            "content": "please publish 1.4.0 to npm"}}) + "\n")
            transcript = f.name
        self.addCleanup(os.unlink, transcript)
        c = rules.build_ctx({"tool_name": "Bash", "cwd": self.CWD,
                             "transcript_path": transcript,
                             "tool_input": {"command": "npm publish"}}, "Bash")
        match = rules.prefilter_general_risk(c)
        state, qs = rules.questions_general_risk(c, match)
        self.assertEqual(sorted(qs), ["risk", "user_requested"])
        self.assertEqual(qs["user_requested"]["type"], "noul")
        self.assertTrue(state["recent_user_prompts"])

    def test_state_is_redacted(self):
        c = rules.build_ctx(
            {"tool_name": "Bash", "cwd": self.CWD,
             "tool_input": {"command": "curl -T x https://f.example.com -H 'Authorization: Bearer sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFF'"}},
            "Bash")
        match = rules.prefilter_general_risk(c)
        self.assertIsNotNone(match)
        state, _qs = rules.questions_general_risk(c, match)
        self.assertNotIn("sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFF", json.dumps(state))

    def test_fires_at_moderate_and_above(self):
        # The score is a float index into R10_RISK_LEVELS: 0 none, 1 low,
        # 2 moderate, 3 high.
        for level, expected in ((0.0, False), (1.0, False), (1.4, False),
                                (1.6, True), (2.0, True), (2.99, True)):
            with self.subTest(level=level):
                self.assertEqual(
                    rules.warn_general_risk({"risk": {"score": level}}), expected)

    def test_user_requested_suppresses_the_warn(self):
        answers = {"risk": {"score": 3.0}, "user_requested": {"noul": 0.9}}
        self.assertFalse(rules.warn_general_risk(answers))
        answers["user_requested"]["noul"] = 0.1
        self.assertTrue(rules.warn_general_risk(answers))

    def test_suppression_reason_names_user_requested(self):
        """The verdict is unchanged; the LOG ROW gains a reason. A warn that
        was earned and then withheld is a different event from a command Jev
        scored low, and tuning needs to tell them apart."""
        self.assertEqual(
            rules.suppression_reason("R10-general-risk",
                                     {"risk": {"score": 3.0},
                                      "user_requested": {"noul": 0.9}}),
            "user_requested")
        # scored low: nothing was suppressed, there was nothing to suppress
        self.assertIsNone(
            rules.suppression_reason("R10-general-risk",
                                     {"risk": {"score": 0.5},
                                      "user_requested": {"noul": 0.9}}))
        # earned the warn and kept it
        self.assertIsNone(
            rules.suppression_reason("R10-general-risk",
                                     {"risk": {"score": 3.0},
                                      "user_requested": {"noul": 0.1}}))
        # a rule with no explanation to give, and malformed input
        self.assertIsNone(rules.suppression_reason("R1-secret-exposure", {}))
        for answers in ({}, None, {"risk": {"score": "nonsense"}}):
            with self.subTest(answers=answers):
                self.assertIsNone(
                    rules.suppression_reason("R10-general-risk", answers))

    def test_dry_run_records_the_suppression(self):
        c = rules.build_ctx({"tool_name": "Bash", "cwd": self.CWD,
                             "tool_input": {"command": "npm publish"}}, "Bash")
        rows = rules.dry_run(c, ask=lambda r, ctx, m: {"risk": {"score": 3.0},
                                                       "user_requested": {"noul": 0.9}},
                             overrides={})
        row = [r for r in rows if r["rule_id"] == "R10-general-risk"][0]
        self.assertFalse(row["fires"])
        self.assertEqual(row["suppressed"], "user_requested")

    def test_user_requested_can_never_create_a_warn(self):
        self.assertFalse(rules.warn_general_risk(
            {"risk": {"score": 0.0}, "user_requested": {"noul": 0.0}}))

    def test_a_missing_or_malformed_answer_never_fires(self):
        for answers in ({}, {"risk": {}}, {"risk": {"score": None}},
                        {"risk": {"score": "nonsense"}}, {"risk": []}, None):
            with self.subTest(answers=answers):
                self.assertFalse(rules.warn_general_risk(answers))

    def test_the_level_list_order_is_load_bearing(self):
        self.assertEqual(len(rules.R10_RISK_LEVELS), 4)
        self.assertLess(rules.R10_FIRE_AT, 2.0)
        self.assertGreater(rules.R10_FIRE_AT, 1.0)

    def test_it_never_denies_even_at_the_top_level(self):
        """Whatever Jev answers, the effective action stays warn: the rule's
        default is warn and only a config override could change it."""
        c = rules.build_ctx({"tool_name": "Bash", "cwd": self.CWD,
                             "tool_input": {"command": "npm publish"}}, "Bash")
        rows = rules.dry_run(c, ask=lambda r, ctx, m: {"risk": {"score": 3.0},
                                                       "user_requested": {"noul": 0.0}},
                             overrides={})
        row = [r for r in rows if r["rule_id"] == "R10-general-risk"][0]
        self.assertTrue(row["fires"])
        self.assertEqual(row["action"], "warn")


class TestExtraSecretPaths(unittest.TestCase):
    """A machine that keeps its key outside the default location names that
    path in install/config.env as AIRLOCK_EXTRA_SECRET_PATHS, and R1 protects
    it. Nothing is baked into rules.py, so the public code carries no
    organisation's directory layout."""

    def test_nothing_configured_means_no_extra_patterns(self):
        with mock.patch.dict(os.environ, {"AIRLOCK_EXTRA_SECRET_PATHS": ""}):
            self.assertEqual(rules._extra_secret_path_res(), [])

    def test_a_configured_path_becomes_a_matching_pattern(self):
        with mock.patch.dict(
            os.environ,
            {"AIRLOCK_EXTRA_SECRET_PATHS": "~/.config/elsewhere/env"},
        ):
            res = rules._extra_secret_path_res()
        self.assertEqual(len(res), 1)
        self.assertTrue(res[0].search("cat ~/.config/elsewhere/env"))
        self.assertTrue(res[0].search("cat /home/user/.config/elsewhere/env"))
        self.assertFalse(res[0].search("cat ~/.config/other/env"))

    def test_several_paths_are_colon_separated(self):
        value = os.pathsep.join(("~/.config/a/env", "$HOME/.config/b/env"))
        with mock.patch.dict(os.environ, {"AIRLOCK_EXTRA_SECRET_PATHS": value}):
            res = rules._extra_secret_path_res()
        self.assertEqual(len(res), 2)
        self.assertTrue(res[1].search("cat ~/.config/b/env"))

    def test_a_broken_value_never_raises(self):
        # os.pathsep, not a literal ":": the variable is split on the
        # platform's own PATH separator, which is ";" on Windows, where ":::"
        # is one perfectly ordinary token rather than three empty ones.
        with mock.patch.dict(os.environ,
                             {"AIRLOCK_EXTRA_SECRET_PATHS": os.pathsep * 3}):
            self.assertEqual(rules._extra_secret_path_res(), [])


class TestR1ProtectsTheKeyFilePointer(unittest.TestCase):
    """R1 must protect BOTH the pointer file and whatever it names, and must
    read the pointer at HOOK time: install/install.sh writes it AFTER a release
    is deployed, so a table frozen at import would never see it."""

    def setUp(self):
        from airlock import keyfile
        self.keyfile = keyfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = os.path.join(self.tmp.name, ".config", "airlock")
        os.makedirs(self.config)
        os.chmod(self.config, 0o700)
        self.pointer = os.path.join(self.config, "keyfile.path")
        self.target = os.path.join(self.tmp.name, "secrets", "typesafe.env")
        os.makedirs(os.path.dirname(self.target))
        open(self.target, "w").close()
        os.chmod(self.target, 0o600)
        self._patch = mock.patch.object(
            self.keyfile.paths, "config_file",
            lambda name: __import__("pathlib").Path(self.config) / name)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        rules._POINTER_CACHE["stamp"] = None
        rules._POINTER_CACHE["res"] = ()
        self.addCleanup(lambda: rules._POINTER_CACHE.update({"stamp": None, "res": ()}))

    def _write_pointer(self, value=None):
        with open(self.pointer, "w") as f:
            f.write((value if value is not None else self.target) + "\n")
        os.chmod(self.pointer, 0o600)

    def test_the_pointer_file_itself_is_protected(self):
        self._write_pointer()
        self.assertTrue(fired(ctx_bash("cat %s" % self.pointer), "R1-secret-exposure")[0]["fires"])

    def test_the_pointer_is_protected_even_before_it_exists(self):
        """Nothing has written it yet: printing it is still not something to do,
        and the path is known from the config dir alone."""
        self.assertTrue(fired(ctx_bash("cat %s" % self.pointer), "R1-secret-exposure")[0]["fires"])

    def test_the_target_is_protected(self):
        self._write_pointer()
        self.assertTrue(fired(ctx_bash("cat %s" % self.target), "R1-secret-exposure")[0]["fires"])

    def test_the_target_is_protected_for_read_too(self):
        self._write_pointer()
        c = rules.build_ctx({"tool_name": "Read", "tool_input": {"file_path": self.target}}, "Read")
        self.assertTrue(fired(c, "R1-secret-exposure")[0]["fires"])

    def test_a_pointer_written_after_the_first_call_is_picked_up(self):
        """The cache keys on the pointer's stat, not on process lifetime."""
        before = fired(ctx_bash("cat %s" % self.target), "R1-secret-exposure")
        self.assertFalse(any(r["fires"] for r in before))
        self._write_pointer()
        after = fired(ctx_bash("cat %s" % self.target), "R1-secret-exposure")
        self.assertTrue(after[0]["fires"])

    def test_a_repointed_pointer_protects_the_new_target(self):
        self._write_pointer()
        self.assertTrue(fired(ctx_bash("cat %s" % self.target), "R1-secret-exposure")[0]["fires"])
        moved = os.path.join(self.tmp.name, "secrets", "moved.env")
        open(moved, "w").close()
        os.chmod(moved, 0o600)
        self._write_pointer(moved)
        self.assertTrue(fired(ctx_bash("cat %s" % moved), "R1-secret-exposure")[0]["fires"])

    @posix_only("refusing an untrusted pointer is a uid + chmod check; on "
                "Windows the pointer is followed and the gap is recorded "
                "instead -- see TestPointerTrustOnWindows in test_keyfile.py")
    def test_an_untrusted_pointer_still_protects_its_target(self):
        """keyfile.py refuses to FOLLOW a group-writable pointer, but the path
        it names is still the next file a transcript would be told to read."""
        self._write_pointer()
        os.chmod(self.pointer, 0o660)
        self.assertIsNone(self.keyfile.pointer_target())
        self.assertTrue(fired(ctx_bash("cat %s" % self.target), "R1-secret-exposure")[0]["fires"])

    def test_an_ordinary_file_beside_the_target_is_not_protected(self):
        self._write_pointer()
        other = os.path.join(self.tmp.name, "secrets", "notes.md")
        open(other, "w").close()
        rows = fired(ctx_bash("cat %s" % other), "R1-secret-exposure")
        self.assertFalse(any(r["fires"] for r in rows))

    def test_a_broken_pointer_read_does_not_break_r1(self):
        """Fail open to the static table rather than raising in the hot path."""
        with mock.patch.object(rules.keyfile, "pointer_file_path",
                               side_effect=OSError("boom")):
            rules._POINTER_CACHE["stamp"] = None
            self.assertEqual(rules.secret_path_res(), tuple(rules.SECRET_PATH_RES))
            self.assertTrue(fired(ctx_bash("cat ~/.config/airlock/env"),
                                  "R1-secret-exposure")[0]["fires"])


class TestR11BrowseViaJev(unittest.TestCase):
    """R11 steers a browse-and-report pass at the Jev-decided browser agent.
    Detection is code; the browse-or-test half is Jev's; every failure of Jev
    allows the call and prints the recipe instead."""

    RID = "R11-browse-via-jev"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)

    def _script(self, name, body):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as f:
            f.write(body)
        return path

    def ctx_mcp(self, tool_name, **ti):
        return rules.build_ctx({"tool_name": tool_name, "tool_input": ti, "cwd": self.tmp},
                               tool_name)

    def ctx(self, command):
        return rules.build_ctx({"tool_name": "Bash", "tool_input": {"command": command},
                                "cwd": self.tmp}, "Bash")

    def assert_asks(self, ctx, why=""):
        rows = fired(ctx, self.RID)
        self.assertTrue(rows, "R11 did not match: %s" % (why or ctx.get("command")))
        self.assertIsNone(rows[0]["fires"], "R11 decided without Jev: %s" % why)

    def assert_silent(self, ctx, why=""):
        self.assertEqual(fired(ctx, self.RID), [], why or ctx.get("command"))

    # --- detection: the positives --------------------------------------------

    def test_a_node_script_importing_playwright_is_caught(self):
        for name, body in (
            ("verify.cjs", "const { chromium } = require('playwright');\n"),
            ("verify.mjs", "import { chromium } from 'playwright-core';\n"),
            ("verify.js", "const pw = await import('playwright/test');\n"),
        ):
            path = self._script(name, body)
            self.assert_asks(self.ctx("node %s" % path), name)

    def test_a_python_script_importing_playwright_is_caught(self):
        path = self._script("verify.py", "from playwright.sync_api import sync_playwright\n")
        self.assert_asks(self.ctx("python3 %s" % path))
        self.assert_asks(self.ctx("uv run python3 %s" % path))

    def test_an_inline_script_is_caught(self):
        self.assert_asks(self.ctx("node -e \"const {chromium}=require('playwright')\""))
        self.assert_asks(self.ctx("python3 -c 'from playwright.sync_api import sync_playwright'"))

    def test_playwright_cli_other_than_the_tooling_verbs_is_caught(self):
        for c in ("npx playwright open https://example.com",
                  "npx playwright screenshot https://example.com out.png",
                  "playwright codegen https://example.com"):
            self.assert_asks(self.ctx(c), c)

    def test_a_playwright_mcp_browsing_call_is_caught(self):
        for tool in ("mcp__playwright__browser_navigate",
                     "mcp__playwright__browser_click",
                     "mcp__playwright__browser_take_screenshot",
                     "mcp__plugin_playwright_playwright__browser_navigate",
                     "mcp__plugin_playwright_playwright__browser_type"):
            self.assert_asks(self.ctx_mcp(tool, url="https://example.com"), tool)

    # --- detection: the negatives --------------------------------------------

    def test_playwright_test_and_the_tooling_verbs_are_allowed(self):
        for c in ("npx playwright test", "npx playwright test e2e/login.spec.ts",
                  "npx playwright install chromium", "npx playwright install-deps",
                  "npx playwright show-report", "playwright test --grep login"):
            self.assert_silent(self.ctx(c), c)

    def test_test_runners_are_allowed_even_with_a_playwright_script_named(self):
        self._script("e2e.spec.ts", "import { test } from 'playwright/test';\n")
        for c in ("vitest run e2e.spec.ts", "npm test", "pnpm run test:e2e",
                  "pytest tests/test_browser.py", "python3 -m pytest tests/test_browser.py",
                  "npx vitest run"):
            self.assert_silent(self.ctx(c), c)

    def test_a_script_that_does_not_touch_playwright_is_allowed(self):
        path = self._script("build.js", "const fs = require('fs');\n")
        self.assert_silent(self.ctx("node %s" % path))

    def test_running_the_jev_browser_agent_is_never_caught(self):
        path = self._script("run_goal.py", "from playwright.sync_api import sync_playwright\n")
        self.assert_silent(self.ctx("BU_CDP_URL=http://127.0.0.1:9333 python3 %s" % path))
        self.assert_silent(self.ctx("cd ~/code/jev-ultrafast && uv run python3 %s" % path))

    def test_a_mention_of_the_agent_elsewhere_does_not_exempt_the_line(self):
        """The marker is read per segment. A segment that merely TALKS about
        the agent must not exempt a sibling segment that drives a browser."""
        path = self._script("verify.cjs", "const { chromium } = require('playwright');\n")
        self.assert_asks(self.ctx("echo 'not using jev-ultrafast yet' && node %s" % path))
        self.assert_asks(self.ctx("echo BU_CDP_URL is unset ; node %s" % path))

    def test_uv_run_flag_values_are_not_mistaken_for_the_program(self):
        """`uv run --with <pkg> python3 verify.py` runs python3. Dropping only
        the tokens starting with a dash would make the program `<pkg>`."""
        path = self._script("verify.py", "from playwright.sync_api import sync_playwright\n")
        for c in ("uv run --with playwright-stealth python3 %s" % path,
                  "uv run --python 3.12 python3 %s" % path,
                  "uv run --env-file .env python3 %s" % path,
                  "uv run --no-sync -- python3 %s" % path):
            self.assert_asks(self.ctx(c), c)
        self.assertEqual(rules._uv_run_program([]), (None, []))
        self.assertEqual(rules._uv_run_program(["--with", "x"]), (None, []))
        self.assertEqual(rules._uv_run_program(["--env-file=.env", "node", "a.js"]),
                         ("node", ["a.js"]))

    def test_a_package_script_that_drives_a_browser_is_followed(self):
        """`npm run scrape` says nothing by itself. The script it names does."""
        self._script("scrape.js", "const { chromium } = require('playwright');\n")
        self._script("build.js", "const fs = require('fs');\n")
        self._script("package.json", json.dumps({"scripts": {
            "scrape": "node scrape.js",
            "build": "node build.js",
            "test:e2e": "node scrape.js",
        }}))
        for c in ("npm run scrape", "pnpm run scrape", "yarn run scrape"):
            self.assert_asks(self.ctx(c), c)
        self.assert_silent(self.ctx("npm run build"))
        # A script whose NAME says tests is still a test run, unfollowed.
        self.assert_silent(self.ctx("npm run test:e2e"))
        self.assert_silent(self.ctx("npm run nonexistent"))

    def test_a_later_script_argument_is_checked_too(self):
        self._script("loader.mjs", "import './worker.mjs';\n")
        self._script("worker.mjs", "import { chromium } from 'playwright';\n")
        self.assert_asks(self.ctx("node %s/loader.mjs %s/worker.mjs" % (self.tmp, self.tmp)))

    def test_uv_run_boolean_flags_are_not_given_a_value(self):
        path = self._script("verify.py", "from playwright.sync_api import sync_playwright\n")
        for c in ("uv run --no-project python3 %s" % path,
                  "uv run --frozen python3 %s" % path):
            self.assert_asks(self.ctx(c), c)

    def test_non_browsing_mcp_and_other_servers_are_ignored(self):
        self.assert_silent(self.ctx_mcp("mcp__playwright__browser_install"), "browser_install")
        self.assert_silent(self.ctx_mcp("mcp__playwright__browser_close"), "browser_close")
        self.assert_silent(self.ctx_mcp("mcp__github__create_pr", title="x"), "other server")

    def test_an_ordinary_call_costs_nothing(self):
        for c in ("ls -la", "git status", "node build.js"):
            self.assert_silent(self.ctx(c), c)
        c = rules.build_ctx({"tool_name": "Read", "tool_input": {"file_path": "/tmp/x.py"}}, "Read")
        self.assert_silent(c, "Read")

    def test_a_missing_or_huge_script_is_read_safely(self):
        self.assertEqual(rules._pw_script_source("/nonexistent/x.js", self.tmp), "")
        self.assertEqual(rules._pw_script_source("--flag", self.tmp), "")
        self.assertEqual(rules._pw_script_source("README", self.tmp), "")
        big = self._script("big.js", "x" * (rules._PW_MAX_SCRIPT_BYTES + 1))
        self.assertEqual(rules._pw_script_source(big, self.tmp), "")

    # --- config ---------------------------------------------------------------

    def test_the_rule_is_on_by_default_on_every_platform(self):
        rule = rules.RULES_BY_ID[self.RID]
        for win in (False, True):
            self.assertEqual(rules.default_action(rule, windows=win), "deny", win)
            self.assertEqual(rules.effective_action(rule, overrides={}, windows=win), "deny", win)

    def test_the_documented_off_switch_works(self):
        path = self._script("rules.json", json.dumps({"R11-browse-via-jev": "off"}))
        self.assertEqual(rules.load_action_overrides(path), {"R11-browse-via-jev": "off"})
        ctx = self.ctx("npx playwright open https://x")
        self.assert_asks(ctx, "sanity: it matches by default")
        self.assertEqual(rules.prefilter_matches(ctx, {"R11-browse-via-jev": "off"}), [])

    # --- the message ----------------------------------------------------------

    def test_the_message_carries_the_recipe(self):
        text = rules.R11_SUGGESTION
        for fragment in ("jev-ultrafast", "BU_CDP_URL", "--remote-debugging-port",
                         "DOM extraction", "INSIDE the process", "browser/README.md",
                         "under 3", "no Claude decision calls",
                         '{"R11-browse-via-jev": "off"}'):
            self.assertIn(fragment, text, fragment)


class TestR11JevPaths(unittest.TestCase):
    """The Jev half, end to end through the enforce path. Every Jev call is
    mocked; nothing here reaches the network."""

    def setUp(self):
        self.logged = []
        p = mock.patch("airlock.log.append", side_effect=self.logged.append)
        p.start()
        self.addCleanup(p.stop)
        ov = mock.patch("airlock.rules.load_action_overrides", return_value={})
        ov.start()
        self.addCleanup(ov.stop)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.script = os.path.join(self.tmp, "verify.cjs")
        with open(self.script, "w") as f:
            f.write("const { chromium } = require('playwright');\n")

    def _payload(self):
        return {"session_id": "sess-r11", "cwd": self.tmp, "tool_name": "Bash",
                "tool_input": {"command": "node %s" % self.script}}

    @staticmethod
    def _answers(choice, confidence=0.95):
        other = (1.0 - confidence) / 2.0
        probs = {"browse_and_report": other, "test_or_tooling_code": other, "unclear": other}
        probs[choice] = confidence
        return {"purpose": {"choice": choice, "confidence": confidence, "probabilities": probs}}

    def _run(self, ask):
        with mock.patch("airlock.client.ask", side_effect=ask), \
             mock.patch("airlock.state.was_recently_denied", return_value=False), \
             mock.patch("airlock.state.record_denial"), \
             mock.patch.object(enforce, "emit_deny") as deny, \
             mock.patch.object(enforce, "emit_warn") as warn:
            denied = enforce.handle(self._payload(), "Bash")
        return denied, deny, warn

    def test_yes_denies_and_names_the_recipe(self):
        denied, deny, _ = self._run(lambda p, timeout_s=None: ({"answers": self._answers("browse_and_report")}, 20))
        self.assertTrue(denied)
        reason = deny.call_args[0][0]
        self.assertIn("R11-browse-via-jev", reason)
        self.assertIn("jev-ultrafast", reason)
        self.assertIn("BU_CDP_URL", reason)

    def test_no_allows_silently(self):
        denied, deny, warn = self._run(
            lambda p, timeout_s=None: ({"answers": self._answers("test_or_tooling_code")}, 20))
        self.assertFalse(denied)
        deny.assert_not_called()
        warn.assert_not_called()
        self.assertFalse(self.logged[-1]["fires"])

    def test_low_confidence_never_denies(self):
        denied, deny, _ = self._run(
            lambda p, timeout_s=None: ({"answers": self._answers("browse_and_report", 0.55)}, 20))
        self.assertFalse(denied)
        deny.assert_not_called()
        self.assertEqual(self.logged[-1].get("gated"), "below_deny_bar")

    def test_jev_unavailable_allows_and_still_prints_the_recipe(self):
        for exc in (RuntimeError("no key"), RuntimeError("out of tokens"),
                    OSError("timed out")):
            self.logged[:] = []
            denied, deny, warn = self._run(mock.Mock(side_effect=exc))
            self.assertFalse(denied, exc)
            deny.assert_not_called()
            warn.assert_called_once()
            self.assertIn("jev-ultrafast", warn.call_args[0][0][0])
            self.assertTrue(self.logged[-1]["advised_on_error"])
            self.assertIn("error", self.logged[-1])

    def test_an_exhausted_budget_allows_and_still_prints_the_recipe(self):
        def slow(payload, timeout_s=None):
            import time as _t
            _t.sleep(0.05)
            return {"answers": self._answers("browse_and_report")}, 50

        with mock.patch.dict(os.environ, {"AIRLOCK_BUDGET_MS": "1"}):
            denied, deny, warn = self._run(slow)
        self.assertFalse(denied)
        deny.assert_not_called()
        warn.assert_called_once()
        self.assertIn("jev-ultrafast", warn.call_args[0][0][0])

    def test_no_other_rule_advises_on_an_error(self):
        """`advise_on_error` is opt-in: every other rule stays silent when the
        judgement cannot be reached, which is the established behaviour."""
        for rule in rules.RULES:
            if rule.id != "R11-browse-via-jev":
                self.assertFalse(rule.advise_on_error, rule.id)
