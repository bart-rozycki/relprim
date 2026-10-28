# Stability and versioning

RelPrim follows [Semantic Versioning](https://semver.org/) from 1.0.0 on.

## What is covered

The stable API is what the [API reference](api-reference.md) lists as public:

* names exported from `relprim` and from the optional integration modules;
* documented parameters, defaults, attributes, return types and raised
  exceptions;
* documented runtime semantics, such as retry counting, fallback ordering,
  idempotency statuses and cancellation behavior;
* `EventType` values and the event payload and report metadata keys
  documented in [structured events and metadata](events-and-metadata.md);
* the SQLite event-store schema: databases created by an earlier 1.x release
  remain readable by later 1.x releases.

## What a release may contain

* **Major** (`2.0.0`): any breaking change to the covered API.
* **Minor** (`1.1.0`): new features, new optional parameters with defaults, new
  event types, new payload or metadata keys, new supported Python versions,
  deprecations, and dropping Python versions that reached end of life.
* **Patch** (`1.0.1`): bug fixes and documentation.

A minor or patch release may change behavior that clearly contradicts the
documentation or is plainly unintended. Such fixes are called out in the
release notes.

Deprecated APIs emit `DeprecationWarning` for at least one minor release
before they are removed in the next major release.

## What is not covered

* Names starting with an underscore, and anything not in an `__all__` list.
* The submodule in which a core name is defined. Import core names from
  `relprim`.
* Exception messages, `repr()` output, validation reason wording and
  `ExecutionError.message` contents.
* Timing details such as exact durations, jitter values and scheduling order
  between concurrent operations.
* The `dev` extra and the contents of the test suite.

## Event and metadata consumers

Event identifiers and documented keys are treated deliberately: they are never
renamed or removed outside a major release, and their value types do not
change. New keys and new event types can appear in minor releases, so
consumers should ignore what they do not recognize.

## Typing

RelPrim ships inline type information (PEP 561). Type annotations of the
public API are part of the contract, but improvements that make inferred types
more precise may land in minor releases. Such changes can surface new errors
or unused `# type: ignore` comments under strict type checking without being
considered breaking.

## Supported Python versions

RelPrim supports Python 3.11–3.14. Each supported version is tested with the
base install and all optional extras.
