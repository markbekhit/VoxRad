"""Tests for the HL7 file-drop path — both inbound (hl7_import.list_inbox)
and outbound (hl7_export.save_hl7_report).

These codify the robustness guarantees the file-drop depends on:
atomic writes, collision-resistant filenames, size caps, encoding fallback,
and quarantine-on-parse-failure.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from typing import Iterable


# ---------------------------------------------------------------------------
# Stub sys.modules so importing llm.hl7_import doesn't drag in config/ui
# ---------------------------------------------------------------------------
import sys
import types

sys.modules.setdefault("config", types.ModuleType("config"))
sys.modules.setdefault("config.config", types.SimpleNamespace(
    config=types.SimpleNamespace()
))

from llm.hl7_import import (  # noqa: E402
    list_inbox,
    _FAILED_SUBDIR,
    _PROCESSED_SUBDIR,
    _MAX_INBOX_FILE_BYTES,
    _MIN_INBOX_AGE_SECONDS,
)
from llm.hl7_export import save_hl7_report  # noqa: E402


_SAMPLE_ORM = (
    "MSH|^~\\&|RIS|HOSPITAL|VOXRAD|VOXRAD|20260101120000||ORM^O01|MSG0001|P|2.4\r"
    "PID|1||MRN12345||Smith^Jane^M||19800615|F\r"
    "ORC|NW|ORD001|ACC0001\r"
    "OBR|1||ACC0001|CT^CT CHEST WITH CONTRAST^L||20260101120000||||||||||||||||||||||||F||||\r"
)


def _age_file(path: str, seconds: float) -> None:
    """Set a file's mtime to N seconds in the past so it's past the defer window."""
    past = time.time() - seconds
    os.utime(path, (past, past))


def _write_aged(path: str, content: str | bytes, age: float = 5.0) -> None:
    mode = "wb" if isinstance(content, bytes) else "w"
    kwargs = {} if isinstance(content, bytes) else {"encoding": "utf-8"}
    with open(path, mode, **kwargs) as f:
        f.write(content)
    _age_file(path, age)


class ListInboxHardeningTests(unittest.TestCase):
    """list_inbox should be robust to bad inputs a production RIS might produce."""

    def test_valid_orm_parses_and_is_listed(self):
        with tempfile.TemporaryDirectory() as d:
            _write_aged(os.path.join(d, "order.hl7"), _SAMPLE_ORM)
            orders = list_inbox(d)
            self.assertEqual(len(orders), 1)
            self.assertEqual(orders[0]["accession"], "ACC0001")
            self.assertEqual(orders[0]["patient_id"], "MRN12345")

    def test_empty_file_is_skipped_not_returned(self):
        with tempfile.TemporaryDirectory() as d:
            _write_aged(os.path.join(d, "empty.hl7"), "")
            self.assertEqual(list_inbox(d), [])

    def test_too_new_file_is_deferred_not_quarantined(self):
        """A file modified in the last second may still be being written."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "fresh.hl7")
            with open(path, "w", encoding="utf-8") as f:
                f.write(_SAMPLE_ORM)
            # Don't age it — mtime is 'now'
            self.assertEqual(list_inbox(d), [])
            # And crucially, it should NOT have been quarantined.
            self.assertTrue(os.path.exists(path))
            self.assertFalse(os.path.isdir(os.path.join(d, _FAILED_SUBDIR))
                             and os.listdir(os.path.join(d, _FAILED_SUBDIR)))

    def test_tmp_suffix_is_deferred(self):
        """Atomic-write patterns use .tmp suffix — must be ignored."""
        with tempfile.TemporaryDirectory() as d:
            _write_aged(os.path.join(d, "order.hl7.tmp"), _SAMPLE_ORM)
            self.assertEqual(list_inbox(d), [])

    def test_hidden_prefix_is_deferred(self):
        """Dotfiles and .in-progress patterns must be ignored."""
        with tempfile.TemporaryDirectory() as d:
            _write_aged(os.path.join(d, ".in-progress.hl7"), _SAMPLE_ORM)
            _write_aged(os.path.join(d, "_staging.hl7"), _SAMPLE_ORM)
            self.assertEqual(list_inbox(d), [])

    def test_oversize_file_is_quarantined(self):
        """A multi-MB blob in the inbox must not OOM the worklist request."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "huge.hl7")
            # Slightly over the cap — big enough to trigger, small enough for CI.
            _write_aged(path, "X" * (_MAX_INBOX_FILE_BYTES + 1024))
            self.assertEqual(list_inbox(d), [])
            failed = os.path.join(d, _FAILED_SUBDIR)
            self.assertTrue(os.path.isdir(failed))
            quarantined = [n for n in os.listdir(failed) if n.endswith("huge.hl7")]
            self.assertEqual(len(quarantined), 1, f"Expected quarantine of huge.hl7, got {os.listdir(failed)}")

    def test_malformed_message_is_quarantined_not_repeatedly_retried(self):
        """Non-HL7 content must move to failed/ on first poll."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "junk.hl7")
            _write_aged(path, "this is not an HL7 message at all\r\njunk junk junk")
            self.assertEqual(list_inbox(d), [])
            self.assertFalse(os.path.exists(path))
            failed = os.path.join(d, _FAILED_SUBDIR)
            self.assertTrue(any(n.endswith("junk.hl7") for n in os.listdir(failed)))

    def test_non_orm_message_is_quarantined(self):
        """An ADT or ORU in the inbox is engine misconfiguration — quarantine."""
        adt = (
            "MSH|^~\\&|RIS|HOSPITAL|VOXRAD|VOXRAD|20260101120000||ADT^A01|M2|P|2.4\r"
            "PID|1||MRN123||Doe^John||19700101|M\r"
        )
        with tempfile.TemporaryDirectory() as d:
            _write_aged(os.path.join(d, "adt.hl7"), adt)
            self.assertEqual(list_inbox(d), [])
            failed_dir = os.path.join(d, _FAILED_SUBDIR)
            self.assertTrue(os.path.isdir(failed_dir))
            self.assertTrue(any("adt.hl7" in n for n in os.listdir(failed_dir)))

    def test_latin1_encoded_names_are_decoded(self):
        """Real HL7 messages carry Latin-1 in patient names."""
        orm_latin1 = (
            "MSH|^~\\&|RIS|HOSPITAL|VOXRAD|VOXRAD|20260101120000||ORM^O01|MSG|P|2.4\r"
            "PID|1||MRN9||M\xfcller^J\xf6rg||19800101|M\r"
            "ORC|NW|O1|ACC99\r"
            "OBR|1||ACC99|CT^CT HEAD^L||20260101120000||||||||||||||||||||||||F||||\r"
        ).encode("latin-1")
        with tempfile.TemporaryDirectory() as d:
            _write_aged(os.path.join(d, "latin.hl7"), orm_latin1)
            orders = list_inbox(d)
            self.assertEqual(len(orders), 1)
            # Umlaut should have survived the Latin-1 fallback decode.
            self.assertIn("Müller", orders[0].get("patient_name", ""))

    def test_processed_and_failed_subdirs_are_skipped(self):
        """Archive subdirs must never be re-processed."""
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, _PROCESSED_SUBDIR))
            os.makedirs(os.path.join(d, _FAILED_SUBDIR))
            # Put a valid ORM inside 'processed/' — it must NOT appear on the
            # worklist (previously archived files shouldn't resurrect).
            _write_aged(os.path.join(d, _PROCESSED_SUBDIR, "old.hl7"), _SAMPLE_ORM)
            _write_aged(os.path.join(d, _FAILED_SUBDIR, "bad.hl7"), "garbage")
            self.assertEqual(list_inbox(d), [])

    def test_mwl_json_is_parsed(self):
        with tempfile.TemporaryDirectory() as d:
            payload = {
                "accession": "ACC42",
                "patient_id": "MRN42",
                "patient_name": "Doe, John",
                "modality": "MR",
                "procedure": "MRI Brain",
                "source": "mwl-agent-v1",
            }
            _write_aged(os.path.join(d, "ord.json"), json.dumps(payload))
            orders = list_inbox(d)
            self.assertEqual(len(orders), 1)
            self.assertEqual(orders[0]["accession"], "ACC42")
            self.assertEqual(orders[0]["source"], "mwl-agent-v1")

    def test_mwl_invalid_json_is_quarantined(self):
        with tempfile.TemporaryDirectory() as d:
            _write_aged(os.path.join(d, "bad.json"), "{this is not json}")
            self.assertEqual(list_inbox(d), [])
            failed = os.path.join(d, _FAILED_SUBDIR)
            self.assertTrue(os.path.isdir(failed))

    def test_mwl_array_root_is_quarantined(self):
        """An MWL JSON file must be an object, not an array."""
        with tempfile.TemporaryDirectory() as d:
            _write_aged(os.path.join(d, "arr.json"), "[1,2,3]")
            self.assertEqual(list_inbox(d), [])
            failed = os.path.join(d, _FAILED_SUBDIR)
            self.assertTrue(os.path.isdir(failed))

    def test_unrelated_extension_is_ignored_not_quarantined(self):
        """Random files (.pdf, .log, etc.) are not ours — don't touch them."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "something.pdf")
            _write_aged(path, "%PDF-1.4 not really")
            self.assertEqual(list_inbox(d), [])
            # Must still be there; we ignore it, we don't quarantine it.
            self.assertTrue(os.path.exists(path))


class SaveHl7ReportTests(unittest.TestCase):
    """Outbound writes must be atomic and collision-safe."""

    def test_save_produces_valid_hl7_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = save_hl7_report(
                report_text="FINDINGS: Clear lungs.\n\nIMPRESSION: Normal.",
                outbox_path=d,
                patient_context={
                    "patient_name": "Smith, Jane",
                    "patient_id": "MRN1",
                    "accession": "ACC1",
                },
            )
            self.assertIsNotNone(path)
            self.assertTrue(os.path.exists(path))
            with open(path, encoding="utf-8") as f:
                msg = f.read()
            self.assertTrue(msg.startswith("MSH|"))
            self.assertIn("ORU^R01", msg)
            self.assertIn("ACC1", msg)

    def test_no_tmp_file_left_behind(self):
        """Successful save must clean up the staging .tmp."""
        with tempfile.TemporaryDirectory() as d:
            save_hl7_report(
                report_text="Report",
                outbox_path=d,
                patient_context={"accession": "ACC1"},
            )
            leftovers = [n for n in os.listdir(d) if n.endswith(".tmp")]
            self.assertEqual(leftovers, [])

    def test_same_accession_same_second_does_not_collide(self):
        """Two exports with identical accession + timestamp must not overwrite."""
        with tempfile.TemporaryDirectory() as d:
            ctx = {"accession": "ACC1"}
            p1 = save_hl7_report(report_text="A", outbox_path=d, patient_context=ctx)
            p2 = save_hl7_report(report_text="B", outbox_path=d, patient_context=ctx)
            self.assertNotEqual(p1, p2)
            self.assertTrue(os.path.exists(p1))
            self.assertTrue(os.path.exists(p2))

    def test_missing_outbox_returns_none(self):
        self.assertIsNone(save_hl7_report(report_text="x", outbox_path=""))

    def test_unsafe_accession_chars_are_sanitised(self):
        """Accession number containing path separators must not escape the outbox."""
        with tempfile.TemporaryDirectory() as d:
            path = save_hl7_report(
                report_text="x",
                outbox_path=d,
                patient_context={"accession": "../../etc/passwd"},
            )
            self.assertIsNotNone(path)
            # File must be inside the outbox dir.
            self.assertEqual(os.path.dirname(path), d)
            self.assertNotIn("..", os.path.basename(path))


if __name__ == "__main__":
    unittest.main()
