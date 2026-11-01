# tests for group parsing and the photo reader helpers, no network and no tesseract needed
#   python3 -m unittest discover -s tests

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ocr_refs
from refparse import check_ref, parse_groups, split_heading


class Headings(unittest.TestCase):
    def test_colon_heading_with_refs(self):
        self.assertEqual(split_heading("The sense of death: John 4:13; Isa. 55:2"),
                         ("The sense of death", "John 4:13; Isa. 55:2"))

    def test_plain_reference_line(self):
        self.assertEqual(split_heading("John 4:13; Isa. 55:2"), ("", "John 4:13; Isa. 55:2"))

    def test_book_name_is_not_a_heading(self):
        self.assertEqual(split_heading("Romans: 8"), ("", "Romans: 8"))
        self.assertEqual(split_heading("Jude"), ("", "Jude"))

    def test_heading_only_line(self):
        self.assertEqual(split_heading("The sense of life"), ("The sense of life", ""))
        self.assertEqual(split_heading("# Life"), ("Life", ""))


class Groups(unittest.TestCase):
    def test_two_groups(self):
        text = ("The sense of life: John 6:35; Phil. 4:11-12\n\n"
                "The sense of death: John 4:13; Isa. 55:2\nLuke 15:14-16")
        groups, warns = parse_groups(text)
        self.assertEqual([g["heading"] for g in groups], ["The sense of life", "The sense of death"])
        self.assertEqual(groups[0]["refs"], ["John 6:35", "Phil. 4:11-12"])
        self.assertEqual(groups[1]["refs"], ["John 4:13", "Isa. 55:2", "Luke 15:14-16"])
        self.assertEqual(warns, [])

    def test_untitled_list(self):
        groups, _ = parse_groups("John 3:16\nRom. 8:2")
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["heading"], "")
        self.assertEqual(groups[0]["refs"], ["John 3:16", "Rom. 8:2"])

    def test_heading_on_its_own_line_survives_blank_line(self):
        groups, _ = parse_groups("The sense of death\n\nJohn 4:13")
        self.assertEqual(groups[0]["heading"], "The sense of death")

    def test_wrapped_reference_with_trailing_separator(self):
        groups, _ = parse_groups("Life: Rom. 6:4; John 10:10;\nRom. 8:11")
        self.assertEqual(groups[0]["refs"], ["Rom. 6:4", "John 10:10", "Rom. 8:11"])

    def test_bad_chapter_warns(self):
        _, warns = parse_groups("Phil. 4212-12")
        self.assertTrue(any("only has 4 chapters" in w for w in warns))

    def test_heading_without_refs_warns(self):
        _, warns = parse_groups("Lonely heading\n\nJohn 3:16")
        # the heading line is followed by a blank line, so it keeps the next group
        self.assertEqual(warns, [])

    def test_check_ref(self):
        self.assertIsNone(check_ref("Jude 9"))
        self.assertIsNone(check_ref("Psa. 119:105"))
        self.assertIn("chapters", check_ref("Rom. 17:1"))
        self.assertIn("backwards", check_ref("John 3:16-10"))


class PhotoReader(unittest.TestCase):
    def test_fix_word(self):
        self.assertEqual(ocr_refs.fix_word("6\u00b035;"), "6:35;")
        self.assertEqual(ocr_refs.fix_word("2'17;"), "2:17;")
        self.assertEqual(ocr_refs.fix_word("l5:l4"), "15:14")
        self.assertEqual(ocr_refs.fix_word("Isa."), "Isa.")

    def test_book_lookup_and_fuzzy(self):
        self.assertEqual(ocr_refs.resolve_book("Isa"), ("Isa.", False))
        self.assertEqual(ocr_refs.resolve_book("tsa"), ("Isa.", True))
        self.assertEqual(ocr_refs.resolve_book("2 Cor"), ("2 Cor.", False))
        self.assertEqual(ocr_refs.resolve_book("I John"), ("1 John", False))
        self.assertEqual(ocr_refs.resolve_book("zz"), (None, False))

    def test_glued_digits(self):
        self.assertEqual(ocr_refs.split_glued("Isa.", "4030"), (40, 30))
        self.assertIsNone(ocr_refs.split_glued("Phil.", "4212"))

    def words(self, text, conf=95):
        return [(w, conf) for w in text.split()]

    def test_build_groups_and_wrapped_reference(self):
        lines = [
            self.words("The sense of death: John 4:13; Isa. 55:2; Rom. 7:18-19; John"),
            self.words("15:5; Heb. 12:3;"),
            self.words("3. The Source of the Sense of Life"),
            self.words("John 5:26 - For just as the Father has life in Himself"),
        ]
        groups, unsure = ocr_refs.build(lines)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["heading"], "The sense of death")
        self.assertEqual(groups[0]["refs"],
                         ["John 4:13", "Isa. 55:2", "Rom. 7:18-19", "John 15:5", "Heb. 12:3"])
        self.assertEqual(unsure, [])

    def test_margin_noise_and_low_confidence(self):
        lines = [[("Bree", 5), ("The", 96), ("sense", 96), ("of", 96), ("death:", 96),
                  ("John", 92), ("4:13;", 40), ("Isa.", 90), ("55", 90)]]
        groups, unsure = ocr_refs.build(lines)
        self.assertEqual(groups[0]["heading"], "The sense of death")
        reasons = dict(unsure)
        self.assertIn("low confidence", reasons["John 4:13"])
        self.assertIn("whole chapter", reasons["Isa. 55"])

    def test_not_an_image(self):
        with self.assertRaises(ocr_refs.OcrError):
            ocr_refs.read_image(b"not an image")


if __name__ == "__main__":
    unittest.main()
