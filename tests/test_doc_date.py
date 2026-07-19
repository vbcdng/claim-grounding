import unittest

from modules.papertrail.matcher import _doc_date


def _src(*lines):
    return {"sentences": [{"text": t} for t in lines]}


class TestDocDate(unittest.TestCase):
    def test_published_datetime_meta(self):
        s = _src("(meta data) TITLE: x",
                 "(meta data) PUBLISHED DATETIME: 2016-08-24T10:00:00+00:00")
        self.assertEqual(_doc_date(s), "2016-08-24")

    def test_published_datetime_bare(self):
        s = _src("(meta data) PUBLISHED DATETIME: 2015-02-13 08:49")
        self.assertEqual(_doc_date(s), "2015-02-13")

    def test_posted_us_style(self):
        s = _src("headline", "Posted: 9:36 PM, Nov 14, 2018")
        self.assertEqual(_doc_date(s), "2018-11-14")

    def test_day_month_year(self):
        s = _src("#### About CapX", "22 January 2019", "# headline")
        self.assertEqual(_doc_date(s), "2019-01-22")

    def test_short_weekday_two_digit_year(self):
        s = _src("Fri 15 Feb 19")
        self.assertEqual(_doc_date(s), "2019-02-15")

    def test_month_day_year(self):
        s = _src("March 7, 2018")
        self.assertEqual(_doc_date(s), "2018-03-07")

    def test_wayback_capture_range_skipped(self):
        # a crawl-span line must not be read as the publication date
        s = _src("7 captures", "17 Apr 2019 - 17 Jul 2022", "About this capture")
        self.assertEqual(_doc_date(s), "")

    def test_range_skipped_but_later_real_date_used(self):
        s = _src("17 Apr 2019 - 17 Jul 2022", "Apr 17, 2019, 09:00am")
        self.assertEqual(_doc_date(s), "2019-04-17")

    def test_no_date(self):
        s = _src("Clearing 2019", "Subject areas", "Undergraduate study")
        self.assertEqual(_doc_date(s), "")

    def test_scan_window_limited(self):
        lines = [f"filler sentence {i}" for i in range(30)] + ["March 7, 2018"]
        self.assertEqual(_doc_date(_src(*lines)), "")

    def test_implausible_rejected(self):
        self.assertEqual(_doc_date(_src("Feb 45, 2019")), "")
        s = _src("(meta data) PUBLISHED DATETIME: 2016-13-24")
        self.assertEqual(_doc_date(s), "")

    def test_iso_data_span_rejected(self):
        # epochai2025 gate source: a data range is not a publication date
        s = _src("Performance is then aggregated by country for each date "
                 "from 2019-01-01 to the present date.")
        self.assertEqual(_doc_date(s), "")

    def test_narrative_event_date_rejected(self):
        # johnrfox class: the date of the EVENT the text describes must not
        # become the publication date
        s = _src("An organized attack by uniformed German formations was "
                 "launched around 0400 hours, 26 December 1944, near Sommocolonia.")
        self.assertEqual(_doc_date(s), "")

    def test_marker_line_long_still_accepted(self):
        s = _src("Posted: 9:36 PM, Nov 14, 2018 by the Milwaukee city desk "
                 "with additional reporting from the wire services")
        self.assertEqual(_doc_date(s), "2018-11-14")

    def test_marker_mid_sentence(self):
        # merged boilerplate: the datestamp sits mid-sentence after a marker word
        s = _src("Essex volleyball teams win # Double success ### Date Fri 15 "
                 "Feb 19 Essex's volleyball teams did the double")
        self.assertEqual(_doc_date(s), "2019-02-15")

    def test_url_path_date_not_wayback_stamp(self):
        s = _src("The Wayback Machine - https://web.archive.org/web/"
                 "20190417141934/https://www.forbes.com/sites/x/2019/04/17/story")
        self.assertEqual(_doc_date(s), "2019-04-17")

    def test_written_on_marker(self):
        s = _src("# YOUNG ENTREPRENEUR", "Written on the 19 November 2013 "
                 "by the Gold Coast business desk staff reporters team")
        self.assertEqual(_doc_date(s), "2013-11-19")

    def test_candidate_word_not_a_marker(self):
        s = _src("The leading candidate for the March 7, 2018 election won "
                 "against a large field of other hopeful candidates then")
        self.assertEqual(_doc_date(s), "")

    def test_empty(self):
        self.assertEqual(_doc_date({"sentences": []}), "")
        self.assertEqual(_doc_date({}), "")


if __name__ == "__main__":
    unittest.main()
