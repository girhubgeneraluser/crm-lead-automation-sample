"""Mini JSON Schema validator."""
import unittest

import helpers  # noqa: F401  (sys.path)
from leadflow.schema import load_schema, validate


class SchemaTests(unittest.TestCase):
    def test_types_and_bool_is_not_integer(self):
        s = {"type": "object", "properties": {"n": {"type": "integer"}}}
        self.assertEqual(validate({"n": True}, s)[0]["message"], "expected integer, got boolean")
        self.assertEqual(validate({"n": 3}, s), [])

    def test_collects_all_errors(self):
        errors = validate({"event_id": "x", "form": "nope"}, load_schema("website"))
        paths = sorted(e["path"] for e in errors)
        self.assertEqual(paths, ["$.event_id", "$.form", "$.lead", "$.submitted_at"])

    def test_nested_array_paths(self):
        payload = {"event_id": "tf_12345678", "event_type": "form_response",
                   "form_response": {"form_id": "F", "submitted_at": "2026-09-01T00:00:00Z",
                                     "answers": [{"type": "email", "field": {"ref": "email"}}, {"type": "video"}]}}
        errors = validate(payload, load_schema("typeform"))
        self.assertEqual(sorted(e["path"] for e in errors),
                         ["$.form_response.answers[1].field", "$.form_response.answers[1].type"])

    def test_non_object_root(self):
        self.assertEqual(validate([1, 2], load_schema("website"))[0]["message"], "expected object, got array")


if __name__ == "__main__":
    unittest.main()
