import asyncio

from relprim import RetryPolicy, ValidationFailedError, resilient, validation_policy
from relprim.jsonschema import json_schema_validator

PROVIDER_RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["id", "name"],
    "properties": {
        "id": {"type": "integer"},
        "name": {"type": "string"},
    },
}


class DemoProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def fetch(self, user_id: int) -> dict[str, object]:
        self.calls += 1

        if self.calls == 1:
            return {"id": user_id}

        return {"id": user_id, "name": "Ada Lovelace"}


async def no_sleep(delay: float) -> None:
    return None


provider = DemoProvider()


@resilient(
    name="fetch_user",
    retry=RetryPolicy(
        max_attempts=2,
        retry_on=(ValidationFailedError,),
        async_sleeper=no_sleep,
    ),
    validation=validation_policy(
        json_schema_validator(
            PROVIDER_RESPONSE_SCHEMA,
            name="provider_response",
        )
    ),
)
async def fetch_user(user_id: int) -> dict[str, object]:
    return await provider.fetch(user_id)


async def main() -> None:
    result = await fetch_user(42)

    print("Value:")
    print(result.value)

    print("\nProvider calls:")
    print(provider.calls)

    print("\nFirst attempt error:")
    print(result.report.attempts[0].error)

    print("\nExecution report:")
    print(result.report.to_dict())


if __name__ == "__main__":
    asyncio.run(main())
