from types import SimpleNamespace
import unittest
from unittest.mock import patch
from grokbot_exporter import capture as capture_module
from grokbot_exporter.capture import Browser, DEFAULT_DELAY


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class PacingTests(unittest.TestCase):
    def test_reads_wait_between_calls(self):
        clock = Clock()
        with patch.object(capture_module.time, 'monotonic', clock.monotonic), \
             patch.object(capture_module.time, 'sleep', clock.sleep), \
             patch.object(capture_module.subprocess, 'run') as run, \
             patch.object(capture_module, 'parse_cli_result', return_value={}):
            run.return_value = SimpleNamespace(returncode=0, stdout='', stderr='')
            browser = Browser('test', delay=DEFAULT_DELAY)
            browser.read('roster')
            self.assertEqual(clock.sleeps, [])
            browser.read('roster')
            self.assertEqual(len(clock.sleeps), 1)
            self.assertGreaterEqual(clock.sleeps[0], DEFAULT_DELAY)
            self.assertLessEqual(clock.sleeps[0], DEFAULT_DELAY * 1.3 + 0.001)

    def test_slow_read_does_not_delay_the_next_read(self):
        clock = Clock()
        with patch.object(capture_module.time, 'monotonic', clock.monotonic), \
             patch.object(capture_module.time, 'sleep', clock.sleep), \
             patch.object(capture_module.subprocess, 'run') as run, \
             patch.object(capture_module, 'parse_cli_result', return_value={}):
            def slow_run(*args, **kwargs):
                clock.now += 5
                return SimpleNamespace(returncode=0, stdout='', stderr='')

            run.side_effect = slow_run
            browser = Browser('test', delay=DEFAULT_DELAY)
            browser.read('roster')
            clock.sleeps.clear()
            browser.read('roster')
            self.assertEqual(clock.sleeps, [])

    def test_delay_has_a_floor(self):
        for value in [0, 0.25, -1, 'slow']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                Browser('test', delay=value)
