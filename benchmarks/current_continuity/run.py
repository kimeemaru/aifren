from benchmarks.current_continuity.harness import report_json, run


if __name__ == "__main__":
    report = run()
    print(report_json(report))
    raise SystemExit(0 if report.failed == 0 else 1)
