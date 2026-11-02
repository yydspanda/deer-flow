"""Exact two-file patch; all surrounding source is preserved."""

HELPER = r'''def _json_document(raw):
    """Unwrap one complete JSON code fence; never extract JSON from mixed prose."""
    lines = raw.strip(" \t\r\n").split("\n")
    if (
        len(lines) >= 3
        and lines[0].rstrip(" \t\r").lower() in {"```json", "```"}
        and lines[-1].strip(" \t\r") == "```"
    ):
        return "\n".join(lines[1:-1])
    return raw


'''

TEST_METHODS = r"""    def test_complete_single_json_code_fences_are_accepted(self):
        raw = json.dumps(draft(), ensure_ascii=False)
        data = materials()
        for label in ("json", "JSON", ""):
            for separator in ("\n", "\r\n"):
                wrapped = " \t\r\n```" + label + separator + raw + separator + "``` \t\r\n"
                with self.subTest(label=label, separator=separator):
                    generate = Mock(return_value=wrapped)
                    result = analysis.analyze_materials(data, generate)
                    self.assertEqual(result["status"], "proposal")
                    self.assertEqual(result["response_sha256"], hashlib.sha256(wrapped.encode("utf-8")).hexdigest())
                    self.assertEqual(result["prompt_sha256"], analysis.build_analysis_request(data)["prompt_sha256"])
                    self.assertEqual(result["model_calls"], 0)
                    generate.assert_called_once()

    def test_fences_never_extract_json_from_mixed_or_incomplete_text(self):
        raw = json.dumps(draft())
        fenced = "```json\n" + raw + "\n```"
        cases = (
            "说明\n" + fenced,
            fenced + "\n说明",
            fenced + "\n" + fenced,
            "```json\n" + raw,
            "```json " + raw + "```",
            "```python\n" + raw + "\n```",
            "````json\n" + raw + "\n````",
            "\ufeff" + fenced,
            "\u00a0" + fenced,
            "```json\n" + raw + "\n```\n说明\n```",
        )
        for reply in cases:
            with self.subTest(reply=reply[:30]):
                generate = Mock(return_value=reply)
                self.assert_failed(analysis.analyze_materials(materials(), generate), generate)

    def test_wrapper_does_not_change_json_string_values(self):
        answer = draft()
        answer["issue"] = "人工说明：保留```literal```、中文\u2028和\u2029分隔符"
        raw = json.dumps(answer, ensure_ascii=False)
        wrapped = "```json\n" + raw + "\n```"
        self.assertEqual(analysis._json_document(wrapped), raw)
        result = analysis.analyze_materials(materials(), Mock(return_value=wrapped))
        self.assertEqual(result["status"], "proposal")
        self.assertEqual(result["issue"], answer["issue"])

    def test_wrapped_output_still_enforces_json_contract_and_grounding(self):
        raw = json.dumps(draft())
        unknown = draft()
        unknown["sample_alert_ids"] = ["not-shown"]
        unsupported = draft()
        unsupported["filter"]["A"]["value"] = "not-in-log"
        malformed = draft()
        malformed.pop("certainty")
        replies = (
            raw.replace('"status": "proposal"', '"status": "no_change", "status": "proposal"'),
            raw.replace('"name": "host"', '"name": "wrong", "name": "host"'),
            raw.replace('"certainty":', '"extra": NaN, "certainty":'),
            json.dumps(unknown),
            json.dumps(unsupported),
            json.dumps(malformed),
            "[]", "null", "true", "{} {}",
        )
        for reply in replies:
            with self.subTest(reply=reply[:40]):
                generate = Mock(return_value="```json\n" + reply + "\n```")
                self.assert_failed(analysis.analyze_materials(materials(), generate), generate)

    def test_original_size_limit_counts_the_wrapper_before_unwrapping(self):
        answer = {**draft(), "status": "no_change", "sample_alert_ids": [], "filter": None}
        raw = json.dumps(answer, ensure_ascii=False)
        raw += " " * (16380 - len(raw.encode("utf-8")))
        self.assertEqual(len(raw.encode("utf-8")), 16380)
        plain = analysis.analyze_materials(materials(), Mock(return_value=raw))
        self.assertEqual(plain["status"], "no_change")
        generate = Mock(return_value="```json\n" + raw + "\n```")
        self.assert_failed(analysis.analyze_materials(materials(), generate), generate)

"""

PATCHES = [
    {
        "path": "my_workflows/zeus/rule_refine/analysis.py",
        "old": "1946b9c0828a38679655d67c1d28677441211b2edf576c15bda8396decee9117",
        "new": "c8e73780c98a0ae6e02611220965050be07e0dcb3b30b133d4419a03176f00e0",
        "changes": [
            (
                "def _supported(condition, logs):",
                HELPER + "def _supported(condition, logs):",
            ),
            (
                "json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)",
                "json.loads(_json_document(raw), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)",
            ),
        ],
    },
    {
        "path": "test/rule_refine/test_analysis.py",
        "old": "a956151f1d3b2615cf745f1c879566af469fb18d8e8d1418c024fd2cf01d5f5f",
        "new": "fe9e13b15690b8947968e7f203f0f1af4c66828c79cd516514514c5a1ac65992",
        "changes": [
            ("import importlib.util\n", "import importlib.util\nimport hashlib\n"),
            ('"[]", "```json\\n" + normal + "\\n```", duplicate', '"[]", duplicate'),
            (
                "    def test_supported_operators_preserve_the_existing_contract",
                TEST_METHODS
                + "    def test_supported_operators_preserve_the_existing_contract",
            ),
        ],
    },
]
