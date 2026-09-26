from __future__ import annotations

from datetime import datetime, timezone
from collections import Counter

from src.providers import TwelveDataProvider


START = datetime(2026, 8, 3, tzinfo=timezone.utc)
END = datetime(2026, 8, 4, tzinfo=timezone.utc)


def main() -> None:
    print("=== Always-on Trader — DQ-03 ===")
    print("Test: Duplicate timestamps")
    print("Provider: Twelve Data")
    print("Symbol: AAPL")
    print()

    provider = TwelveDataProvider()

    bars = provider.get_1h(
        "AAPL",
        START,
        END,
    )

    timestamps = [bar.timestamp for bar in bars]
    counts = Counter(timestamps)

    duplicates = {
        timestamp: count
        for timestamp, count in counts.items()
        if count > 1
    }

    print(f"Downloaded bars: {len(bars)}")
    print(f"Unique timestamps: {len(counts)}")
    print(f"Duplicate timestamps: {len(duplicates)}")

    if duplicates:
        print("\nDuplicates:")
        for timestamp, count in sorted(duplicates.items()):
            print(f"  {timestamp.isoformat()} -> {count} occurrences")

    status = "PASS" if not duplicates else "FAIL"
    print(f"\nSTATUS: {status}")


if __name__ == "__main__":
    main()