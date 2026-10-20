"""Finite qualification runner with explicit forbidden-I/O audit guards."""
from pathlib import Path
import datetime
import json
import os
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'source'), str(ROOT / 'qualification')]
os.environ['TMPDIR'] = str(ROOT / 'tmp')
blocked_events = []

def audit(event, args):
    device_open = event == 'open' and args and isinstance(args[0], (str, bytes)) and os.fsdecode(args[0]).startswith('/dev/')
    if (device_open or event.startswith('socket.') or event.startswith('subprocess.') or
            event in {'os.system', 'os.posix_spawn', 'os.exec', 'os.spawn', 'os.fork', 'ctypes.dlopen'}):
        blocked_events.append(event)
        raise RuntimeError('Forbidden external operation during fake qualification: ' + event)

sys.addaudithook(audit)
loader = unittest.TestLoader()
baseline = loader.discover(str(ROOT / 'source' / 'tests'))
baseline_count = baseline.countTestCases()
extra = loader.loadTestsFromName('test_offline_acceptance')
acceptance_count = extra.countTestCases()
start = time.monotonic()
with (ROOT / 'TEST-OUTPUT.txt').open('x') as log:
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(unittest.TestSuite([baseline, extra]))
record = {
    'atUtc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'baselineTests': baseline_count,
    'newJourneyTests': acceptance_count,
    'testsRun': result.testsRun,
    'failures': len(result.failures),
    'errors': len(result.errors),
    'skipped': len(result.skipped),
    'successful': result.wasSuccessful(),
    'elapsedSeconds': time.monotonic() - start,
    'forbiddenOperationAttempts': blocked_events,
    'networkHardwareProviderProcessLaunchGuarded': True,
    'observedCodeMode': 'synthetic fake model/source/transport and explicit fixture operator only',
    'hostedOrActualDeviceClaim': False,
}
(ROOT / 'TEST-RESULT.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record))
raise SystemExit(0 if result.wasSuccessful() and not blocked_events else 1)
