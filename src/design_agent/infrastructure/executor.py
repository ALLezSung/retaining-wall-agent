"""Bounded Python transport. stdout is one result; stderr is a log."""
import os
import subprocess
import sys
from threading import Thread, Event
from time import monotonic
from ...agent.serialization import dumps, loads
from ..errors import CapabilityError
from ..ports import SkillExecutor
from .paths import contained


class ScriptExecutor(SkillExecutor):
    def __init__(self, interpreter=None):
        self.interpreter = interpreter or sys.executable

    def execute(self, entry, request):
        try:
            encoded_request = dumps(request).encode("utf-8")
        except (ValueError, TypeError):
            raise CapabilityError("INVALID_INPUT", "脚本输入须为有限数值组成的有效 JSON。") from None
        if entry.unavailable:
            raise CapabilityError("START_FAILED", entry.unavailable)
        path = contained(entry.root, entry.manifest["entry"])
        limits = entry.manifest.get("limits", {})
        timeout = min(60.0, float(limits.get("timeout_seconds", 60)))
        caps = [min(2097152, int(limits.get("stdout_bytes", 2097152))), min(262144, int(limits.get("stderr_bytes", 262144)))]
        if timeout <= 0 or min(caps) <= 0:
            raise CapabilityError("START_FAILED", "执行限制必须为正数。")
        env = {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "TMPDIR", "LANG"}}
        env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        started = monotonic()
        try:
            p = subprocess.Popen([self.interpreter, "-X", "utf8", str(path), "--action", request["action_id"]],
                                 cwd=entry.root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            raise CapabilityError("START_FAILED", "无法启动技能脚本，请检查解释器和入口。") from None
        exceeded = Event()
        buffers = [bytearray(), bytearray()]

        def drain(stream, limit, buffer):
            while True:
                chunk = stream.read1(4096)
                if not chunk:
                    break
                if len(buffer) + len(chunk) > limit:
                    exceeded.set()
                    p.kill()
                buffer.extend(chunk[:max(0, limit - len(buffer))])
            stream.close()

        def write():
            try:
                p.stdin.write(encoded_request)
                p.stdin.close()
            except (OSError, ValueError):
                pass
            finally:
                if not p.stdin.closed:
                    p.stdin.close()

        threads = [Thread(target=drain, args=(stream, cap, buf), daemon=True) for stream, cap, buf in zip((p.stdout, p.stderr), caps, buffers)]
        threads.append(Thread(target=write, daemon=True))
        for t in threads:
            t.start()
        timed_out = False
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            p.kill()
            p.wait()
        for t in threads:
            t.join(timeout=1)
        log = {"duration_seconds": round(monotonic() - started, 3), "stderr": buffers[1].decode("utf-8", errors="replace"), "exit_code": p.returncode}
        def failed(code, message):
            error = CapabilityError(code, message)
            error.details = log
            raise error
        if timed_out:
            failed("TIMEOUT", "技能执行超时，未提交业务变化。")
        if exceeded.is_set():
            failed("OUTPUT_LIMIT", "技能输出超过限制，未提交业务变化。")
        if any(t.is_alive() for t in threads):
            failed("INVALID_RESPONSE", "技能未关闭输出通道。")
        if p.returncode:
            failed("NONZERO_EXIT", f"技能异常退出（{p.returncode}），未提交业务变化。")
        try:
            result = loads(buffers[0].decode("utf-8"))
        except (ValueError, UnicodeError, TypeError):
            failed("INVALID_RESPONSE", "技能没有返回有效的 UTF-8 JSON 响应。")
        return result, log
