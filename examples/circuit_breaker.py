import asyncio

from relprim import (
    CircuitBreaker,
    RetryPolicy,
    TimeoutPolicy,
    async_operation,
    fallback_chain,
)


class PrimaryProviderError(Exception):
    pass


class PrimaryProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, prompt: str) -> str:
        self.calls += 1
        raise PrimaryProviderError("primary provider unavailable")


async def call_fallback_provider(prompt: str) -> str:
    return f"Fallback response for: {prompt}"


async def main() -> None:
    primary_provider = PrimaryProvider()

    circuit_breaker = CircuitBreaker(
        name="primary_provider",
        failure_threshold=1,
        recovery_timeout_seconds=30,
        record_failure_on=(PrimaryProviderError,),
    )

    operation = (
        async_operation("generate_response", primary_provider.generate)
        .with_circuit_breaker(circuit_breaker)
        .with_retry(
            RetryPolicy(
                max_attempts=1,
                retry_on=(PrimaryProviderError,),
            )
        )
        .with_timeout(TimeoutPolicy(seconds=5))
        .with_fallbacks(
            fallback_chain(
                ("fallback_provider", call_fallback_provider),
            )
        )
    )

    first_result = await operation.run("Write a short product summary")
    second_result = await operation.run("Write a second product summary")

    print("First result:")
    print(first_result.value)
    print(first_result.report.to_dict())

    print("\nSecond result:")
    print(second_result.value)
    print(second_result.report.to_dict())

    snapshot = await circuit_breaker.snapshot()

    print("\nCircuit breaker snapshot:")
    print(snapshot)


if __name__ == "__main__":
    asyncio.run(main())
