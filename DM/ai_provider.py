"""Configured structured-draft providers. Jobs keep a provider-specific launch command, not credentials."""

import json
import os
import re
import shutil
import sys

import campaign
import config

KEY_ENV = re.compile(r'[A-Z][A-Z0-9_]{0,63}')


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


PROVIDERS = {'claude': ClaudeCLI(), 'openai': OpenAIResponses()}


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
