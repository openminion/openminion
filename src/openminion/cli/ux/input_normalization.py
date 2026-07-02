def normalize_multiline_input_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n") if text else ""
