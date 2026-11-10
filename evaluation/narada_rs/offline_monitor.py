"""E6: physical record of the house while the network is gone.

Runs beside the live service (which owns GPIO17/27) and writes one JSON line
per observation:
    contact  -- relay 2's contact, wired as COM -> GND, NO -> GPIO24 (pulled
                up), kernel-timestamped edges: what the relay physically did
    lamp     -- GPIO17's level as driven by the service (relay 1, active low)
    net      -- whether the internet answers (TCP to 1.1.1.1:443), every 5 s
Wall-clock times (time.time()) line up with the service's actuations.jsonl.

    python3 -m evaluation.narada_rs.offline_monitor --seconds 780 --out FILE
"""
import argparse
import json
import socket
import subprocess
import threading
import time

import gpiod

from evaluation.narada_rs.relay_latency import SENSE_PIN, _header_chip


def _online():
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=2).close()
        return True
    except OSError:
        return False


def _lamp_level():
    out = subprocess.run(["pinctrl", "lev", "17"], capture_output=True, text=True).stdout.strip()
    return {"0": "on", "1": "off"}.get(out, out)       # active low


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    lock = threading.Lock()
    fh = open(args.out, "a", encoding="utf-8")

    def emit(kind, **kw):
        with lock:
            fh.write(json.dumps({"t": time.time(), "kind": kind, **kw}) + "\n")
            fh.flush()

    line = _header_chip().get_line(SENSE_PIN)
    line.request(consumer="drishti-e6", type=gpiod.LINE_REQ_EV_BOTH_EDGES,
                 flags=gpiod.LINE_REQ_FLAG_BIAS_PULL_UP)
    time.sleep(0.05)
    emit("start", contact="closed" if line.get_value() == 0 else "open", lamp=_lamp_level(), net=_online())

    end = time.time() + args.seconds
    stop = threading.Event()

    def contacts():
        while not stop.is_set():
            if line.event_wait(sec=0, nsec=200_000_000):
                ev = line.event_read()
                emit("contact", state="closed" if ev.type == gpiod.LineEvent.FALLING_EDGE else "open",
                     kernel_ns=ev.sec * 1_000_000_000 + ev.nsec)

    def network():
        last = None
        while not stop.is_set():
            now = _online()
            emit("net", online=now, changed=now != last)
            last = now
            stop.wait(5)

    threads = [threading.Thread(target=f, daemon=True) for f in (contacts, network)]
    for th in threads:
        th.start()
    lamp = None
    while time.time() < end:
        level = _lamp_level()
        if level != lamp:
            emit("lamp", state=level)
            lamp = level
        time.sleep(0.2)
    stop.set()
    for th in threads:
        th.join()
    emit("stop")
    line.release()


if __name__ == "__main__":
    main()
