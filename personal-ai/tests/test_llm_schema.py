import io
import json
import unittest
from unittest.mock import patch

from personal_ai.llm import chat


class LocalSchemaTests(unittest.TestCase):
    def test_schema_seed_and_temperature_go_only_to_loopback(self):
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        response = io.BytesIO(b'{"message":{"content":"ok"}}')
        with patch("personal_ai.llm.urllib.request.urlopen", return_value=response) as opened:
            self.assertEqual(chat([], "local:test", json_schema=schema, seed=12, temperature=0), "ok")
        request = opened.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/chat")
        self.assertEqual(body["format"], schema)
        self.assertEqual(body["options"]["seed"], 12)
        self.assertEqual(body["options"]["temperature"], 0)
        self.assertEqual(body["options"]["num_ctx"], 8192)

    def test_cloud_model_rejected_before_network(self):
        with patch("personal_ai.llm.urllib.request.urlopen") as opened:
            with self.assertRaises(ValueError):
                chat([], "model:cloud", json_schema={})
        opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
