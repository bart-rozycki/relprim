import asyncio

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

from relprim import EventEmitter, RetryPolicy, resilient
from relprim.opentelemetry import OpenTelemetryEventSink


class TransientProviderError(Exception):
    pass


class DemoProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, prompt: str) -> str:
        self.calls += 1

        if self.calls == 1:
            raise TransientProviderError("temporary provider failure")

        return f"Response for: {prompt}"


async def no_sleep(delay: float) -> None:
    return None


tracer_provider = TracerProvider()
tracer_provider.add_span_processor(
    SimpleSpanProcessor(
        ConsoleSpanExporter(),
    )
)

tracer = tracer_provider.get_tracer("relprim.example")

event_sink = OpenTelemetryEventSink()
event_emitter = EventEmitter(sinks=(event_sink,))

provider = DemoProvider()


@resilient(
    name="generate_response",
    retry=RetryPolicy(
        max_attempts=2,
        retry_on=(TransientProviderError,),
        async_sleeper=no_sleep,
    ),
    events=event_emitter,
)
async def generate_response(prompt: str) -> str:
    return await provider.generate(prompt)


async def main() -> None:
    with tracer.start_as_current_span("example.request"):
        result = await generate_response("Write a short product summary")

    print("Value:")
    print(result.value)

    print("\nProvider calls:")
    print(provider.calls)

    tracer_provider.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
