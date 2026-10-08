"""UI-only localization. Backend state and diagnostic logs remain English."""
import importlib
import logging
import re


class Translator:
    def __init__(self, language='en'):
        self.english = self._load('en')
        self.sources = {text: key for key, text in self.english.items()}
        self.patterns = []
        for key, text in self.english.items():
            fields = list(re.finditer(r'\{(field\d+|stage|detail)\}', text))
            if not fields:
                continue
            parts, cursor = [], 0
            seen = set()
            for field in fields:
                name = field.group(1)
                parts.append(re.escape(text[cursor:field.start()]))
                parts.append('(?P=' + name + ')' if name in seen else '(?P<' + name + '>.*?)')
                seen.add(name)
                cursor = field.end()
            parts.append(re.escape(text[cursor:]))
            self.patterns.append((len(text) - sum(len(f.group()) for f in fields), key,
                                  re.compile(''.join(parts), re.DOTALL)))
        self.patterns.sort(key=lambda item: item[0], reverse=True)
        self.set_language(language)

    @staticmethod
    def _load(language):
        try:
            return importlib.import_module('locales.' + language).MESSAGES
        except (ImportError, AttributeError, SyntaxError):
            logging.warning('Language pack unavailable: %s; using English fallback', language)
            return {}

    def set_language(self, language):
        self.language = language if language in ('en', 'ja') else 'en'
        self.messages = self._load(self.language) if self.language != 'en' else self.english

    def get(self, key, **values):
        source = self.english.get(key, key)
        message = self.messages.get(key, source)
        if values:
            try:
                return message.format(**values)
            except (KeyError, ValueError):
                logging.warning('Invalid translation placeholders: %s', key)
                return source.format(**values)
        return message

    def text(self, source, depth=0):
        """Render an English backend message without modifying its stored value."""
        source = str(source)
        if self.language == 'en':
            return source
        key = self.sources.get(source)
        if key:
            return self.get(key)
        if depth < 4:
            for _, key, pattern in self.patterns:
                match = pattern.fullmatch(source)
                if match:
                    values = {name: self.text(value, depth + 1) if value != source else value
                              for name, value in match.groupdict().items()}
                    return self.get(key, **values)
        # Unrecognized device data and third-party errors retain their original text.
        return source


translator = Translator()
_japanese_sources = {text: translator.english.get(key, text) for key, text in Translator._load('ja').items()}


def ui(source):
    return translator.text(source)


def set_language(language):
    translator.set_language(language)


def localize_widgets(widget):
    """Localize display text only; variables, callbacks and selection IDs stay English."""
    try:
        if 'text' in widget.keys():
            value = str(widget.cget('text'))
            if value != getattr(widget, '_translated_text', None):
                widget._english_text = _japanese_sources.get(value, value)
            translated = ui(getattr(widget, '_english_text', value))
            widget.configure(text=translated)
            widget._translated_text = translated
        if widget.winfo_class() == 'TNotebook':
            for tab in widget.tabs():
                child = widget.nametowidget(tab)
                if not hasattr(child, '_english_tab'):
                    child._english_tab = widget.tab(tab, 'text')
                widget.tab(tab, text=ui(child._english_tab))
        for child in widget.winfo_children():
            localize_widgets(child)
    except Exception:
        logging.exception('UI localization failed')
