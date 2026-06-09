import asyncio

from relprim import EventEmitter, InMemoryEventSink, ValidationFailedError, resilient


class DemoProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, prompt: str) -> str:
        self.calls += 1

        if self.calls == 1:
            raise ValidationFailedError(
                "Validation failed in 'non_empty_response': Response must not be empty.",
                validator_name="non_empty_response",
                reason="Response must not be empty.",
            )

        return f"Validated response for: {prompt}"


provider = DemoProvider()
event_sink = InMemoryEventSink()
event_emitter = EventEmitter(sinks=(event_sink,))


@resilient(
    name="generate_response",
    retries=2,
    retry_on=(ValidationFailedError,),
    timeout=10,
    events=event_emitter,
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
