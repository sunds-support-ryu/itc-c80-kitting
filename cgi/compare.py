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
            if isinstance(node.value.func, ast.Name) and node.value.func.id == 'reset_endpoint':
                option=ast.literal_eval(node.value.args[0])
                helper=ast.parse(path.with_name('reset.py').read_text(encoding='utf-8'))
                function=next(item for item in helper.body if isinstance(item,ast.FunctionDef) and item.name=='reset_endpoint')
                call=next(item.value for item in function.body if isinstance(item,ast.Return))
                class Substitute(ast.NodeTransformer):
                    def visit_Name(self,item):
                        return ast.Constant(option) if item.id=='option' else item
                result={}
                for keyword in call.keywords:
                    try:result[keyword.arg]=ast.literal_eval(Substitute().visit(keyword.value))
                    except (ValueError,TypeError):pass
                result.update({keyword.arg:ast.literal_eval(keyword.value) for keyword in node.value.keywords})
                return result
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
    for relative in ('CGI/common.py', 'ITEC/legacy/itec/get_info.py', 'ITEC/legacy/itec/change_ip.py', 'ITEC/legacy/itec/upload_config.py',
                     'ITEC/legacy/itec/upgrade_fw.py', 'ITEC/legacy/itc-c80/config_writer.py', 'ITEC/legacy/root-scripts/ITEC.py', 'ITEC/legacy/root-scripts/251202_ITEC.py',
                     'ITEC/products/ITC-C80/src/config_import.py', 'ITEC/products/ITC-C80/src/inspection_engine.py'):
        source = workspace / relative
        fingerprints[relative] = hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else None
    return {'comparison': rows, 'source_fingerprints': fingerprints,
            'scope': 'All current ITC-C80 HTTP camera commands plus catalogued Milesight camera candidates; unrelated gateway/NVR products excluded.',
            'important_differences': [
                'Workspace Milesight get_info uses Basic; current repair code uses Digest with explicit Basic-challenge fallback.',
                'The independent runner selects one authentication scheme, does not warm up the root page, and sends one CGI only.',
                'Current app performs a multi-step import-status/identity workflow. These probes deliberately do not orchestrate it.',
                'Legacy RSA/AES parameters match the current and legacy Python algorithms; marker acceptance is not verified application.',
                'ITEC/legacy/itec/change_ip.py treats some timeouts/JSON errors as success. Probes retain unknown outcome; L2 MAC SET is a different protocol.',
                'Reset option=2 behavior, post-config account changes and supported model-specific candidates require physical testing.',
                'Current IR-CUT testing analyzes RTSP frames; it does not send a dedicated IR-CUT CGI.',
                'Files referenced outside the Python workspace are provenance only, not read or validated by this comparison.'
            ]}


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    default_workspace = next((path for path in Path(__file__).resolve().parents if (path / 'CGI/products').is_dir()), None)
    parser.add_argument('--workspace', type=Path, default=default_workspace)
    args = parser.parse_args()
    if args.workspace is None:
        parser.error('Workspace CGI catalog not found. Specify --workspace explicitly')
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
