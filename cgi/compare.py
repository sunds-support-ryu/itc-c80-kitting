"""Compare static CGI contracts with the workspace library without importing scripts."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys

TECHNICAL_FIELDS = ('method', 'path', 'query', 'auth', 'effects', 'encoding', 'parameters',
                    'required_parameters', 'required_body', 'fixed_body', 'headers', 'required_headers')


def contract(path):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'ENDPOINT' for target in node.targets):
            return {keyword.arg: ast.literal_eval(keyword.value) for keyword in node.value.keywords}
    raise ValueError('Endpoint contract not found: ' + str(path))


def compare(workspace, folder=None):
    folder = folder or Path(__file__).resolve().parent
    inventory = json.loads((folder / 'inventory.json').read_text(encoding='utf-8'))
    rows = []
    for item in inventory:
        local, reference = folder / (item['id'] + '.py'), workspace / item['reference']
        left, right = contract(local), contract(reference)
        differences = {key: {'local': left.get(key), 'reference': right.get(key)}
                       for key in TECHNICAL_FIELDS if left.get(key) != right.get(key)}
        rows.append({**item, 'technical_match': not differences, 'differences': differences,
                     'local_sha256': hashlib.sha256(local.read_bytes()).hexdigest(),
                     'reference_sha256': hashlib.sha256(reference.read_bytes()).hexdigest(),
                     'hardware_validation': 'NOT_TESTED_IN_THIS_TASK'})
    fingerprints = {}
    for relative in ('CGI/common.py', 'ITEC/get_info.py', 'ITEC/change_ip.py', 'ITEC/upload_config.py',
                     'ITEC/upgrade_fw.py', 'ITC-C80/config_writer.py', 'ITEC.py', '251202_ITEC.py',
                     'ITC-C80/ITC-C80修復/src/config_import.py', 'ITC-C80/ITC-C80修復/src/inspection_engine.py'):
        source = workspace / relative
        fingerprints[relative] = hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else None
    return {'comparison': rows, 'source_fingerprints': fingerprints,
            'scope': 'All current ITC-C80 HTTP camera commands plus catalogued Milesight camera candidates; unrelated gateway/NVR products excluded.',
            'important_differences': [
                'Workspace Milesight get_info uses Basic; current repair code uses Digest with explicit Basic-challenge fallback.',
                'The independent runner selects one authentication scheme, does not warm up the root page, and sends one CGI only.',
                'Current app performs a multi-step import-status/identity workflow. These probes deliberately do not orchestrate it.',
                'Legacy RSA/AES parameters match the current and legacy Python algorithms; marker acceptance is not verified application.',
                'ITEC/change_ip.py treats some timeouts/JSON errors as success. Probes retain unknown outcome; L2 MAC SET is a different protocol.',
                'Reset option=2 behavior, post-config account changes and supported model-specific candidates require physical testing.',
                'Current IR-CUT testing analyzes RTSP frames; it does not send a dedicated IR-CUT CGI.',
                'Files referenced outside the Python workspace are provenance only, not read or validated by this comparison.'
            ]}


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=Path(__file__).resolve().parents[3])
    args = parser.parse_args()
    folder = Path(__file__).resolve().parent
    report = compare(args.workspace)
    (folder / 'comparison_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# CGI source comparison', '',
             'Source contracts only. This report does not establish actual device behavior.', '',
             '| Probe | Scope | Method | Path | Reference contract |',
             '| --- | --- | --- | --- | --- |']
    for row in report['comparison']:
        lines.append(f"| {row['id']}.py | {row['scope']} | {row['method']} | `{row['path']}` | {'MATCH' if row['technical_match'] else 'DIFFERENT'} |")
    lines.extend(['', '## Operational differences', ''])
    lines.extend('- ' + note for note in report['important_differences'])
    lines.extend(['', 'The JSON report records exact reference paths and SHA256 fingerprints. The workspace sources are unchanged.'])
    (folder / 'COMPARISON.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('Compared probes:', len(report['comparison']))
    print('Technical differences:', sum(not row['technical_match'] for row in report['comparison']))


if __name__ == '__main__':
    main()
