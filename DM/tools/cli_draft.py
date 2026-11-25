"""What the workers for signed-in AI command-line tools (Codex, Gemini) share: run the tool for one draft.

Each worker knows its own tool: how to start it without tools, how to sign it in on the subscription and how
to read its event stream. This module runs the tool: it feeds the prompt, hands each output line to the
worker's reader (which raises to stop the tool at a forbidden event), stops a tool that takes too long,
removes scratch folders that a cancelled draft left behind, and reads the model's final text as one JSON
object.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time

from job_service import SubprocessProcess

MAX_ANSWER = 8 * 1024 * 1024
STALE_AFTER = 24 * 3600  # scratch folders of draft runs that were cancelled are removed after a day
NO_WINDOW = 0x08000000 if os.name == 'nt' else 0
FENCE = re.compile(r'^```(?:json)?\s*|\s*```$')


class DraftError(ValueError):
    """A draft that the tool could not or must not produce. The message is shown to the GM."""


def sweep_stale(prefix, root=None, now=None):
    """Remove scratch folders that a cancelled draft could not clean up (it is stopped without warning)."""
    root = root or tempfile.gettempdir()
    for name in os.listdir(root):
        path = os.path.join(root, name)
        try:
            if (
                name.startswith(prefix)
                and (now or time.time()) - os.path.getmtime(path) > STALE_AFTER
            ):
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass  # another worker removed it first


def json_draft(text, name, error=DraftError):
    """The one JSON object in the model's final text: bare, fenced, or (failing both) between its outer braces."""
    if not text:
        raise error(f'{name} returned no answer.')
    if len(text) > MAX_ANSWER:
        raise error(f'{name} returned an answer larger than 8 MB.')
    text = FENCE.sub('', text.strip())
    try:
        draft = json.loads(text)
    except ValueError:
        start, end = text.find('{'), text.rfind('}')
        try:
            draft = json.loads(text[start : end + 1]) if 0 <= start < end else None
        except ValueError:
            draft = None
        if draft is None:
            raise error(f'{name} did not return JSON.') from None
    if not isinstance(draft, dict):
        raise error(f'{name} returned a proposal that is not an object.')
    return draft


def feed(process, prompt):
    """Send the prompt on a thread, so a large one cannot block the tool's own output."""

    def write():
        try:
            process.stdin.write(prompt)
            process.stdin.close()
        except OSError:
            pass  # the tool stopped reading: its exit says why

    threading.Thread(target=write, daemon=True).start()


def run(command, prompt, env, read, *, name, timeout, error=DraftError, cwd=None):
    """Run the tool, passing each output line to `read`, and return its exit code.

    `read` raises `error` to stop the tool at once (a tool request); the tool is also stopped, with its
    process tree, when it runs longer than `timeout` seconds.
    """
    with subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding='utf-8',
        errors='replace',
        env=env,
        cwd=cwd,
        creationflags=NO_WINDOW,
    ) as process:
        expired = threading.Event()

        def give_up():
            expired.set()
            SubprocessProcess(process).terminate()

        timer = threading.Timer(timeout, give_up)
        timer.start()
        try:
            feed(process, prompt)
            for line in process.stdout:
                read(line)
        except error:
            SubprocessProcess(process).terminate()
            raise
        finally:
            timer.cancel()
        code = process.wait()
    if expired.is_set():
        raise error(f'{name} took too long and was stopped.')
    return code
