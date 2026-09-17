#!/usr/bin/env python3
"""Double-fork daemonizer — the only launch pattern that survives the
sandbox's inter-tool-call process cleanup (agent-browser uses the same
trick). Usage: daemonize.py <logfile> <cwd> <cmd> [args...]"""
import os
import sys
import time

log, cwd, cmd = sys.argv[1], sys.argv[2], sys.argv[3:]
logf = open(log, "ab", buffering=0)
devnull = os.open(os.devnull, os.O_RDWR)

# fork 1
pid = os.fork()
if pid > 0:
    print(f"fork1 pid={pid}")
    sys.exit(0)
os.setsid()
# fork 2 — reparent to init, fully detached
pid = os.fork()
if pid > 0:
    print(f"fork2 pid={pid}")
    sys.exit(0)
os.umask(0)
os.chdir(cwd)
os.dup2(devnull, 0)
os.dup2(logf.fileno(), 1)
os.dup2(logf.fileno(), 2)
if logf.fileno() > 2:
    logf.close()
os.execvp(cmd[0], cmd)
