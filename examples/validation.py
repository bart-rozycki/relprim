import asyncio

from relprim import (
    RetryPolicy,
    ValidationFailedError,
    async_operation,
    validation_policy,
    validator,
)


class DemoProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, prompt: str) -> str:
        self.calls += 1

        if self.calls < 3:
            return " "

        return f"Validated response for: {prompt}"


async def main() -> None:
    provider = DemoProvider()

    result = await (
        async_operation("generate_response", provider.generate)
        .with_retry(
            RetryPolicy(
                max_attempts=3,
                retry_on=(ValidationFailedError,),
            )
        )
        .with_validation(
            validation_policy(
                validator(
                    "non_empty_response",
                    lambda value: bool(value.strip()),
                    message="Response must not be empty.",
                )
            )
        )
        .run("Write a short product summary")
    )

    print("Value:")
    print(result.value)

    print("\nExecution report:")
    print(result.report.to_dict())


if __name__ == "__main__":
    asyncio.run(main())
