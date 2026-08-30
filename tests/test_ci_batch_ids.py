"""Batch-qualified benchmark row ids (task #26).

Every Citation-Integrity batch numbers its rows from cidev0001, so a bare id is
ambiguous across batches. These tests pin the qualification helpers and, most
importantly, the guard that refuses to merge two batches under one tag.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "benchmarks"))
from ci_batch_ids import (  # noqa: E402
    batch_tag, qualify, qualify_all, unqualify, merge, ci_id, check_disjoint,
)


class TestBatchTag(unittest.TestCase):
    def test_batch_dir(self):
        self.assertEqual(batch_tag("data/citation_integrity/batch_dev_pilot100"),
                         "pilot100")
        self.assertEqual(batch_tag("batch_dev_fresh50"), "fresh50")

    def test_run_dir_gives_the_same_tag_as_its_batch(self):
        for run in ("batch_dev_pilot100_run",
                    "batch_dev_pilot100_run_gemma_0802",
                    "data/citation_integrity/batch_dev_pilot100_run_qwen37"):
            self.assertEqual(batch_tag(run), "pilot100", run)

    def test_trailing_separator_and_plain_tag(self):
        self.assertEqual(batch_tag("data/x/batch_dev_fresh50/"), "fresh50")
        self.assertEqual(batch_tag("pilot100"), "pilot100")   # idempotent

    def test_unprefixed_batch_dir_keeps_its_name(self):
        self.assertEqual(batch_tag("citation_integrity_runs/dev_1"), "dev_1")


class TestQualify(unittest.TestCase):
    def test_qualify_and_back(self):
        q = qualify("pilot100", "cidev0019")
        self.assertEqual(q, "pilot100:cidev0019")
        self.assertEqual(unqualify(q), ("pilot100", "cidev0019"))

    def test_qualify_is_idempotent(self):
        q = qualify("pilot100", "cidev0019")
        self.assertEqual(qualify("pilot100", q), q)
        self.assertEqual(qualify("fresh50", q), q)   # never re-tags

    def test_unqualify_tolerates_older_bare_ids(self):
        self.assertEqual(unqualify("cidev0019"), (None, "cidev0019"))

    def test_qualify_all_preserves_order(self):
        self.assertEqual(qualify_all("fresh50", ["cidev0026", "cidev0003"]),
                         ["fresh50:cidev0026", "fresh50:cidev0003"])


class TestMerge(unittest.TestCase):
    def test_colliding_ids_stay_distinct(self):
        out = merge({"data/citation_integrity/batch_dev_pilot100": {"cidev0019": "A"},
                     "data/citation_integrity/batch_dev_fresh50": {"cidev0019": "B"}})
        self.assertEqual(out, {"pilot100:cidev0019": "A", "fresh50:cidev0019": "B"})

    def test_two_inputs_with_one_tag_are_refused(self):
        # a batch dir and one of its run dirs resolve to the same tag: merging
        # them would silently drop every colliding row
        with self.assertRaises(ValueError) as cm:
            merge({"batch_dev_pilot100": {"cidev0001": "A"},
                   "batch_dev_pilot100_run_gemma_0802": {"cidev0001": "B"}})
        self.assertIn("pilot100", str(cm.exception))

    def test_plain_tags_work_too(self):
        self.assertEqual(merge({"pilot100": {"cidev0001": 1}}),
                         {"pilot100:cidev0001": 1})


class TestGroundTruthHelpers(unittest.TestCase):
    def setUp(self):
        self.a = {"cidev0001": {"ci_id": "dev/006_PMC1/PMC2_1", "label": "ACCURATE"},
                  "cidev0002": {"ci_id": "dev/006_PMC1/PMC2_2", "label": "CONTRADICT"}}
        self.b = {"cidev0001": {"ci_id": "dev/006_PMC1/PMC2_3", "label": "ACCURATE"}}

    def test_ci_id_lookup(self):
        self.assertEqual(ci_id(self.a, "cidev0002"), "dev/006_PMC1/PMC2_2")
        self.assertIsNone(ci_id(self.a, "cidev0099"))

    def test_disjoint_batches_report_nothing(self):
        self.assertEqual(check_disjoint({"pilot100": self.a, "fresh50": self.b}), [])

    def test_a_shared_row_is_reported(self):
        overlap = dict(self.b, cidev0002={"ci_id": "dev/006_PMC1/PMC2_2"})
        self.assertEqual(check_disjoint({"pilot100": self.a, "fresh50": overlap}),
                         ["dev/006_PMC1/PMC2_2"])

    def test_same_batch_twice_is_not_an_overlap(self):
        # the same rows under one tag are one batch, not two sharing rows
        self.assertEqual(check_disjoint({"pilot100": self.a}), [])


class TestRealBatchesAreDisjoint(unittest.TestCase):
    """The invariant --exclude-used is supposed to guarantee, on real data."""

    def test_pilot100_and_fresh50_share_no_row(self):
        import json
        base = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "data", "citation_integrity")
        paths = {t: os.path.join(base, d, "ci_ground_truth.json")
                 for t, d in (("pilot100", "batch_dev_pilot100"),
                              ("fresh50", "batch_dev_fresh50"))}
        if not all(os.path.exists(p) for p in paths.values()):
            self.skipTest("benchmark batches not present (gitignored data/)")
        claims = {}
        for t, p in paths.items():
            with open(p, encoding="utf-8") as f:
                claims[t] = json.load(f)["claims"]
        self.assertEqual(check_disjoint(claims), [])
        # and the thing that motivated all this: the bare ids DO collide
        self.assertTrue(set(claims["pilot100"]) & set(claims["fresh50"]))


if __name__ == "__main__":
    unittest.main()
