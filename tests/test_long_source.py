"""Offline tests for the shared long-source reading plan (task #105).

No API calls, no network. What these pin:
 1. cutting a source loses nothing and neighbouring pieces really overlap;
 2. the piece size is derived from the seat's real character ceiling, and on
    the free Google seat's 52,000 characters it lands on exactly the 40,000
    the task #15 panel already measured, so old piece counts still hold;
 3. the three size classes, and that "the source is silent" is only called
    trustworthy when every piece of the source is read;
 4. the two real long sources of card #15 price out at 28 and 35 pieces;
 5. reading order puts the piece carrying the claim's rare words first;
 6. the arbiter's source section now fits the seat's ceiling instead of being
    skipped without being sent.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import long_source as ls

FREE_SEAT_CEILING = 52_000       # llm_client._FREE_TIER_MAX_PROMPT_CHARS
THESIS_CHARS = 1_036_530         # retreat:b047, hukantaival2016.pdf
REPORT_CHARS = 1_294_440         # retreat:b071, karger2023.pdf


class FreeSeatClient:
    """A client with the free Google seat's measured character ceiling."""

    def max_prompt_chars(self):
        return FREE_SEAT_CEILING


class NoCeilingClient:
    def max_prompt_chars(self):
        return None


class ClientWithoutTheMethod:
    pass


class TestCutting(unittest.TestCase):

    def test_cutting_loses_nothing(self):
        text = "".join(f"sentence number {i}. " for i in range(4000))
        pieces = ls.split_into_pieces(text, piece_chars=5_000,
                                      overlap_chars=500)
        self.assertGreater(len(pieces), 1)
        rebuilt = pieces[0] + "".join(p[500:] for p in pieces[1:])
        self.assertEqual(rebuilt, text)

    def test_neighbours_share_the_overlap(self):
        text = "x" * 12_000
        pieces = ls.split_into_pieces(text, piece_chars=5_000, overlap_chars=500)
        for a, b in zip(pieces, pieces[1:]):
            self.assertEqual(a[-500:], b[:500])

    def test_short_source_is_one_piece(self):
        self.assertEqual(ls.split_into_pieces("short", 5_000, 500), ["short"])
        self.assertEqual(ls.piece_count(5, 5_000, 500), 1)

    def test_piece_count_agrees_with_the_pieces_it_predicts(self):
        for n in (1, 4_999, 5_000, 5_001, 9_500, 40_000, 123_456):
            text = "y" * n
            self.assertEqual(
                ls.piece_count(n, 5_000, 500),
                len(ls.split_into_pieces(text, 5_000, 500)),
                f"piece_count disagrees with split_into_pieces at {n} characters")

    def test_a_zero_or_negative_piece_size_is_refused(self):
        with self.assertRaises(ValueError):
            ls.split_into_pieces("abc", 0, 0)
        with self.assertRaises(ValueError):
            ls.piece_count(10, -1, 0)


class TestPieceSizeFromTheSeat(unittest.TestCase):

    def test_free_seat_gives_exactly_the_measured_40000(self):
        # 52,000 characters minus the 12,000 a prompt needs around the source.
        self.assertEqual(ls.piece_size_for(FREE_SEAT_CEILING), 40_000)
        self.assertEqual(ls.piece_size_for(FREE_SEAT_CEILING),
                         ls.DEFAULT_PIECE_CHARS)

    def test_no_known_ceiling_keeps_the_default(self):
        self.assertEqual(ls.piece_size_for(None), ls.DEFAULT_PIECE_CHARS)
        self.assertEqual(ls.piece_size_for(0), ls.DEFAULT_PIECE_CHARS)

    def test_a_small_ceiling_shrinks_the_piece_but_never_below_the_floor(self):
        self.assertEqual(ls.piece_size_for(30_000), 18_000)
        self.assertEqual(ls.piece_size_for(13_000), ls.MIN_PIECE_CHARS)

    def test_call_limit_is_asked_of_the_client_never_guessed(self):
        self.assertEqual(ls.call_limit_for(FreeSeatClient()), FREE_SEAT_CEILING)
        self.assertIsNone(ls.call_limit_for(NoCeilingClient()))
        self.assertIsNone(ls.call_limit_for(ClientWithoutTheMethod()))


class TestSizeClasses(unittest.TestCase):

    def test_a_source_that_fits_is_read_in_one_call(self):
        plan = ls.read_plan(30_000, FREE_SEAT_CEILING)
        self.assertEqual(plan["size_class"], ls.CLASS_ONE_CALL)
        self.assertEqual(plan["calls"], 1)
        self.assertTrue(plan["reads_whole_source"])
        self.assertTrue(plan["silence_trustworthy"])

    def test_the_prompt_room_is_counted_against_the_ceiling(self):
        # 45,000 characters of source is under the 52,000 ceiling on its own,
        # but not once the prompt around it is counted.
        self.assertTrue(ls.fits_one_call(39_000, FREE_SEAT_CEILING))
        self.assertFalse(ls.fits_one_call(45_000, FREE_SEAT_CEILING))
        self.assertEqual(ls.size_class(45_000, FREE_SEAT_CEILING),
                         ls.CLASS_PIECES)

    def test_the_two_real_long_sources_price_out_as_recorded(self):
        thesis = ls.read_plan(THESIS_CHARS, FREE_SEAT_CEILING)
        report = ls.read_plan(REPORT_CHARS, FREE_SEAT_CEILING)
        self.assertEqual(thesis["pieces"], 28)
        self.assertEqual(report["pieces"], 35)
        # one call per piece plus the single claim-splitting call
        self.assertEqual(thesis["calls"], 29)
        self.assertEqual(report["calls"], 36)
        self.assertEqual(thesis["size_class"], ls.CLASS_PIECES)
        self.assertEqual(report["size_class"], ls.CLASS_PIECES)
        self.assertTrue(report["silence_trustworthy"])

    def test_beyond_the_piece_budget_no_reading_is_offered(self):
        huge = 40_000 * 60
        plan = ls.read_plan(huge, FREE_SEAT_CEILING)
        self.assertEqual(plan["size_class"], ls.CLASS_OVER_BUDGET)
        self.assertFalse(plan["reads_whole_source"])
        self.assertFalse(plan["silence_trustworthy"])

    def test_no_ceiling_means_one_call_whatever_the_size(self):
        plan = ls.read_plan(THESIS_CHARS, None)
        self.assertEqual(plan["size_class"], ls.CLASS_ONE_CALL)
        self.assertEqual(plan["calls"], 1)


class TestReadingOrder(unittest.TestCase):

    def test_the_piece_holding_the_claims_rare_words_is_read_first(self):
        filler = "The committee met and discussed the agenda at length. " * 200
        needle = ("Weathered biotite sorbed caesium two orders of magnitude "
                  "more strongly than fresh biotite. ")
        text = filler + filler + needle + filler
        pieces, plan = ls.plan_pieces(
            text, claim="Weathered biotite sorbs caesium two orders of "
                        "magnitude better than fresh biotite.",
            piece_chars=5_000, overlap_chars=500, relevance_order=True)
        self.assertGreater(plan["pieces"], 1)
        self.assertIn(needle.strip(), pieces[0][1])

    def test_piece_numbers_describe_the_source_not_the_reading_order(self):
        text = "a" * 4_000 + "the needle sentence is here. " + "b" * 6_000
        pieces, _ = ls.plan_pieces(text, claim="the needle sentence",
                                   piece_chars=5_000, overlap_chars=500,
                                   relevance_order=True)
        numbers = sorted(n for n, _ in pieces)
        self.assertEqual(numbers, list(range(1, len(pieces) + 1)))

    def test_document_order_by_default(self):
        text = "z" * 12_000
        pieces, _ = ls.plan_pieces(text, claim="anything", piece_chars=5_000,
                                   overlap_chars=500)
        self.assertEqual([n for n, _ in pieces], [1, 2, 3])


class TestBestSection(unittest.TestCase):

    def _chunks(self, n, size=1_000):
        return [(f"chunk {i} " + ("w" * size), [i]) for i in range(n)]

    def test_a_short_source_is_returned_whole(self):
        out = ls.best_section("claim", ["short text"], 10_000)
        self.assertEqual(out, "short text")

    def test_the_section_never_exceeds_the_budget(self):
        chunks = self._chunks(50)
        out = ls.best_section("chunk 7", [c[0] for c in chunks], 5_000, chunks)
        self.assertLessEqual(len(out), 5_000)
        self.assertIn("chunk 7", out)

    def test_with_no_chunks_the_text_is_cut_to_the_budget(self):
        out = ls.best_section("claim", ["q" * 90_000], 5_000)
        self.assertEqual(len(out), 5_000)


class TestArbiterFitsTheSeat(unittest.TestCase):
    """The arbiter used to size its source slice in words, so on the free seat
    every long source produced a prompt the client skipped without sending."""

    def _sents(self, n_chars):
        one = "This sentence is filler for the arbiter section test. "
        return [{"text": one} for _ in range(max(1, n_chars // len(one)))]

    def test_a_long_source_now_fits_the_free_seat(self):
        from modules.papertrail import arbiter
        sents = self._sents(600_000)
        budget = arbiter.section_budget(FreeSeatClient(), 1)
        self.assertEqual(budget, 40_000)
        section = arbiter._relevant_section("caesium sorption", sents, budget)
        self.assertLessEqual(len(section), budget)
        # what it used to send instead, sized in words with no ceiling at all
        old = arbiter._relevant_section("caesium sorption", sents)
        self.assertGreater(len(old), FREE_SEAT_CEILING)

    def test_the_budget_is_split_between_several_cited_sources(self):
        from modules.papertrail import arbiter
        self.assertEqual(arbiter.section_budget(FreeSeatClient(), 2), 20_000)
        self.assertEqual(arbiter.section_budget(FreeSeatClient(), 4), 10_000)

    def test_a_seat_without_a_ceiling_keeps_the_old_behaviour(self):
        from modules.papertrail import arbiter
        self.assertIsNone(arbiter.section_budget(NoCeilingClient(), 1))
        self.assertIsNone(arbiter.section_budget(ClientWithoutTheMethod(), 1))
        sents = self._sents(600_000)
        self.assertEqual(arbiter._relevant_section("x", sents, None),
                         arbiter._relevant_section("x", sents))


if __name__ == "__main__":
    unittest.main()
