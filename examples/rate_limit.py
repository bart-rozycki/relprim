import asyncio

from relprim import resilient


class ProviderRateLimitError(Exception):
    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class DemoProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, prompt: str) -> str:
        self.calls += 1

        if self.calls == 1:
            raise ProviderRateLimitError(
                "provider rate limit exceeded",
                retry_after_seconds=0.1,
            )

        return f"Response for: {prompt}"


def provider_retry_after(
    exception: Exception,
) -> float | None:
    if isinstance(exception, ProviderRateLimitError):
        return exception.retry_after_seconds

    return None


provider = DemoProvider()


@resilient(
    retries=2,
    timeout=10,
    rate_limit_on=(ProviderRateLimitError,),
    retry_after=provider_retry_after,
    max_rate_limit_wait=5,
)
async def generate_response(prompt: str) -> str:
    return await provider.generate(prompt)


async def main() -> None:
    result = await generate_response("Write a short product summary")

    print("Value:")
    print(result.value)

    print("\nExecution report:")
    print(result.report.to_dict())

    print("\nProvider calls:")
    print(provider.calls)


if __name__ == "__main__":
    asyncio.run(main())
