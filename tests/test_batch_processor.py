"""Tests for batch processor worker configuration."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.batch_processor import BatchProcessor


class _DummyLicense:
    can_export = True

    def record_export(self) -> None:
        pass


def test_batch_processor_normalizes_workers_on_init():
    processor = BatchProcessor(max_workers=0, license_manager=_DummyLicense())
    assert processor.max_workers == 1


def test_batch_processor_set_max_workers_updates_value():
    processor = BatchProcessor(max_workers=2, license_manager=_DummyLicense())
    processor.set_max_workers(6)
    assert processor.max_workers == 6


def test_batch_processor_available_workers_is_at_least_one():
    assert BatchProcessor.available_workers() >= 1
