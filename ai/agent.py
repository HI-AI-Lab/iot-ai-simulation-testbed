#!/usr/bin/env python3
# testbed/ai/agent.py
import argparse
import json
import sys
import time
from typing import Any, Dict

from py4j.java_gateway import JavaGateway, GatewayParameters, CallbackServerParameters


class Agent(object):
    """Python-side implementation of bridge.Agent with zero stdout/stderr."""

    def __init__(self) -> None:
        self.fn = {
            "inc": self.inc,          # demo: x -> x+1
            # "decide": self.decide,  # add later
            # "sum": self.sum,
        }

    # ---- bridge.Agent required methods ----
    def ping(self) -> bool:
        return True

    def rpc(self, method: str, payload_json: str) -> str:
        if method not in self.fn:
            return json.dumps({"ok": False, "error": f"unknown method: {method}"})
        try:
            args: Dict[str, Any] = json.loads(payload_json) if payload_json else {}
        except Exception as e:
            return json.dumps({"ok": False, "error": f"bad json: {e}"})
        try:
            result = self.fn[method](**args)
            return json.dumps({"ok": True, "result": result})
        except TypeError as te:
            return json.dumps({"ok": False, "error": f"bad args: {te}"})
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})

    # ---- exposed functions (extend freely) ----
    def inc(self, x: int) -> Dict[str, Any]:
        return {"y": int(x) + 1}

    # def sum(self, values): return {"sum": float(sum(values))}
    # def decide(self, state=None): return {"action": 0}

    class Java:
        implements = ['bridge.Agent']


def main() -> int:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--address", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=25333)
    ap.add_argument("--retries", type=int, default=50)
    ap.add_argument("--delay", type=float, default=0.2)
    args, _ = ap.parse_known_args()

    # Retry until the Java GatewayServer is available
    attempts = 0
    gw = None
    while True:
        attempts += 1
        try:
            gw = JavaGateway(
                gateway_parameters=GatewayParameters(address=args.address, port=args.port, auto_convert=True),
                callback_server_parameters=CallbackServerParameters()
            )
            break
        except Exception:
            if args.retries >= 0 and attempts >= args.retries:
                return 1
            time.sleep(args.delay)

    try:
        gw.entry_point.setAgent(Agent())
        # Serve callbacks until terminated by controller/OS
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    except Exception:
        # stay silent; controller can detect readiness via isReady()/rpc errors
        return 1
    finally:
        try:
            gw.close_callback_server()
        except Exception:
            pass
        try:
            gw.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
