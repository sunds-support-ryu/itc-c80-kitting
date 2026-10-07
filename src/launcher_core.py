"""GitHub Release file updater. Downloads and hashes everything before replacing code."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import time
import uuid
import requests
import yaml

MANAGED = {'src/' + name for name in ('bootstrap.py', 'appearance_inspection.py', 'inspection_engine.py',
    'config_import.py', 'inspection_runtime.py', 'native_window.py', 'evidence_store.py', 'network_workflow.py', 'job_store.py',
    'gas_post.py', 'l2_device_info.py', 'instance_lock.py', 'ir_cut_check.py', 'app_paths.py')}
MANAGED.update({'src/launcher.py', 'src/launcher_core.py', 'src/script_launcher.py',
                'examples/gas_receiver.gs', 'docs/LAUNCHER.md', 'docs/JOB_WORKFLOW.md', 'docs/DESKTOP_UI.md', 'docs/SAVE_LAYOUT.md'})



def safe_path(root, path):
    parsed = PurePosixPath(path)
    code_or_doc = parsed.parts and parsed.parts[0] in ('src','docs','examples') and parsed.suffix in ('.py','.md','.gs','.yaml','.html','.css','.js')
    if (path not in MANAGED and not code_or_doc) or '\\' in path or parsed.as_posix()!=path or any(part in ('..', '.') for part in parsed.parts):
        raise ValueError('更新対象外のパス: ' + str(path))
    destination = (root / path).resolve()
    if not destination.is_relative_to(root.resolve()):
        raise ValueError('更新パスがフォルダ外です')
    return destination


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_yaml(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        yaml.safe_dump(value, stream, allow_unicode=True, sort_keys=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class Updater:
    def __init__(self, root, repository, log=print):
        import re
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
            raise ValueError('GitHubリポジトリは owner/repo 形式です')
        self.root, self.repository, self.log = Path(root), repository, log
        self.session = requests.Session()
        self.session.headers['User-Agent'] = 'ITC-C80-Launcher/1.0'
        token = os.environ.get('ITC_GITHUB_TOKEN')
        if token:
            self.session.headers['Authorization'] = 'Bearer ' + token

    def close(self):
        self.session.close()

    def fetch(self, url, limit, accept=None):
        headers = {'Accept': accept} if accept else {}
        with self.session.get(url, headers=headers, stream=True, timeout=(10, 30)) as response:
            response.raise_for_status()
            pieces, size = [], 0
            for piece in response.iter_content(65536):
                size += len(piece)
                if size > limit:
                    raise ValueError('ダウンロードサイズが上限を超えました')
                pieces.append(piece)
            return b''.join(pieces)

    def update(self):
        transaction = self.root / 'data' / 'updates' / 'transaction.yaml'
        if transaction.exists():
            previous = yaml.safe_load(transaction.read_text(encoding='utf-8'))
            backup_root = (self.root / previous['backup']).resolve()
            if not backup_root.is_relative_to((self.root / 'data' / 'update_backups').resolve()):
                raise ValueError('更新復旧パスが不正です')
            for entry in reversed(previous['files']):
                destination = safe_path(self.root, entry['path'])
                old = backup_root / entry['path']
                if entry['existed']:
                    shutil.copy2(old, destination)
                else:
                    destination.unlink(missing_ok=True)
            transaction.unlink()
            self.log('中断された更新を前のバージョンへ復旧しました')
        api = 'https://api.github.com/repos/' + self.repository + '/releases/latest'
        release = json.loads(self.fetch(api, 2_000_000, 'application/vnd.github+json'))
        assets = {asset['name']: asset for asset in release['assets']}
        manifest_asset = assets.get('update-manifest.yaml')
        if not manifest_asset:
            raise ValueError('最新Releaseに update-manifest.yaml がありません')
        def asset_url(asset):
            return asset['url'] if os.environ.get('ITC_GITHUB_TOKEN') else asset['browser_download_url']
        manifest = yaml.safe_load(self.fetch(asset_url(manifest_asset), 500_000, 'application/octet-stream'))
        if not isinstance(manifest, dict) or manifest.get('schema') != 1 or not isinstance(manifest.get('files'), list):
            raise ValueError('更新YAMLの形式が不正です')
        if manifest.get('entrypoint') != 'src/bootstrap.py':
            raise ValueError('起動ファイルは bootstrap.py のみ許可します')
        files, seen = [], set()
        import re
        for item in manifest['files']:
            destination = safe_path(self.root, item['path'])
            if item['path'] in seen or not re.fullmatch('[0-9a-f]{64}', str(item.get('sha256', ''))):
                raise ValueError('重複パスまたはSHA256不正')
            seen.add(item['path'])
            if not item.get('version') or item.get('asset') not in assets or not isinstance(item.get('size'), int) or not 0 <= item['size'] <= 20_000_000:
                raise ValueError('ファイルのversion / asset / sizeが不正')
            files.append((item, destination))
        if not {'src/bootstrap.py', 'src/inspection_runtime.py', 'src/native_window.py'}.issubset(seen):
            raise ValueError('必須ファイルが更新YAMLにありません')
        changes = [(item, dst) for item, dst in files if not dst.exists() or digest(dst) != item['sha256']]
        self.log('Release ' + str(manifest.get('version')) + ': ' + str(len(changes)) + ' ファイルを更新')
        staging_root = self.root / 'data' / 'updates'
        staging_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=staging_root) as folder:
            stage = Path(folder)
            for item, dst in changes:
                self.log('Download ' + item['path'] + ' / ' + str(item['version']))
                content = self.fetch(asset_url(assets[item['asset']]), item['size'] + 1, 'application/octet-stream')
                if len(content) != item['size'] or hashlib.sha256(content).hexdigest() != item['sha256']:
                    raise ValueError('サイズ / SHA256が一致しません: ' + item['path'])
                target = stage / item['path']
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            backup = self.root / 'data' / 'update_backups' / (time.strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8])
            replaced = []
            transaction_data = {'backup': backup.relative_to(self.root).as_posix(), 'files': []}
            try:
                for item, dst in changes:
                    old = backup / item['path']
                    existed = dst.exists()
                    if existed:
                        old.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(dst, old)
                    transaction_data['files'].append({'path': item['path'], 'existed': existed})
                    save_yaml(transaction, transaction_data)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(stage / item['path'], dst)
                    replaced.append((dst, old, existed))
                save_yaml(self.root / 'data' / 'installed_versions.yaml', manifest)
                transaction.unlink(missing_ok=True)
            except Exception:
                for dst, old, existed in reversed(replaced):
                    if existed:
                        shutil.copy2(old, dst)
                    else:
                        dst.unlink(missing_ok=True)
                transaction.unlink(missing_ok=True)
                raise
        return manifest
