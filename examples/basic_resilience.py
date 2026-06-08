import asyncio

from relprim import RetryPolicy, TimeoutPolicy, async_operation


class TemporaryProviderError(Exception):
    pass


class DemoProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, prompt: str) -> str:
        self.calls += 1

        if self.calls < 3:
            raise TemporaryProviderError("temporary provider failure")

        return f"Generated response for: {prompt}"


async def main() -> None:
    provider = DemoProvider()

    result = await (
        async_operation("generate_response", provider.generate)
        .with_retry(
            RetryPolicy(
                max_attempts=3,
                retry_on=(TemporaryProviderError,),
            )
        )
        .with_timeout(TimeoutPolicy(seconds=5))
        .run("Write a short product summary")
    )

    print("Value:")
    print(result.value)

    print("\nExecution report:")
    print(result.report.to_dict())


if __name__ == "__main__":
    asyncio.run(main())
