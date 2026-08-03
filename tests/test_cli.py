import unittest

from nexo_crawler.cli import _with_legacy_default_source, build_parser


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


if __name__ == "__main__":
    unittest.main()
