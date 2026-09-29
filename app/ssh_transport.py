import asyncio
from contextlib import asynccontextmanager

import asyncssh


class ProbeError(Exception):
    """Machine-readable error which never embeds remote stderr or credentials."""


def connection_options(server):
    options = dict(host=server.host, port=server.port, username=server.username,
                   connect_timeout=8, login_timeout=8, keepalive_interval=15)
    key = server.resolved_key_path()
    if key:
        options["client_keys"] = [key]
    if server.strict_host_key and server.host_key:
        options["known_hosts"] = ([asyncssh.import_public_key(server.host_key)], [], [])
    elif not server.strict_host_key:
        options["known_hosts"] = None
    return options


@asynccontextmanager
async def connect(server):
    try:
        async with asyncssh.connect(**connection_options(server)) as conn:
            yield conn
    except asyncssh.HostKeyNotVerifiable as exc:
        raise ProbeError("host_key_unverified") from exc
    except asyncssh.PermissionDenied as exc:
        raise ProbeError("ssh_auth_failed") from exc
    except (asyncssh.Error, OSError, asyncio.TimeoutError) as exc:
        raise ProbeError("ssh_unavailable") from exc


async def read_limited(stream, limit=131072):
    chunks, count = [], 0
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            return "".join(chunks)
        count += len(chunk)
        if count > limit:
            raise ProbeError("output_limit_exceeded")
        chunks.append(chunk)


async def run_script(server, command, script, timeout=35):
    async with connect(server) as conn:
        async with conn.create_process(command) as proc:
            proc.stdin.write(script)
            proc.stdin.write_eof()
            try:
                stdout, _ = await asyncio.wait_for(asyncio.gather(
                    read_limited(proc.stdout), read_limited(proc.stderr, 8192)), timeout)
                await asyncio.wait_for(proc.wait_closed(), 3)
                if proc.exit_status != 0:
                    raise ProbeError("remote_probe_failed")
                return stdout
            except asyncio.TimeoutError as exc:
                proc.close()
                raise ProbeError("probe_timeout") from exc
