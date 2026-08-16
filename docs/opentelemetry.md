# OpenTelemetry integration

RelPrim can export structured reliability events to an active OpenTelemetry
span.

The integration is provided by `OpenTelemetryEventSink`.

It exports events such as retries, validation outcomes, fallbacks, circuit
breaker rejections and rate-limit decisions without configuring the
application's tracing infrastructure.

## Install

Install RelPrim with the optional OpenTelemetry API dependency:

```bash
pip install "relprim[otel]"
```

The application must also provide an OpenTelemetry SDK and exporter.

For a basic local example:

```bash
pip install opentelemetry-sdk
```

Production applications may use an OTLP exporter or another exporter supported
by their observability platform.

RelPrim does not select or configure an exporter.

## Configure OpenTelemetry

The application owns its `TracerProvider`, span processors and exporters.

A minimal console configuration looks like this:

```python
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)


tracer_provider = TracerProvider()
tracer_provider.add_span_processor(
    SimpleSpanProcessor(
        ConsoleSpanExporter(),
    )
)

tracer = tracer_provider.get_tracer("my.application")
```

RelPrim intentionally does not install this provider globally.

## Add the event sink

Create an `OpenTelemetryEventSink` and pass it to an `EventEmitter`:

```python
from relprim import EventEmitter
from relprim.opentelemetry import OpenTelemetryEventSink


otel_sink = OpenTelemetryEventSink()
event_emitter = EventEmitter(sinks=(otel_sink,))
```

Use the emitter with the decorator API:

```python
from relprim import resilient


@resilient(
    retries=3,
    timeout=10,
    events=event_emitter,
)
async def call_provider(prompt: str) -> str:
    return await provider.generate(prompt)
```

The operation must execute while an OpenTelemetry span is active:

```python
with tracer.start_as_current_span("request"):
    result = await call_provider("Write a short product summary")
```

RelPrim events are added to the active `request` span.

## Exported event names

RelPrim prefixes event names with `relprim.`.

Examples include:

```text
relprim.operation.started
relprim.attempt.started
relprim.attempt.failed
relprim.retry.scheduled
relprim.validation.failed
relprim.fallback.started
relprim.rate_limit.detected
relprim.operation.succeeded
```

The exact events depend on the configured policies and execution path.

## Exported attributes

Every exported event includes:

```text
relprim.event.type
relprim.operation.name
```

Payload fields are exported using the following prefix:

```text
relprim.payload.
```

For example, a retry event may contain:

```text
relprim.event.type = "retry.scheduled"
relprim.operation.name = "generate_response"
relprim.payload.attempt_number = 1
relprim.payload.next_attempt_number = 2
relprim.payload.delay_seconds = 0.5
relprim.payload.rate_limited = false
```

Payload values equal to `None` are omitted because they are not valid
OpenTelemetry attributes.

## Event timestamps

The original timezone-aware `StructuredEvent.timestamp` is used as the
OpenTelemetry span event timestamp.

The sink does not replace it with the time at which the exporter processes the
event.

## Multiple sinks

An event emitter may send the same event to OpenTelemetry and another sink:

```python
from relprim import EventEmitter, InMemoryEventSink
from relprim.opentelemetry import OpenTelemetryEventSink


memory_sink = InMemoryEventSink()
otel_sink = OpenTelemetryEventSink()

event_emitter = EventEmitter(
    sinks=(
        memory_sink,
        otel_sink,
    )
)
```

Sinks are awaited in their configured order.

RelPrim does not create hidden background tasks or buffers for event delivery.

## Builder API

The same sink works with `AsyncOperation`:

```python
from relprim import EventEmitter, RetryPolicy, async_operation
from relprim.opentelemetry import OpenTelemetryEventSink


event_emitter = EventEmitter(sinks=(OpenTelemetryEventSink(),))

with tracer.start_as_current_span("request"):
    result = await (
        async_operation("generate_response", call_provider)
        .with_retry(RetryPolicy(max_attempts=3))
        .with_events(event_emitter)
        .run("Write a short product summary")
    )
```

## No active span

When no recording span is active, `OpenTelemetryEventSink.emit()` is a no-op.

The reliability operation continues normally, but no OpenTelemetry event is
recorded.

This makes the integration safe when tracing is disabled, sampling rejects the
span or the application has not installed an SDK.

## Dependency model

The `relprim[otel]` extra installs `opentelemetry-api`.

It does not install:

- `opentelemetry-sdk`;
- an OTLP exporter;
- an observability vendor integration;
- a collector.

Applications choose those components independently.

Import the optional integration from:

```python
from relprim.opentelemetry import OpenTelemetryEventSink
```

The core package does not import OpenTelemetry, so `import relprim` continues to
work without the optional extra.

## Scope and limitations

RelPrim 0.10.0 exports structured events to an already active span.

It does not:

- create a dedicated span for each RelPrim operation;
- create attempt spans;
- configure a global `TracerProvider`;
- record exception objects;
- export metrics;
- use the OpenTelemetry logs signal;
- ship an OTLP exporter;
- replace an observability backend.

Dedicated operation spans may be considered later only when their lifecycle,
cancellation and correlation semantics can be defined without making the core
API unnecessarily complex.
