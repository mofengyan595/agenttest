"""Optional Deep Agents harness integration (P1 evaluation, not yet production default).

Importing this package must never require ``langchain``/``deepagents``: the base install
does not have them and the product chain must keep working without them. Import the
concrete modules directly (``agenttest.deep_harness.chat_model``) and only behind the
optional-dependency gate.
"""

__all__: list[str] = []
