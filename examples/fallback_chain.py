import asyncio

from relprim import RetryPolicy, TimeoutPolicy, async_operation, fallback_chain


class PrimaryProviderError(Exception):
    pass


async def call_primary_provider(prompt: str) -> str:
    raise PrimaryProviderError("primary provider unavailable")


async def call_fallback_provider(prompt: str) -> str:
    return f"Fallback response for: {prompt}"


async def main() -> None:
    result = await (
        async_operation("generate_response", call_primary_provider)
        .with_retry(
            RetryPolicy(
                max_attempts=2,
                retry_on=(PrimaryProviderError,),
            )
        )
        .with_timeout(TimeoutPolicy(seconds=5))
        .with_fallbacks(
            fallback_chain(
                ("fallback_provider", call_fallback_provider),
            )
        )
        .run("Write a short product summary")
    )

    print("Value:")
    print(result.value)

    print("\nFallback metadata:")
    print(result.report.metadata)

    print("\nExecution report:")
    print(result.report.to_dict())


if __name__ == "__main__":
    asyncio.run(main())
