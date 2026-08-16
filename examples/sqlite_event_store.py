import asyncio
from pathlib import Path

from relprim import (
    EventEmitter,
    RetryPolicy,
    SQLiteEventStore,
    resilient,
)


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


async def main() -> None:
    database = Path("relprim-example-events.db")

    event_store = SQLiteEventStore(database)

    await event_store.initialize()

    event_emitter = EventEmitter(sinks=(event_store,))

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
    async def generate_response(
        prompt: str,
    ) -> str:
        return await provider.generate(prompt)

    result = await generate_response("Write a short product summary")

    print("Value:")
    print(result.value)

    print("\nPersisted event history:")

    history = await event_store.history(
        operation_name="generate_response",
        limit=100,
    )

    for stored in history:
        print(
            stored.sequence_id,
            stored.event.event_type.value,
            stored.event.to_dict(),
        )

    print(f"\nDatabase: {database.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
