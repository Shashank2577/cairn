# CLI contract

```
cairn review [<branch> | <pr-number>] [--base REF] [--staged] [--model | --no-model]
             [--ci] [--format text|json] [--fail-on high|medium|low|never]
             [--post] [--budget N]
cairn review list [--branch B] [--limit N]
cairn review show <review-id>
cairn review accept <finding-id>
cairn review dismiss <finding-id> --reason TEXT
cairn review rule <finding-id>
cairn review rules [--export | --import FILE]
cairn review suppressions
cairn review --forget <review-id>
```

Exit status: 0 normally; with `--ci`, 1 when any finding is at or above `--fail-on` (default `high`, or `review.fail_on`); 2 for errors. Text output lists findings most serious first, each with severity, location, one-sentence explanation, provenance tag, citations, and a final line stating whether a model was used and its token cost.
