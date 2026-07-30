from __future__ import annotations

import unittest

from oracle_app.configuration import ConfigurationSyntaxError, RestrictedYamlParser


class RestrictedYamlParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = RestrictedYamlParser()

    def test_parses_restricted_yaml_and_preserves_comments(self) -> None:
        parsed = self.parser.parse(
            "# bundle identity\n"
            "kind: oracle_configuration_bundle\n"
            "schema_version: 1\n"
            "enabled: false\n"
            "optional: null\n"
            "ratio: 1.25e-2\n"
            "aliases: [one, two]\n"
        )

        self.assertEqual(parsed.primitive["schema_version"], 1)
        self.assertEqual(parsed.primitive["enabled"], False)
        self.assertEqual(parsed.primitive["optional"], None)
        self.assertAlmostEqual(parsed.primitive["ratio"], 0.0125)
        self.assertIn("bundle identity", parsed.round_trip.ca.comment[1][0].value)

    def test_requires_one_non_empty_mapping_document(self) -> None:
        for text in ("", "null\n", "- item\n", "a: 1\n---\nb: 2\n"):
            with self.subTest(text=text):
                with self.assertRaises(ConfigurationSyntaxError):
                    self.parser.parse(text)

    def test_rejects_executable_or_compositional_yaml_features(self) -> None:
        cases = {
            "anchor": "a: &value 1\nb: *value\n",
            "tag": "a: !!str 1\n",
            "directive": "%YAML 1.2\n---\na: 1\n",
            "merge": "a:\n  <<: {value: 1}\n",
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(ConfigurationSyntaxError):
                    self.parser.parse(text)

    def test_rejects_duplicate_keys(self) -> None:
        with self.assertRaises(ConfigurationSyntaxError) as caught:
            self.parser.parse("a: 1\na: 2\n")
        self.assertEqual(caught.exception.code, "config.yaml.parse")

    def test_rejects_implicit_timestamps_and_non_json_scalars(self) -> None:
        cases = (
            "value: 2026-07-06\n",
            "value: 01\n",
            "value: 0x10\n",
            "value: .inf\n",
            "value: .nan\n",
            "value: TRUE\n",
            "value: ~\n",
        )
        for text in cases:
            with self.subTest(text=text):
                with self.assertRaises(ConfigurationSyntaxError):
                    self.parser.parse(text)

    def test_allows_date_like_and_ambiguous_words_when_quoted(self) -> None:
        parsed = self.parser.parse('date: "2026-07-06"\nanswer: "TRUE"\nempty: "~"\n')

        self.assertEqual(
            parsed.primitive,
            {"date": "2026-07-06", "answer": "TRUE", "empty": "~"},
        )

    def test_rejects_non_string_mapping_keys_and_utf8_bom(self) -> None:
        for text in ("1: value\n", "\ufeffkey: value\n"):
            with self.subTest(text=text):
                with self.assertRaises(ConfigurationSyntaxError):
                    self.parser.parse(text)


if __name__ == "__main__":
    unittest.main()
