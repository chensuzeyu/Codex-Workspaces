"""Restart one foreground service; cleanup groups on SIGINT, SIGTERM and SIGHUP."""
import atexit
import logging
from logging.handlers import RotatingFileHandler
import os
import signal
import subprocess
import threading
import time

from . import environment, network, tmux
from .util import lock, listening, stop_group


def run(config, profile, service):
    tmux.owned(config, profile)
    if os.environ.get("CWS_PROFILE") != profile.name:
        raise RuntimeError("Service environment identity mismatch")
    with lock(config.identity + "-service-" + profile.name + "-" + service, blocking=False):
        profile.proxy_home.mkdir(mode=0o700, parents=True, exist_ok=True)
        log = profile.proxy_home / ("service-" + service + ".log")
        handler = RotatingFileHandler(log, maxBytes=10 * 1024 * 1024, backupCount=2, encoding="utf-8")
        os.chmod(log, 0o600)
        logger = logging.Logger("cws-" + profile.name + "-" + service)
        logger.addHandler(handler)
        stopped = threading.Event()
        process = None
        def stop(*_):
            stopped.set()
            if process is not None:
                stop_group(process)
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, stop)
        atexit.register(stop)
        env = environment.build(config, profile)
        for key in environment.PROXIES:
            env.pop(key, None)
        try:
            while not stopped.is_set():
                if service == "mihomo" and any(listening(profile.proxy[key]) for key in ("mixed_port", "controller_port")):
                    raise RuntimeError("Proxy ports occupied; refusing duplicate service")
                process = subprocess.Popen(network.command(config, profile, service), cwd=profile.cwd, env=env,
                                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
                def consume(stream):
                    while chunk := stream.readline(65536):
                        logger.info(chunk.decode("utf-8", errors="replace").rstrip())
                reader = threading.Thread(target=consume, args=(process.stdout,), daemon=True)
                reader.start()
                print(service + " running; details in private runtime log", flush=True)
                while process.poll() is None and not stopped.wait(0.3):
                    pass
                stop_group(process)
                reader.join(timeout=2)
                process.stdout.close()
                if not stopped.is_set():
                    logger.info("child exit=%s; retrying", process.returncode)
                    stopped.wait(5)
        finally:
            stop()
            atexit.unregister(stop)
            handler.close()
