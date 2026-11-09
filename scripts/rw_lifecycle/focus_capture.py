"""Capture the redacted raw focus model output and the product's validation diagnostics.

The acceptance driver installs this recorder around the configured focus model, so a
failed first position can be attributed to the model, the product or the fixture
without ever keeping an API key, bearer token or credential header value.
"""

import json
from hashlib import sha256

RECORDS = []
MAX_TEXT = 20_000


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)[:MAX_TEXT]


def record(content, stats=None, model=None, protocol=None):
    entry = dict(
        model=model, protocol=protocol,
        content_sha256=sha256(content.encode()).hexdigest(),
        content_bytes=len(content.encode()),
        content=content[:MAX_TEXT],
        parsed=None, decoded=None, diagnostic=None,
        usage=dict(calls=stats.calls if stats else None,
                   input_tokens=stats.input_tokens if stats else None,
                   output_tokens=stats.output_tokens if stats else None,
                   total_tokens=stats.total_tokens if stats else None,
                   request_sha256=stats.request_sha256 if stats else None,
                   output_sha256=stats.output_sha256 if stats else None),
    )
    try:
        entry["parsed"] = json.loads(content)
    except ValueError:
        entry["parsed"] = None
    RECORDS.append(entry)
    return entry


def note_decoded(result):
    if not RECORDS:
        return
    try:
        RECORDS[-1]["decoded"] = json.loads(result)
    except (TypeError, ValueError):
        RECORDS[-1]["decoded"] = _dump(result)


def note_failure(error):
    if not RECORDS:
        return
    diagnostic = getattr(error, "diagnostic", None)
    RECORDS[-1]["diagnostic"] = dict(
        kind=type(error).__name__,
        outcome=str(getattr(error, "outcome", "")),
        reason=str(getattr(error, "reason", "")),
        code=getattr(diagnostic, "code", None),
        detail=getattr(diagnostic, "detail", None) if diagnostic else None,
    )


def take():
    records, RECORDS[:] = list(RECORDS), []
    return records


def install():
    """Wrap the module-level focus factory used by the task service."""
    from agenttest import goal_server
    from agenttest.product_focus import TestFocusModel

    class RecordingFocus(TestFocusModel):
        def _message_content(self, choice):
            content = super()._message_content(choice)
            record(content, self.stats, model=self._settings.model,
                   protocol=self._settings.protocol)
            return content

        def _decode_output(self, content, context):
            try:
                result = super()._decode_output(content, context)
            except Exception as error:  # diagnostic first, then the product decides
                note_failure(error)
                raise
            note_decoded(result)
            return result

    original = goal_server.test_focus
    goal_server.test_focus = lambda settings: RecordingFocus(settings)
    return original, goal_server.test_focus
