
import tests  # noqa: F401 -- MUST be the first import. `python3 -m unittest
# discover -s tests` runs with start_dir == top_level_dir, so unittest treats
# `tests/` as a flat directory of top-level modules and never executes
# tests/__init__.py as a package init (name == '.' in TestLoader._find_tests).
# Importing it explicitly, here, first, is what actually runs its HOME/
# AIRLOCK_*-isolating fixture before any airlock.* module resolves a real path.

import unittest

from airlock import redact


class TestRedact(unittest.TestCase):
    def assertRedacted(self, secret, text=None):
        text = text if text is not None else secret
        out = redact.redact(text)
        self.assertNotIn(secret, out, "secret leaked in: %r" % out)
        self.assertIn(redact.REDACTED, out)

    def test_apikey(self):
        self.assertRedacted("apikey_ABC123xyz789")

    def test_openai_style_key(self):
        self.assertRedacted("sk-abcdefghijklmno1234567890")

    def test_github_token(self):
        self.assertRedacted("ghp_" + "a" * 36)

    def test_slack_token(self):
        self.assertRedacted("xoxb-1234567890-abcdefghijklmno")

    def test_jwt(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dQw4w9WgXcQrandomsig"
        self.assertRedacted(jwt)

    def test_bearer_header(self):
        out = redact.redact("Authorization: Bearer abcdef123456.token")
        self.assertNotIn("abcdef123456", out)
        self.assertIn(redact.REDACTED, out)

    def test_password_equals(self):
        out = redact.redact("password=hunter2secret")
        self.assertNotIn("hunter2secret", out)

    def test_password_flag(self):
        out = redact.redact("mysql --password hunter2secret")
        self.assertNotIn("hunter2secret", out)

    def test_generic_secret_env(self):
        out = redact.redact("MY_APP_SECRET_TOKEN=abc123def456")
        self.assertNotIn("abc123def456", out)

    def test_generic_api_key_env(self):
        out = redact.redact("SOME_API_KEY=xyz987fooBAR")
        self.assertNotIn("xyz987fooBAR", out)

    def test_bare_hex_32(self):
        hexstr = "a" * 40
        out = redact.redact("token: %s end" % hexstr)
        self.assertNotIn(hexstr, out)

    def test_bare_base64ish_32(self):
        b64 = "QWxhZGRpbjpvcGVuIHNlc2FtZQnotarealsecretbutlong=="
        out = redact.redact("blob=%s" % b64)
        self.assertNotIn(b64, out)

    def test_short_strings_untouched(self):
        text = "run the tests and check status"
        self.assertEqual(redact.redact(text), text)

    def test_empty_and_none(self):
        self.assertEqual(redact.redact(""), "")
        self.assertIsNone(redact.redact(None))

    def test_prompt_truncation(self):
        long_prompt = "please read this file and summarize it " * 200
        out = redact.redact_and_truncate_prompt(long_prompt)
        self.assertEqual(len(out), redact.PROMPT_TRUNCATE)

    def test_command_truncation(self):
        long_cmd = "grep -rn something long_directory_name_here " * 100
        out = redact.redact_and_truncate_command(long_cmd)
        self.assertEqual(len(out), redact.COMMAND_TRUNCATE)

    def test_truncation_after_redaction_keeps_secret_out(self):
        # A secret near the truncation boundary must still be redacted, not
        # sliced in half and partially leaked.
        prefix = "a" * 3990
        secret = "sk-" + "b" * 30
        text = prefix + secret
        out = redact.redact_and_truncate_prompt(text)
        self.assertNotIn("b" * 30, out)


if __name__ == "__main__":
    unittest.main()
