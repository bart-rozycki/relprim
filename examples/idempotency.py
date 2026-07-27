import asyncio

from relprim import InMemoryIdempotencyStore, resilient


class DemoPaymentGateway:
    def __init__(self) -> None:
        self.calls = 0

    async def create_payment(
        self,
        request_id: str,
        amount_cents: int,
    ) -> str:
        self.calls += 1

        return f"payment:{request_id}:{amount_cents}"


gateway = DemoPaymentGateway()
idempotency_store = InMemoryIdempotencyStore()


def payment_idempotency_key(
    request_id: str,
    amount_cents: int,
) -> str:
    # request_id must uniquely identify this logical payment request.
    return f"create-payment:{request_id}"


@resilient(
    retries=2,
    timeout=10,
    idempotency_key=payment_idempotency_key,
    idempotency_store=idempotency_store,
    idempotency_ttl=300,
)
async def create_payment(
    request_id: str,
    amount_cents: int,
) -> str:
    return await gateway.create_payment(request_id, amount_cents)


async def main() -> None:
    first = await create_payment("request-123", 2499)
    second = await create_payment("request-123", 2499)

    print("First result:")
    print(first.value)
    print(first.report.metadata)

    print("\nSecond result:")
    print(second.value)
    print(second.report.metadata)

    print("\nGateway calls:")
    print(gateway.calls)


if __name__ == "__main__":
    asyncio.run(main())
