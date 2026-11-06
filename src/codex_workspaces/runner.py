"""Restart one foreground service; cleanup groups on SIGINT, SIGTERM and SIGHUP."""
import atexit
import logging
from logging.handlers import RotatingFileHandler
import os
import signal
import subprocess
import threading
import time

from . import environment, network, services
from .util import lock, listening, stop_group, json_write


def run(config, profile, service):
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
        record = services.record(config, profile, service)
        token = services.birth(os.getpid())
        request = record.with_suffix(".stop")
        request.unlink(missing_ok=True)
        json_write(record, {"pid": os.getpid(), "birth": token})
        def requested():
            try:
                if request.read_text() == token:
                    stop()
            except FileNotFoundError:
                pass
            return stopped.is_set()
        try:
            while not requested():
                if service == "mihomo" and any(listening(profile.proxy[key]) for key in ("mixed_port", "controller_port")):
                    raise RuntimeError("Proxy ports occupied; refusing duplicate service")
                process = subprocess.Popen(network.command(config, profile, service), cwd=profile.cwd, env=env,
                                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
                def consume(stream):
                    while chunk := stream.readline(65536):
                        logger.info(chunk.decode("utf-8", errors="replace").rstrip())
                reader = threading.Thread(target=consume, args=(process.stdout,), daemon=True)
                reader.start()
                while process.poll() is None and not stopped.wait(0.3) and not requested():
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
            record.unlink(missing_ok=True)
            request.unlink(missing_ok=True)
