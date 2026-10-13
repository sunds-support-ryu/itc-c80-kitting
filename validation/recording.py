"""Timestamped hardware evidence stored as literal .py data, never executable logs."""
import ast
from datetime import datetime, timezone, timedelta
import hashlib
from pathlib import Path
import pprint
import re
import statistics
import time
import uuid


def read_literal(path, name):
    tree = ast.parse(Path(path).read_text(encoding='utf-8-sig'))
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in statement.targets):
            return ast.literal_eval(statement.value)
    raise ValueError('Literal data not found: ' + name)


def write_literal(path, name, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text('"""Private hardware validation evidence. Do not publish."""\n' + name + ' = ' + pprint.pformat(value, width=115, sort_dicts=False) + '\n', encoding='utf-8')
    try:
        for attempt in range(4):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 3:
                    raise
                time.sleep(.1 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def timestamps():
    now = datetime.now(timezone.utc)
    return {'utc': now.isoformat(timespec='milliseconds'),
            'jst': now.astimezone(timezone(timedelta(hours=9))).isoformat(timespec='milliseconds'),
            'unix_ms': round(now.timestamp() * 1000)}


class Recorder:
    def __init__(self, folder, secrets=()):
        self.folder = Path(folder)
        self.secrets = [value for value in secrets if value]
        self.path = self.folder / 'validation_record.py'
        self.data = read_literal(self.path, 'REPORT') if self.path.exists() else {
            'schema': 1, 'created': timestamps(), 'events': [], 'phases': {}, 'pending': [], 'metadata': {}}

    def safe(self, value):
        from urllib.parse import quote
        if isinstance(value, dict):
            return {key: '<redacted>' if re.search(r'password|encryptpwd|^pwd$|private.?key|^keyid$', str(key), re.I)
                    else self.safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.safe(item) for item in value]
        if isinstance(value, bytes):
            return {'bytes': len(value), 'sha256': hashlib.sha256(value).hexdigest()}
        if isinstance(value, str):
            for secret in self.secrets:
                value = value.replace(quote(secret, safe=''), '<redacted>').replace(secret, '<redacted>')
            value = re.sub(r'([?&](?:pwd|encryptPwd|keyId)=)[^&\s]+', r'\1<redacted>', value, flags=re.I)
            value = re.sub(r'<(key|keyID)>.*?</\1>', r'<\1>redacted</\1>', value, flags=re.S | re.I)
            return value
        return value

    def save(self):
        self.data['updated'] = timestamps()
        self.data['response_statistics_ms'] = {}
        for module in {event['module'] for event in self.data['events']}:
            events = [event for event in self.data['events'] if event['module'] == module]
            values = sorted(event['duration_ms'] for event in events if 'duration_ms' in event)
            if values:
                self.data['response_statistics_ms'][module] = {
                    'count': len(values), 'minimum': values[0], 'maximum': values[-1],
                    'mean': round(statistics.mean(values), 3), 'median': round(statistics.median(values), 3),
                    'p95_observed': values[max(0, __import__('math').ceil(len(values) * .95) - 1)]}
            write_literal(self.folder / (module + '.py'), 'VALIDATION', self.safe({'module': module, 'events': events}))
        write_literal(self.path, 'REPORT', self.safe(self.data))

    def call(self, module, context, operation):
        start = timestamps()
        counter = time.perf_counter_ns()
        event = {'module': module, 'context': context, 'started': start, 'physical_effect_verified': False}
        try:
            result = operation()
            event.update(result=self.safe(result), outcome='response_observed')
            return result
        except Exception as error:
            event.update(error_type=type(error).__name__, error=self.safe(str(error)), outcome='unknown')
            raise
        finally:
            event.update(ended=timestamps(), duration_ms=round((time.perf_counter_ns() - counter) / 1_000_000, 3))
            self.data['events'].append(event)
            self.save()
            print(module, context, event['duration_ms'], 'ms', event['outcome'], flush=True)

    def mark_effect(self, module, evidence):
        self.data['events'].append({'module': module, 'context': 'effect_verified', 'started': timestamps(),
                                    'physical_effect_verified': True, 'result': self.safe(evidence), 'outcome': 'verified'})
        self.save()

    def latch(self, phase):
        if phase in self.data['phases']:
            raise ValueError('Phase already attempted; inspect evidence before any repeat: ' + phase)
        self.data['phases'][phase] = {'state': 'ATTEMPT_STARTED', 'timestamp': timestamps()}
        self.save()

    def finish_phase(self, phase, detail):
        self.data['phases'][phase].update(state='COMPLETED', completed=timestamps(), detail=self.safe(detail))
        self.save()
