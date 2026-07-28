import unittest

from app import build_search_index


class SearchIndexTests(unittest.TestCase):
    def test_search_index_contains_chinese_full_pinyin_and_initials(self) -> None:
        index = build_search_index("北京 UP")

        self.assertIn("北京", index)
        self.assertIn("beijing", index)
        self.assertIn("bj", index)
        self.assertIn("up", index)


if __name__ == "__main__":
    unittest.main()
