"""Top-level package for tooliscode."""

from importlib import metadata

try:  # pragma: no cover - optional dependency
    from openai import OpenAI  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    class OpenAI:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs) -> None:
            raise ModuleNotFoundError(
                "openai package not installed; run `pip install tooliscode[dev]` "
                "or provide a stub before using tooliscode.Client."
            )

__all__ = ["__version__"]

try:
    __version__ = metadata.version("tooliscode")
except metadata.PackageNotFoundError:  # pragma: no cover - package not installed
    __version__ = "0.0.0"


class Client:
    def __init__(self, client: OpenAI):
        self.client = client

    def create(self, model: str, instructions: str, input: str, tools: list[dict]) -> str:
        return self.client.responses.create(
            model=model,
            instructions=instructions,
            input=input,
            tools=tools,
        )
