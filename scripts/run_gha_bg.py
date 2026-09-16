#!/usr/bin/env python3
"""Double-forked background runner for the gha sequence (playwright transport)."""
import os, sys

def daemonize():
    if os.fork():
        sys.exit(0)
    os.setsid()
    if os.fork():
        sys.exit(0)
    sys.stdout.flush(); sys.stderr.flush()
    devnull = os.open('/dev/null', os.O_RDWR)
    os.dup2(devnull, 0)

log = open('/tmp/gha-test.log', 'wb', buffering=0)
os.dup2(log.fileno(), 1)
os.dup2(log.fileno(), 2)

daemonize()
os.environ['AIX_TRANSPORT'] = 'playwright'
os.chdir('/home/z/my-project')
os.execvp('bash', ['bash', 'run.sh', 'gha'])
