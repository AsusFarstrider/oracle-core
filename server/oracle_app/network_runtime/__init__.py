__all__ = ["CanonicalNetworkExecution"]


def __getattr__(name: str):
    if name == "CanonicalNetworkExecution":
        from .canonical import CanonicalNetworkExecution

        return CanonicalNetworkExecution
    raise AttributeError(name)
