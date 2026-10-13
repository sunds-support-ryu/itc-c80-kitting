"""Unified reset API and offline/explicit-execute CLI for retention options 1-4."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from camera_cgi.reset import reset, validate_option, OPTION_NOTES
if __package__:
    from .common import Endpoint, show_command
else:
    from common import Endpoint, show_command


def reset_endpoint(option, name='reset', sources=(), notes=None):
    validate_option(option)
    return Endpoint(name=name, method='POST', path='/cgi-bin/admin/admin.cgi',
                    auth=('digest','basic'), effects='reset', sources=tuple(sources),
                    query=(('action','set.system.reset'),('format','json')),
                    encoding='json', fixed_body=(('options',option),),
                    notes=notes or OPTION_NOTES[option])


def prepare(base_url, option, **kwargs):
    """Prepare one selected reset request without sending it; option cannot be overridden."""
    kwargs.setdefault('body', {})
    return reset_endpoint(option).prepare(base_url, **kwargs)


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('--option', type=int, choices=(1,2,3,4), required=True)
    args, remaining=parser.parse_known_args()
    sys.argv=[sys.argv[0],*remaining]
    show_command(reset_endpoint(args.option), 'reset')
