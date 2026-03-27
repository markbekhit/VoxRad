"""
Unit tests for llm/format.py — template selection and recommendation analysis.

These tests mock the OpenAI client so no real API calls are made.
"""
import json
import types
import unittest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Minimal stubs so importing llm.format doesn't require the full app stack
# ---------------------------------------------------------------------------

# Stub config
import sys

config_stub = types.SimpleNamespace(
    TEXT_API_KEY="test-key",
    BASE_URL="http://localhost:11434/v1",
    SELECTED_MODEL="test-model",
    save_directory="/tmp",
)

# Patch config and ui.utils before importing the module under test
sys.modules.setdefault("config", types.ModuleType("config"))
sys.modules["config.config"] = types.SimpleNamespace(config=config_stub)
sys.modules.setdefault("ui", types.ModuleType("ui"))
sys.modules["ui.utils"] = types.SimpleNamespace(update_status=lambda _: None)

import llm.format as fmt  # noqa: E402  (imported after stubs)


# ---------------------------------------------------------------------------
# Helpers to build minimal OpenAI response objects
# ---------------------------------------------------------------------------

def _tool_response(args: dict) -> MagicMock:
    """Fake response that contains a tool_call with `args` as JSON arguments."""
    tool_call = MagicMock()
    tool_call.function.arguments = json.dumps(args)
    message = MagicMock()
    message.tool_calls = [tool_call]
    message.content = None
    choice = MagicMock()
    choice.message = message
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _json_response(body: str) -> MagicMock:
    """Fake response that contains a plain-text / JSON string (no tool_calls)."""
    message = MagicMock()
    message.tool_calls = None
    message.content = body
    choice = MagicMock()
    choice.message = message
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _empty_response() -> MagicMock:
    """Fake response with no content and no tool_calls."""
    message = MagicMock()
    message.tool_calls = None
    message.content = None
    choice = MagicMock()
    choice.message = message
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# ---------------------------------------------------------------------------
# Tests for _select_template
# ---------------------------------------------------------------------------

TEMPLATES = ["CT_Head.txt", "MRI_Brain.txt", "CXR.txt"]
GUIDELINES = ["BIRADS_MAMMOGRAPHY.md", "TIRADS.md"]


class TestSelectTemplate(unittest.TestCase):

    def _patch(self, templates=None):
        """Return a context-manager that patches file-list lookup and OpenAI."""
        if templates is None:
            templates = TEMPLATES
        return patch.multiple(
            "llm.format",
            _get_file_list=MagicMock(return_value=templates),
        )

    # ------------------------------------------------------------------
    # Tool-call (first-attempt) path
    # ------------------------------------------------------------------

    def test_tool_call_success(self):
        """First attempt via tool_call returns the selected template."""
        with self._patch():
            with patch("llm.format.OpenAI") as mock_openai:
                mock_client = MagicMock()
                mock_client.chat.completions.create.return_value = _tool_response(
                    {"template": "CT_Head.txt"}
                )
                mock_openai.return_value = mock_client

                result = fmt._select_template("CT head dictation", attempt=1)

        self.assertEqual(result, "CT_Head.txt")
        self.assertEqual(mock_client.chat.completions.create.call_count, 1)

    def test_tool_call_invalid_template_falls_back_to_json(self):
        """Tool-call returns a template name NOT in the list → JSON fallback succeeds."""
        with self._patch():
            with patch("llm.format.OpenAI") as mock_openai:
                mock_client = MagicMock()
                # First call: tool_call returns an unknown template name
                mock_client.chat.completions.create.side_effect = [
                    _tool_response({"template": "NonExistent.txt"}),
                    _json_response('{"template": "MRI_Brain.txt"}'),
                ]
                mock_openai.return_value = mock_client

                # Simulate the tool_call returning an invalid name by having the
                # JSON fallback (attempt=2) succeed instead.
                result = fmt._select_template("brain MRI report", attempt=2)

        self.assertEqual(result, "MRI_Brain.txt")

    # ------------------------------------------------------------------
    # JSON fallback path
    # ------------------------------------------------------------------

    def test_json_fallback_plain(self):
        """attempt=2 skips tool_call and uses JSON-only fallback."""
        with self._patch():
            with patch("llm.format.OpenAI") as mock_openai:
                mock_client = MagicMock()
                mock_client.chat.completions.create.return_value = _json_response(
                    '{"template": "CXR.txt"}'
                )
                mock_openai.return_value = mock_client

                result = fmt._select_template("CXR report", attempt=2)

        self.assertEqual(result, "CXR.txt")

    def test_json_fallback_markdown_code_block(self):
        """JSON wrapped in a markdown code-block is correctly parsed."""
        body = "```json\n{\"template\": \"CT_Head.txt\"}\n```"
        with self._patch():
            with patch("llm.format.OpenAI") as mock_openai:
                mock_client = MagicMock()
                mock_client.chat.completions.create.return_value = _json_response(body)
                mock_openai.return_value = mock_client

                result = fmt._select_template("head CT dictation", attempt=2)

        self.assertEqual(result, "CT_Head.txt")

    def test_json_fallback_invalid_json_retries(self):
        """Malformed JSON on attempt=2 causes a recursive retry (attempt=3)."""
        with self._patch():
            with patch("llm.format.OpenAI") as mock_openai:
                mock_client = MagicMock()
                mock_client.chat.completions.create.side_effect = [
                    _json_response("not-valid-json"),           # attempt 2 → bad JSON
                    _json_response('{"template": "CXR.txt"}'),  # attempt 3 → OK
                ]
                mock_openai.return_value = mock_client

                result = fmt._select_template("chest X-ray", attempt=2)

        self.assertEqual(result, "CXR.txt")
        self.assertEqual(mock_client.chat.completions.create.call_count, 2)

    def test_json_fallback_unknown_template_retries(self):
        """JSON contains a template name not in the list → retries."""
        with self._patch():
            with patch("llm.format.OpenAI") as mock_openai:
                mock_client = MagicMock()
                mock_client.chat.completions.create.side_effect = [
                    _json_response('{"template": "Unknown.txt"}'),  # attempt 2 → bad name
                    _json_response('{"template": "CXR.txt"}'),      # attempt 3 → OK
                ]
                mock_openai.return_value = mock_client

                result = fmt._select_template("chest X-ray", attempt=2)

        self.assertEqual(result, "CXR.txt")

    def test_empty_response_retries(self):
        """Empty model response triggers a retry."""
        with self._patch():
            with patch("llm.format.OpenAI") as mock_openai:
                mock_client = MagicMock()
                mock_client.chat.completions.create.side_effect = [
                    _empty_response(),                             # attempt 2 → empty
                    _json_response('{"template": "CXR.txt"}'),    # attempt 3 → OK
                ]
                mock_openai.return_value = mock_client

                result = fmt._select_template("test", attempt=2)

        self.assertEqual(result, "CXR.txt")

    def test_max_attempts_returns_none(self):
        """Attempt > 3 returns None without making any API calls."""
        with self._patch():
            with patch("llm.format.OpenAI") as mock_openai:
                mock_client = MagicMock()
                mock_openai.return_value = mock_client

                result = fmt._select_template("anything", attempt=4)

        self.assertIsNone(result)
        mock_client.chat.completions.create.assert_not_called()

    def test_no_templates_returns_none(self):
        """Empty template directory returns None immediately."""
        with patch("llm.format._get_file_list", return_value=[]):
            with patch("llm.format.OpenAI") as mock_openai:
                mock_client = MagicMock()
                mock_openai.return_value = mock_client

                result = fmt._select_template("anything", attempt=1)

        self.assertIsNone(result)
        mock_client.chat.completions.create.assert_not_called()


# ---------------------------------------------------------------------------
# Tests for _analyze_recommendation_needs
# ---------------------------------------------------------------------------

class TestAnalyzeRecommendationNeeds(unittest.TestCase):

    def _patch(self, guidelines=None):
        if guidelines is None:
            guidelines = GUIDELINES
        return patch("llm.format._get_file_list", return_value=guidelines)

    def test_tool_call_recommendations_needed(self):
        """Tool_call path: recommendations needed with valid guidelines."""
        with self._patch():
            with patch("llm.format.OpenAI") as mock_openai:
                mock_client = MagicMock()
                mock_client.chat.completions.create.return_value = _tool_response(
                    {"recommendations_needed": True, "selected_guidelines": ["TIRADS.md"]}
                )
                mock_openai.return_value = mock_client

                needed, guides = fmt._analyze_recommendation_needs("report text", attempt=1)

        self.assertTrue(needed)
        self.assertEqual(guides, ["TIRADS.md"])

    def test_tool_call_no_recommendations(self):
        """Tool_call path: recommendations not needed, empty guideline list."""
        with self._patch():
            with patch("llm.format.OpenAI") as mock_openai:
                mock_client = MagicMock()
                mock_client.chat.completions.create.return_value = _tool_response(
                    {"recommendations_needed": False, "selected_guidelines": []}
                )
                mock_openai.return_value = mock_client

                needed, guides = fmt._analyze_recommendation_needs("report text", attempt=1)

        self.assertFalse(needed)
        self.assertEqual(guides, [])

    def test_json_fallback_recommendations_needed(self):
        """JSON fallback (attempt=2): recommendations needed."""
        with self._patch():
            with patch("llm.format.OpenAI") as mock_openai:
                mock_client = MagicMock()
                mock_client.chat.completions.create.return_value = _json_response(
                    '{"recommendations_needed": true, "selected_guidelines": ["BIRADS_MAMMOGRAPHY.md"]}'
                )
                mock_openai.return_value = mock_client

                needed, guides = fmt._analyze_recommendation_needs("report text", attempt=2)

        self.assertTrue(needed)
        self.assertEqual(guides, ["BIRADS_MAMMOGRAPHY.md"])

    def test_json_fallback_null_guidelines(self):
        """JSON fallback: null guidelines returns empty list."""
        with self._patch():
            with patch("llm.format.OpenAI") as mock_openai:
                mock_client = MagicMock()
                mock_client.chat.completions.create.return_value = _json_response(
                    '{"recommendations_needed": false, "selected_guidelines": null}'
                )
                mock_openai.return_value = mock_client

                needed, guides = fmt._analyze_recommendation_needs("report text", attempt=2)

        self.assertFalse(needed)
        self.assertEqual(guides, [])

    def test_invalid_guideline_triggers_retry(self):
        """A guideline name not in the available list causes a retry."""
        with self._patch():
            with patch("llm.format.OpenAI") as mock_openai:
                mock_client = MagicMock()
                mock_client.chat.completions.create.side_effect = [
                    _json_response(
                        '{"recommendations_needed": true, "selected_guidelines": ["UNKNOWN.md"]}'
                    ),
                    _json_response(
                        '{"recommendations_needed": true, "selected_guidelines": ["TIRADS.md"]}'
                    ),
                ]
                mock_openai.return_value = mock_client

                needed, guides = fmt._analyze_recommendation_needs("report", attempt=2)

        self.assertTrue(needed)
        self.assertEqual(guides, ["TIRADS.md"])

    def test_max_attempts_returns_false_empty(self):
        """Attempt > 3 returns (False, []) without calling the API."""
        with self._patch():
            with patch("llm.format.OpenAI") as mock_openai:
                mock_client = MagicMock()
                mock_openai.return_value = mock_client

                needed, guides = fmt._analyze_recommendation_needs("report", attempt=4)

        self.assertFalse(needed)
        self.assertEqual(guides, [])
        mock_client.chat.completions.create.assert_not_called()

    def test_malformed_json_retries(self):
        """Malformed JSON response triggers retry."""
        with self._patch():
            with patch("llm.format.OpenAI") as mock_openai:
                mock_client = MagicMock()
                mock_client.chat.completions.create.side_effect = [
                    _json_response("this is not json"),
                    _json_response(
                        '{"recommendations_needed": false, "selected_guidelines": null}'
                    ),
                ]
                mock_openai.return_value = mock_client

                needed, guides = fmt._analyze_recommendation_needs("report", attempt=2)

        self.assertFalse(needed)
        self.assertEqual(guides, [])
        self.assertEqual(mock_client.chat.completions.create.call_count, 2)


# ---------------------------------------------------------------------------
# Tests for _validate_guidelines
# ---------------------------------------------------------------------------

class TestValidateGuidelines(unittest.TestCase):

    def test_all_valid(self):
        with patch("llm.format._get_file_list", return_value=GUIDELINES):
            valid, missing = fmt._validate_guidelines(["TIRADS.md", "BIRADS_MAMMOGRAPHY.md"])
        self.assertEqual(sorted(valid), sorted(["TIRADS.md", "BIRADS_MAMMOGRAPHY.md"]))
        self.assertEqual(missing, [])

    def test_some_missing(self):
        with patch("llm.format._get_file_list", return_value=GUIDELINES):
            valid, missing = fmt._validate_guidelines(["TIRADS.md", "UNKNOWN.md"])
        self.assertEqual(valid, ["TIRADS.md"])
        self.assertEqual(missing, ["UNKNOWN.md"])

    def test_all_missing(self):
        with patch("llm.format._get_file_list", return_value=GUIDELINES):
            valid, missing = fmt._validate_guidelines(["GHOST.md"])
        self.assertEqual(valid, [])
        self.assertEqual(missing, ["GHOST.md"])


if __name__ == "__main__":
    unittest.main()
