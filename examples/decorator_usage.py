import asyncio

from relprim import (
    EventEmitter,
    InMemoryEventSink,
    RetryPolicy,
    ValidationFailedError,
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


provider = DemoProvider()
event_sink = InMemoryEventSink()
event_emitter = EventEmitter(sinks=(event_sink,))


@resilient(
    name="generate_response",
    events=event_emitter,
    retry=RetryPolicy(
        max_attempts=2,
        retry_on=(ValidationFailedError,),
    ),
    validation=validation_policy(
        validator(
            "non_empty_response",
            lambda value: bool(value.strip()),
            message="Response must not be empty.",
        )
    ),
)
async def generate_response(prompt: str) -> str:
    return await provider.generate(prompt)


async def main() -> None:
    result = await generate_response("Write a short product summary")

    print("Value:")
    print(result.value)

    print("\nExecution report:")
    print(result.report.to_dict())

    print("\nStructured events:")
    for event in await event_sink.events():
        print(event.to_dict())


if __name__ == "__main__":
    asyncio.run(main())
