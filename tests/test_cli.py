import unittest

from nexo_crawler.cli import _with_legacy_default_source, build_parser, main


class CliTests(unittest.TestCase):
    def test_old_style_command_defaults_to_metmuseum(self):
        self.assertEqual(
            _with_legacy_default_source(["--object-id", "42"]),
            ["metmuseum", "--object-id", "42"],
        )

    def test_explicit_source_is_unchanged(self):
        self.assertEqual(
            _with_legacy_default_source(["metmuseum", "--object-id", "42"]),
            ["metmuseum", "--object-id", "42"],
        )

    def test_top_level_help_is_not_rewritten(self):
        self.assertEqual(_with_legacy_default_source(["--help"]), ["--help"])

    def test_parser_selects_registered_adapter(self):
        args = build_parser().parse_args(["metmuseum", "--object-id", "42"])
        self.assertEqual(args.source, "metmuseum")
        self.assertEqual(args.object_id, ["42"])
        self.assertEqual(args.request_delay, 1.0)

    def test_parser_accepts_strict_requirements(self):
        args = build_parser().parse_args(
            ["metmuseum", "--object-id", "42", "--require-image", "--require-description"]
        )
        self.assertTrue(args.require_image)
        self.assertTrue(args.require_description)

    def test_parser_accepts_year_and_creator_filters(self):
        args = build_parser().parse_args(
            [
                "metmuseum",
                "--object-id",
                "42",
                "--year-from",
                "1800",
                "--year-to",
                "2000",
                "--require-creator",
            ]
        )
        self.assertEqual(args.year_from, 1800)
        self.assertEqual(args.year_to, 2000)
        self.assertTrue(args.require_creator)

    def test_parser_accepts_max_examined_safety_cap(self):
        args = build_parser().parse_args(
            ["metmuseum", "--object-id", "42", "--limit", "100", "--max-examined", "5000"]
        )
        self.assertEqual(args.limit, 100)
        self.assertEqual(args.max_examined, 5000)

    def test_reversed_year_range_is_rejected(self):
        with self.assertRaises(SystemExit):
            main(
                [
                    "metmuseum",
                    "--object-id",
                    "42",
                    "--year-from",
                    "2000",
                    "--year-to",
                    "1800",
                ]
            )

    def test_require_image_cannot_be_combined_with_skip_images(self):
        with self.assertRaises(SystemExit):
            main(
                [
                    "metmuseum",
                    "--object-id",
                    "42",
                    "--require-image",
                    "--skip-images",
                ]
            )


if __name__ == "__main__":
    unittest.main()
