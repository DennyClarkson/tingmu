"""Explicit, temporary transport hooks for pre-refactor injected integrations.

No learning agent is constructed. The default hooks delegate to the owned client;
legacy class/instance overrides remain visible until callers adopt public ports.
"""


def legacy_learning_complete(owner, system, payload, schema=None):
    from .agent import LearningAgent
    return LearningAgent._complete(owner, system, payload, schema)


def legacy_learning_request(owner, path, body=None, **options):
    from .agent import LearningAgent
    return LearningAgent._request(owner, path, body, **options)


class LegacyTransportAdapter:
    def __init__(self, client):
        self.client = client

    @property
    def model(self):
        return self.client.model

    @property
    def provider(self):
        return self.client.provider

    @property
    def _profile(self):
        return self.client._profile

    def transport_config(self):
        return self.client.transport_config()

    def _transport_config(self):
        return self.transport_config()

    def request(self, path, body=None, *, on_reasoning=None):
        options = {"on_reasoning": on_reasoning} if on_reasoning is not None else {}
        return self._request(path, body, **options)

    def _request(self, path, body=None, **options):
        return legacy_learning_request(self, path, body, **options)

    def stream_request(self, body, *, on_delta=None, cancelled=None, on_reasoning=None):
        options = {"on_reasoning": on_reasoning} if on_reasoning is not None else {}
        return self._stream_request(body, on_delta=on_delta, cancelled=cancelled, **options)

    def _stream_request(self, body, **options):
        from .agent import LearningAgent
        return LearningAgent._stream_request(self, body, **options)
