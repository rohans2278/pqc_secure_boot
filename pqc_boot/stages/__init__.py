"""Pipeline stages.

Each stage module exposes two functions:
  - plan(ctx) -> str : a one-line description of what the stage would do
  - run(ctx)  -> None: perform the stage (raises NotImplementedError if a stub)
"""
