import asyncio

from relprim import (
    EventEmitter,
    InMemoryEventSink,
    OperationExecutionError,
    resilient,
    validation_policy,
    validator,
)


class DemoProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, prompt: str) -> str:
        self.calls += 1

        if self.calls == 1:
            return " "

        return f"Validated response for: {prompt}"


def is_non_empty(value: str) -> bool:
    return bool(value.strip())


provider = DemoProvider()
event_sink = InMemoryEventSink()
event_emitter = EventEmitter(sinks=(event_sink,))


@resilient(
    name="generate_response",
    retries=2,
    timeout=10,
    validation=validation_policy(
        validator(
            "non_empty_response",
            is_non_empty,
            message="Response must not be empty.",
        )
    ),
    events=event_emitter,
)
async def generate_response(prompt: str) -> str:
    return await provider.generate(prompt)


async def main() -> None:
    try:
        result = await generate_response("Write a short product summary")
    except OperationExecutionError as error:
        print("Operation failed:", error.cause)
        print(error.report.to_dict())
        return

    print("Value:")
    print(result.value)

    print("\nAttempts:", result.report.attempt_count, "retries:", result.report.retry_count)

    print("\nExecution report:")
    print(result.report.to_dict())

    print("\nStructured events:")
    for event in await event_sink.events():
        print(event.to_dict())


if __name__ == "__main__":
    asyncio.run(main())
