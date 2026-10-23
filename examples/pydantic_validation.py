import asyncio

from pydantic import BaseModel

from relprim import async_operation, fallback_chain, validation_policy
from relprim.pydantic import pydantic_validator


class ProviderResponse(BaseModel):
    id: int
    name: str


async def call_primary_provider(user_id: int) -> dict[str, object]:
    return {"id": "not-an-integer", "name": "Ada Lovelace"}


async def call_backup_provider(user_id: int) -> dict[str, object]:
    return {"id": user_id, "name": "Ada Lovelace"}


async def main() -> None:
    result = await (
        async_operation("fetch_user", call_primary_provider)
        .with_validation(validation_policy(pydantic_validator(ProviderResponse)))
        .with_fallbacks(fallback_chain(("backup_provider", call_backup_provider)))
        .run(42)
    )

    print("Value:")
    print(result.value)

    print("\nPrimary attempt error:")
    print(result.report.attempts[0].error)

    print("\nExecution report:")
    print(result.report.to_dict())


if __name__ == "__main__":
    asyncio.run(main())
