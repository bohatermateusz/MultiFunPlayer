"""Sends the actions to MultiFunPlayer's remote command listener (UDP, 127.0.0.1)."""
import json
import socket
import time


class Executor:
    def __init__(self, host="127.0.0.1", port=23555, target="Buttplug.io/0", step_delay=0.3):
        self.addr = (host, port)
        self.target = target
        self.step_delay = step_delay
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, action: str, args=None):
        message = {"action": action.replace("{target}", self.target), "args": list(args or [])}
        self._sock.sendto(json.dumps(message).encode("utf-8"), self.addr)
        return message

    def run_steps(self, steps):
        for i, step in enumerate(steps):
            if i:
                time.sleep(self.step_delay)
            self.send(step["action"], step.get("args"))
