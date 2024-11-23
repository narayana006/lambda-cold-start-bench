# lambda-cold-start-bench

Measure AWS Lambda cold start times across runtimes, memory sizes and package sizes, and chart the results.

> **Project status: planning. There is no working code in this repository yet.**
> This README describes what the project will do. Nothing below can be installed or run today.
> Features are ticked off in the checklist as they are built.

## What it is

A command-line tool.

**The problem it solves:** Cold start advice is mostly folklore; the numbers depend on your runtime, memory and package size.

## Who it is for

Developers choosing a Lambda runtime or tuning start-up time.

## How it will work

1. You choose the configurations to compare.
2. The tool deploys test functions, invokes them cold many times and records the timings.
3. It writes a table and chart, then deletes the test functions.

## Planned features

Unticked items are not built yet.

- [ ] Deploys small test functions for each configuration
- [ ] Forces cold starts and records init and invoke durations
- [ ] Compares runtimes, memory sizes and architectures
- [ ] CSV output and a chart
- [ ] Removes everything it created when finished

## Planned usage

This is the intended interface, shown so you can judge the design. **It does not work yet**, and names may change before the first release.

```bash
lambda-cold-start-bench run --runtimes python3.13,nodejs22.x --memory 256,1024
```

## Contributing

Feedback on the design is the most useful contribution right now. Open an issue to suggest a feature, question an approach or describe your use case. Code contributions are welcome once the first version lands.

## Author

Built and maintained by [Narayana Vemburaj](https://github.com/narayana006).

## License

[MIT](LICENSE)
