import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from personal_ai import persona


class PersonaTests(unittest.TestCase):
    def test_defaults_and_fictional_separation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "persona.json"
            self.assertIn("助言は押し付けない", persona.load(path)["conversation_style"])
            self.assertNotIn("架空の背景設定", persona.system_prompt(path))
            persona.set_field("fictional_background", "架空の港町で育った設定", path)
            prompt = persona.system_prompt(path)
            self.assertIn("架空の背景設定（実体験ではない）", prompt)
            self.assertIn("現実に体験したと偽らない", prompt)
            self.assertEqual(persona.load(path)["fictional_background"], "架空の港町で育った設定")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_reject_unknown_and_malformed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "persona.json"
            with self.assertRaises(ValueError):
                persona.set_field("user_memory", "secret", path)
            with self.assertRaises(ValueError):
                persona.set_field("values", "x" * 2001, path)
            path.write_text(json.dumps({"version": 1, "values": "incomplete"}))
            with self.assertRaises(ValueError):
                persona.load(path)

    def test_cli_show_set(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "persona.json"
            env = {**os.environ, "PERSONAL_AI_PERSONA": str(path)}
            result = subprocess.run([sys.executable, "-m", "personal_ai.persona", "set", "likes", "詩"],
                                    capture_output=True, text=True, env=env, check=True)
            self.assertEqual(json.loads(result.stdout)["likes"], "詩")
            result = subprocess.run([sys.executable, "-m", "personal_ai.persona", "show"],
                                    capture_output=True, text=True, env=env, check=True)
            self.assertEqual(json.loads(result.stdout)["likes"], "詩")


if __name__ == "__main__":
    unittest.main()
