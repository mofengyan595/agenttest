from agenttest.dsl import AssertionPolicy, PollPolicy
from agenttest.policies import PolicyRegistry, RegisteredPolicy


def registry(max_attempts=4):
    return PolicyRegistry([RegisteredPolicy(
        id="items_v1", operation_id="items", roles=[None], business_scenario="default",
        assertions=AssertionPolicy(reviewed=True, source="operator test fixture",
                                   status={"codes": [200]}),
        poll=PollPolicy(select="$.state", terminal_values=["DONE"], progress_values=["RUNNING"],
                        max_attempts=max_attempts, interval=0.0),
    )])
