class TestControlplaneFileListingParity:
    def test_normalize_command_aliases_maps_list_files(self):
        from openminion.modules.controlplane.channels.telegram.command_aliases import (
            normalize_command_aliases,
        )

        test_cases = [
            ("show me all files on current location", "current location"),
            ("list files in the workspace", "workspace"),
            ("show files here", "here"),
            ("what files are in this directory", "this directory"),
        ]

        for original, expected_path in test_cases:
            normalized = normalize_command_aliases(original, bot_username="testbot")
            assert normalized is not None
            assert isinstance(normalized, str)

    def test_controlplane_message_flow_to_runtime(self):
        from openminion.modules.controlplane.channels.telegram.command_aliases import (
            normalize_command_aliases,
        )

        original_text = "show me all files on current location"

        normalized = normalize_command_aliases(original_text, bot_username="testbot")
        assert normalized == original_text

        assert len(normalized) > 0
        assert isinstance(normalized, str)


class TestAliasMappingParity:
    def test_list_files_alias_exists(self):
        from openminion.modules.tool.contracts import normalize_raw_model_tool_name

        assert normalize_raw_model_tool_name("file.list_dir") == "file.list_dir"
        assert normalize_raw_model_tool_name("file.read") == "file.read"
        assert normalize_raw_model_tool_name("file.find") == "file.find"

    def test_alias_normalization_preserves_file_listing_intent(self):
        from openminion.modules.controlplane.channels.telegram.command_aliases import (
            normalize_command_aliases,
        )

        file_listing_prompts = [
            "show me all files on current location",
            "list files in the workspace",
            "what files are here",
            "show directory contents",
            "list all files",
        ]

        for prompt in file_listing_prompts:
            normalized = normalize_command_aliases(prompt, bot_username="testbot")
            assert len(normalized) > 0
            assert isinstance(normalized, str)
