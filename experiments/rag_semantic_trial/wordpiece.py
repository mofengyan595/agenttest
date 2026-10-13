"""Dependency-free tokenizer for the pinned BGE BertNormalizer/WordPiece configuration."""

import re
import unicodedata

SPECIAL = {"[PAD]": 0, "[UNK]": 100, "[CLS]": 101, "[SEP]": 102, "[MASK]": 103}
CHINESE = ((0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0x20000, 0x2A6DF),
           (0x2A700, 0x2B73F), (0x2B740, 0x2B81F), (0x2B820, 0x2CEAF),
           (0xF900, 0xFAFF), (0x2F800, 0x2FA1F))


class FrozenWordPiece:
    """Single-sequence, case-sensitive BERT subset; reject other tokenizer pipelines."""

    def __init__(self, definition: dict):
        if (definition["normalizer"] != {"type": "BertNormalizer", "clean_text": True,
                "handle_chinese_chars": True, "strip_accents": None, "lowercase": False}
                or definition["pre_tokenizer"] != {"type": "BertPreTokenizer"}):
            raise ValueError("Unsupported tokenizer normalization")
        model = definition["model"]
        if {k: v for k, v in model.items() if k != "vocab"} != {
                "type": "WordPiece", "unk_token": "[UNK]",
                "continuing_subword_prefix": "##", "max_input_chars_per_word": 100}:
            raise ValueError("Unsupported tokenizer model")
        expected = [{"SpecialToken": {"id": "[CLS]", "type_id": 0}},
                    {"Sequence": {"id": "A", "type_id": 0}},
                    {"SpecialToken": {"id": "[SEP]", "type_id": 0}}]
        if (definition["post_processor"].get("type") != "TemplateProcessing"
                or definition["post_processor"].get("single") != expected):
            raise ValueError("Unsupported token post-processing")
        self.vocab = dict(model["vocab"])
        if any(self.vocab.get(token) != value for token, value in SPECIAL.items()):
            raise ValueError("Unexpected special-token IDs")
        if {t["content"]: t["id"] for t in definition["added_tokens"]} != SPECIAL:
            raise ValueError("Unexpected added tokens")
        if any(t.get("normalized") or not t.get("special") or t.get("single_word")
               or t.get("lstrip") or t.get("rstrip") for t in definition["added_tokens"]):
            raise ValueError("Unsupported added-token behavior")
        self.special_pattern = re.compile("(" + "|".join(map(re.escape, SPECIAL)) + ")")

    def verify_vocabulary(self, text: str) -> None:
        # U+2028 is itself a vocabulary token; str.splitlines() would change every later ID.
        words = text.removesuffix("\n").split("\n")
        if self.vocab != {token: index for index, token in enumerate(words)}:
            raise ValueError("Tokenizer JSON and vocabulary disagree")

    @staticmethod
    def basic(text: str) -> list[str]:
        """BERT text cleaning, Chinese boundaries, whitespace and punctuation splitting."""
        cleaned = []
        for char in text:
            category = unicodedata.category(char)
            if char in " \t\r\n" or category == "Zs":
                cleaned.append(" ")
            elif char in "\x00\ufffd" or category in {"Cc", "Cf"}:
                continue
            elif any(start <= ord(char) <= end for start, end in CHINESE):
                cleaned.extend((" ", char, " "))
            else:
                cleaned.append(char)
        result = []
        for word in "".join(cleaned).split():
            pending = []
            for char in word:
                code = ord(char)
                punctuation = (33 <= code <= 47 or 58 <= code <= 64 or 91 <= code <= 96
                               or 123 <= code <= 126
                               or unicodedata.category(char).startswith("P"))
                if punctuation:
                    if pending:
                        result.append("".join(pending))
                        pending = []
                    result.append(char)
                else:
                    pending.append(char)
            if pending:
                result.append("".join(pending))
        return result

    def word(self, word: str) -> list[int]:
        if len(word) > 100:
            return [SPECIAL["[UNK]"]]
        result, start = [], 0
        while start < len(word):
            end = len(word)
            while end > start:
                candidate = ("##" if start else "") + word[start:end]
                if candidate in self.vocab:
                    result.append(self.vocab[candidate])
                    start = end
                    break
                end -= 1
            else:
                return [SPECIAL["[UNK]"]]
        return result

    def encode(self, text: str, maximum: int = 512) -> tuple[list[int], dict]:
        if not isinstance(text, str) or not 2 <= maximum <= 512:
            raise ValueError("Unsupported tokenizer input")
        ids = []
        for piece in self.special_pattern.split(text):
            if piece in SPECIAL:
                ids.append(SPECIAL[piece])
            else:
                for word in self.basic(piece):
                    ids.extend(self.word(word))
        full_length = len(ids) + 2
        encoded = [SPECIAL["[CLS]"], *ids[:maximum - 2], SPECIAL["[SEP]"]]
        return encoded, {"full_tokens": full_length, "encoded_tokens": len(encoded),
                         "truncated": full_length > maximum,
                         "unknown_tokens": encoded.count(SPECIAL["[UNK]"])}
