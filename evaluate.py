"""Run unit tests and the compression benchmark; print one JSON object to stdout.

Corpus: the 40 alphabetically-first *.py files directly inside the stdlib directory.
Metric: total compressed bytes / total original bytes (lower is better).
tests_total = unit tests + one round-trip check per corpus file.
The metric is null if any test fails or the run exceeds the time limit.
"""

import glob
import io
import json
import os
import sys
import time
import unittest

TIME_LIMIT = 120.0
CORPUS_SIZE = 40


def corpus_files():
    stdlib = os.path.dirname(os.__file__)
    return sorted(glob.glob(os.path.join(stdlib, "*.py")))[:CORPUS_SIZE]


def run_unit_tests():
    here = os.path.dirname(os.path.abspath(__file__))
    suite = unittest.defaultTestLoader.discover(here, pattern="test_*.py")
    result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
    failed = len(result.failures) + len(result.errors)
    return result.testsRun - failed, result.testsRun


def main():
    start = time.time()
    passed, total = run_unit_tests()

    from compressor import compress, decompress

    original = compressed = 0
    for path in corpus_files():
        with open(path, "rb") as f:
            data = f.read()
        total += 1
        blob = b""
        try:
            blob = compress(data)
            ok = decompress(blob) == data
        except Exception as e:
            print(f"error on {os.path.basename(path)}: {e!r}", file=sys.stderr)
            ok = False
        if ok:
            passed += 1
        else:
            print(f"round-trip failed: {os.path.basename(path)}", file=sys.stderr)
        original += len(data)
        compressed += len(blob) if ok else len(data)

    duration = time.time() - start
    valid = passed == total and duration <= TIME_LIMIT
    print(json.dumps({
        "metric": round(compressed / original, 6) if valid else None,
        "tests_passed": passed,
        "tests_total": total,
        "duration_seconds": round(duration, 2),
    }))


if __name__ == "__main__":
    main()
