"""Configured structured-draft providers. Jobs keep a provider-specific launch command, not credentials."""

import json
import os
import re
import shutil
import sys

import campaign
import config

KEY_ENV = re.compile(r'[A-Z][A-Z0-9_]{0,63}')
# A model name a command-line provider can be given safely. On Windows `gemini` is a `.cmd` shim that cmd.exe
# parses, so a name stays to letters, digits, dots, dashes and underscores.
CLI_MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}')


class ClaudeCLI:
    label = 'Claude Code'

    def available(self, ai):
        return bool(shutil.which('claude'))

    def command(self, ai, kind, schema):
        exe = shutil.which('claude')
        if not exe:
            raise ValueError('Claude Code (the claude command) is not installed or not on PATH')
        command = [
            exe,
            '-p',
            '--output-format',
            'json',
            '--json-schema',
            json.dumps(schema),
            '--tools',
            '',
            '--restricted',
            '--strict-mcp-config',
        ]
        if ai.get('model'):
            command += ['--model', ai['model']]
        return command


class CodexCLI:
    """The signed-in Codex CLI (a ChatGPT subscription, never an API key), run by `codex_worker.py`.

    The worker holds Codex to the draft contract: no tools, one JSON object, and the subscription login.
    """

    label = 'Codex'

    def available(self, ai):
        return bool(shutil.which('codex'))

    def command(self, ai, kind, schema):
        exe = shutil.which('codex')
        if not exe:
            raise ValueError('Codex (the codex command) is not installed or not on PATH')
        command = [
            sys.executable,
            '-u',
            os.path.join(campaign.INSTALL, 'tools', 'codex_worker.py'),
            kind,
            exe,
        ]
        return command + [ai['model']] if ai.get('model') else command


class GeminiCLI:
    """The signed-in Gemini CLI (a Google AI Pro or other Gemini subscription, never an API key), run by
    `gemini_worker.py`, which holds it to the draft contract: no tools, one JSON object, the Google login.
    """

    label = 'Gemini'

    def available(self, ai):
        return bool(shutil.which('gemini'))

    def command(self, ai, kind, schema):
        exe = shutil.which('gemini')
        if not exe:
            raise ValueError('Gemini CLI (the gemini command) is not installed or not on PATH')
        model = ai.get('model') or ''
        if model and not CLI_MODEL.fullmatch(model):
            raise ValueError('Use a Gemini model name such as gemini-2.5-pro.')
        command = [
            sys.executable,
            '-u',
            os.path.join(campaign.INSTALL, 'tools', 'gemini_worker.py'),
            kind,
            exe,
        ]
        return command + [model] if model else command


class OpenAIResponses:
    label = 'OpenAI API'

    def available(self, ai):
        return bool(ai.get('model') and os.environ.get(ai.get('key_env', 'OPENAI_API_KEY')))

    def command(self, ai, kind, schema):
        if not ai.get('model'):
            raise ValueError('Choose an OpenAI model in Settings first.')
        if not self.available(ai):
            raise ValueError(
                'Set the configured OpenAI API key environment variable before drafting.'
            )
        return [
            sys.executable,
            '-u',
            os.path.join(campaign.INSTALL, 'tools', 'openai_worker.py'),
            kind,
            ai['model'],
            ai.get('key_env', 'OPENAI_API_KEY'),
        ]


PROVIDERS = {
    'claude': ClaudeCLI(),
    'codex': CodexCLI(),
    'gemini': GeminiCLI(),
    'openai': OpenAIResponses(),
}


def clean_settings(raw):
    """Validate the saved selector and key name; never accept a key value from the browser."""
    if not isinstance(raw, dict):
        raise ValueError('AI settings must be an object.')
    provider = str(raw.get('provider') or 'claude')
    if provider not in PROVIDERS:
        raise ValueError('Choose a supported AI provider.')
    model = str(raw.get('model') or '').strip()[:100]
    if provider == 'openai' and not model:
        raise ValueError('Choose an OpenAI model.')
    if provider == 'gemini' and model and not CLI_MODEL.fullmatch(model):
        raise ValueError('Use a Gemini model name such as gemini-2.5-pro.')
    key_env = str(raw.get('key_env') or 'OPENAI_API_KEY')
    if not KEY_ENV.fullmatch(key_env):
        raise ValueError('Use an environment variable name such as OPENAI_API_KEY.')
    return {'provider': provider, 'model': model, 'key_env': key_env}


def selected(ai=None):
    ai = ai or config.settings()['ai']
    provider = PROVIDERS.get(ai.get('provider', 'claude'))
    if provider is None:
        raise ValueError('Choose a supported AI provider in Settings.')
    return provider


def status(ai=None):
    ai = ai or config.settings()['ai']
    provider = selected(ai)
    return {
        'provider': ai.get('provider', 'claude'),
        'label': provider.label,
        'available': provider.available(ai),
        'key_available': bool(os.environ.get(ai.get('key_env', 'OPENAI_API_KEY'))),
    }


def command(kind, schema):
    ai = config.settings()['ai']
    return selected(ai).command(ai, kind, schema)
