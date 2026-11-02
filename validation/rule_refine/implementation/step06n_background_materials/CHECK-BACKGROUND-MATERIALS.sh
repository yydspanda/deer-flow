bash <<'SOC_BACKGROUND_MATERIALS'
set -euo pipefail
umask 077
cd "$HOME/sec_know_model"
.venv/bin/python -B - <<'PY_CHECK'
import base64, hashlib, json, os, subprocess, sys, tempfile, zlib
from pathlib import Path

parent = Path.home() / "sec-rule-refine-data"
source = parent / "synthetic-chain-diagnostic-o4olbtkt"
OLD = "1946b9c0828a38679655d67c1d28677441211b2edf576c15bda8396decee9117"
NEW = "c8e73780c98a0ae6e02611220965050be07e0dcb3b30b133d4419a03176f00e0"
BASIC_PINS = {
    "runner.py": "5560912497769e7496abc9d6698ad8c3e12562633cf4215b518fee910780f062",
    "probe.py": "508eb4db65c072fdf6de2ecc113e3d62fa2624a5c9adb1ad9be3b96b8d147c15",
    "pins.json": "75a50783a9dae5016e0cae26f9380f73cf54480492c847169175aeb0eda4087c",
}


def prepare(files):
    """Pure transformations of pinned prior tools, never the application files."""
    if set(files) != set(BASIC_PINS):
        raise ValueError("基础工具清单不匹配")
    result = dict(files)
    runner = result["runner.py"].decode("utf-8")
    changes = [
        ('                "background_service",', '                "background_service",\n                "background_tick",\n                "task_source",'),
        ('预检通过：3条人工告警 + 1次重发；隔离Redis计数；请求模型=', '预检通过：临时SQLite + 隔离Redis + 后台处理函数；3条人工告警、1次重发；模型='),
        ('"counter_mode": "demo",', '"counter_mode": "live",\n                "counter_namespace_scope": "isolated_synthetic",\n                "database_scope": "temporary_sqlite",\n                "background_tick_invocation": "manual_in_process",'),
        ('"rule_refine_synthetic_chain"', '"rule_refine_background_materials"'),
        ('开始隔离串联；模型请求可能需要几分钟，请等待。', '开始后台函数隔离验证；最多一次SDK请求，请等待。'),
    ]
    for before, after in changes:
        if runner.count(before) != 1:
            raise ValueError("旧验证工具位置不匹配")
        runner = runner.replace(before, after, 1)
    result["runner.py"] = runner.encode("utf-8")
    result["base_probe.py"] = result.pop("probe.py")
    for name in ("pins.json",):
        text = result[name].decode("utf-8")
        if text.count(OLD) != 1:
            raise ValueError("旧解析器摘要不匹配")
        result[name] = text.replace(OLD, NEW, 1).encode("utf-8")
    return result


def unpack(payload, expected):
    decoder = zlib.decompressobj()
    raw = decoder.decompress(base64.b64decode("".join(payload.split()), validate=True), 256 * 1024 + 1)
    if len(raw) > 256 * 1024 or not decoder.eof or decoder.unused_data or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("命令内容不完整或校验失败；未连接服务")
    files = json.loads(raw)
    if not isinstance(files, dict) or set(files) != {"probe.py", "diagnostics.py", "entry.py"}:
        raise ValueError("新工具清单不匹配")
    result = {}
    for name, content in files.items():
        if not isinstance(content, str):
            raise ValueError("新工具内容无效")
        compile(content, name, "exec")
        result[name] = content.encode("utf-8")
    return result


def publish(source, bundled):
    files = {}
    for name, expected in BASIC_PINS.items():
        raw = (source / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("原基础工具版本不符：" + name)
        files[name] = raw
    files = {**prepare(files), **bundled}
    for name, raw in files.items():
        if name.endswith(".py"):
            compile(raw, name, "exec")
    folder = Path(tempfile.mkdtemp(prefix="background-materials-check-", dir=source.parent))
    for name, raw in files.items():
        fd = os.open(folder / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    return folder

payload = """
eNrFPGtzHEWSf6VPn3q8o/bIAmN0O3vhA22cb702J3nZ5SRF0+qukXrV0z3bD0uDTxEmWLDBNq/FELDAYs4L3ALGG8vD+AE/5jQj
+RN/4TKzHl3V3SOZCCLOBDBdlZWVlZXvqvK5qUGarDJnMJyam1qmf+a3WOqHGbPydWaxrTDLw3jNWvX8jbU0KeLAykN/w9oM83Ur
zJLIy1lgLbAgzCwPOhf/42SYM6sPzWnoRZnDkS7Hy3HYHyRpbvnJYKg+fp8lZU+SqZ/ZHyJAM7sc99KkD0PinG3lUbhqSSRRkgFZ
oj+A2fKwz2Sv/G5b+N9nkpgJwIGXr2tYnoBP0VMUYSCb8fdDGsWrXsZc4hM2Zt5ZQJwNY+APsMKFyTyrqwE5HEJvMIARyXLsR16W
WYuyZ6GI2GJSpD6ztYEN3a255diCPwHrWRFsjptCV2ZnLOrJLvyTsrxIY2sJue0EjA3wB0E5OKC1olPhLjA/SQMWPJEmPsuyJNUm
cd0wDnPXpcFtayBB9NkIr+pp8++kyP2kzzJgjta1RDOXyNdY7uV5qvDHXp81LESA2dWZCFzHKPoEtixci73IxJcVUQ40mZgcNY6P
qKxNrsXxBgMWB7bJVo6z1apRzTs4q2m5uP+4vzZKYttKk82s3NDMT8NBnqRAXJI5CUwkwODrtPvYwvzxM9Z/8Y/53z12Uv7+7cLp
Uyefalud5GinI2iADlQRZpdYRQ8prlAfW6iZAwoWMz+n6Voty8ssSWhb/dJ4KJsctsX8Imc2V3KicN46c/xfT85bhZe5wCx3M0k3
elGy6eZetuFmsItFZtklLvwDqndm/ndnrCcWTvz6+MJT1q/mYTk42E8CRl1ti3AIQOiMWJqrTxObmIMD+ilDC4WMZ8ZYUtt/Xzx9
ChrWWAx6xLJBEoPdw8YWX1Fr8pr7XjysLGN56sSpxfmFM9aJU2dO78+AJ4+f/M38omX/S9v4B6atLGbJBhlZWp4Kg+WpFRIY+JCs
0ZoEe3QgwSJsMnEipcokwYzKXjpxsmlLk+mAyLccMPG9JAVzbrdqWNB4O0HRH2S2PiXZOKSDxVmRMtfL/DDs/hKcAWu1rVOAumUB
TiTTCmPSAZ3ClqYuXhGEue2BQg6zUGlKnnpxRpa5a8k+BwwEiGEfuBK5CgAXd25b7KLQyXPlVECxGC72ZXlqropRdujL18axNE1S
sRm1sXqnOZ7TaQ5WVP+o0flwsM9o3ts02veiqGm9Rq8+8NChcxtsWJ0Imvhuwg/czZpKZMEGqNYfCgauSmBtQ7NoAs2kCWUj7pDZ
pqajxpp+gOSDe8rWYQ4PYwkaU2Sg0vwni7xBhhjFvMUgywG074I3Yf1BnhkYW9viY7sUwrSIXX+d+Rt2EKZgJJN02LYOtS0gBz0v
qBsGP64fhWBF2pydz4Dd3Ey9gav8Szfy+quBZw3mrAFYJTAkXdIEafwlahBpjErKuVpgcAYQWRUZS20hyVnR73sEawgzUYlbCpwE
0oAlwBvmltFHGcRxZpRCD2z0woiJ9r6QyOWpKDxr8hw6RWjnJmm4FsYcrpyjF27BJgruS6PpZuAnBUpke5IC+S73PhX0qwV4JmCZ
cpWlpHYMlLCLQdmsFFKD3kfoaUyDaEpsRZ7AOmE5YGqjIbaT/TJQAgERxEZrKVIrPChyUMLijjCwiEMX5AM8q7/u5iTyzejKzXEx
wgbMZxMfaEgEi8HhFDShFCrOYh9GwIa4GAjBLL7BaxmgazKAJFRYDsHNoAC7HaY4CvSjlD7HWwUU6OJbLZotYkDGwE1Izs6kBSsV
hjx5rxRlB83qsB+F8YbdssBEaGgpr8igGXMGO05ycxh88CHoZLUeWCh1wZ96iHju0CGhGG0hz+h9QMbXBDfKdXJ8LszrohEYgiwj
v70I3aVYChg0UG5ib9ampCdrK2MJzg2gC9oeiiXRP0Jsy/89t43/cjQQYYNDDco4ysUgC8YIITlF2Ql3a0M9xlKr7m/gmlGAu53k
kQ4I6MBLwdZkXeI/z9FgR+iz1YABJiuxgbQm0Vlma4AYLK4Dft3A0Ux6FAw5jd6/zBPHaWAveILfC9Mr+K+NE8sHEpA6M5xCxhmZ
Eew5CBAaR0gtiTMWAybxQCNKQN9tYXUd5bVcFqOLtH0KVmeOHLMOWTOdIw/p0TggJRkLwbQAwTEkWTgd7Eno5yRnuIHY1LL+CY0q
2U/hkyA84b8G3pBoUBKi+JfScpbksBUZOlDCJmIQ0bYRovGlaRqtZg0zakAD7UsgniBpmAHSAiJIFlQ7oZ813XLjQkw9Il3yMPV/
EmSbzWMEAQPPrKeMlTtFARvprbCd8JEy+gDxCIyguS41Cs10GIMyKskh5u83EFlrQJfsbojSpZKVOnCYXE+WT2MYnjnS95RDy9zM
QFJORvxbMZI8L2CYrUmh5BE+7bpzEox39FsR+5+BDpHdG8h1ZIgBcDVVBCYuVxh/jQTR4lBFBlE8xhsqcZkZs5BtUc7eUq6kq+9X
6Z+ml6esn/Eqid1y1tlWJSbrQ0TWL/ou0dKdbfOQ0fPRWILDzdOQZdBshPxq47kFgdUqKmDJsEC5sLJZ2wchh11r6ZDgVSnjK23L
zNbrEEudFSyIVPEtTc+sAGAGvhYpp1TK+hkq7nTKBkW2rotPsgoB2llyCESIho+SHY6TEh6BvqJ5MaZZUfgMGUu5nSK8cFQn8M9n
sE+BS8mWXc3JFPVtLlEYnsoBeXdyolcVAhNvq5Kv9zRql8o4coWbNdQLiJmkWTJAdTahLOOImbmGVdTtkNIMndfoKJTnrpCpg3Wt
/cgAATCHovN3igHyy5aS52KjrY1sTZ5PFYp0cBOaRxZlQYlPwuHZpGnqdtGYVNpHvVEfTbUtTZlwaqwD6fAo9XWFlMzIitV+CKlT
0MWt5WsA4SniEKROaDxvFdrjFykGK7yrqRZRKgDbdDmFAg9MbYOR7i9hFhiurTFyLyukT9iOyqQoCIpBFEK8zFwvCIA8gwhqMk0n
yXBu2bgM3NiW1UWfiX5Nm1cObJqTBtRXIzAIK6YzhgyZNok0dJV1C7CZg1BXVozDQBkorOSQfLehDRdaxplVjIJTR5BRQiAEPagZ
oN2cNm7+ul0JP/ug8Py70tFMCm5GCe71B5DFyjoWNxfIvweIWUpbQdTiCYaXgYxz8yxz3f0jFaKjDG1lXVgLNrAEDy7ExdDAloJe
Lzmbfq2awJJLmam6Z1E+xgCDIl9ePWyaqEqRKEC4CYaK6hymSg+If0lSNXlemWiQF5CZfcXmBTag8io5j8haYzFLlVlGqQN8ySar
m+Z95m5kB3eepV9U4f9qEUbg2iQSAamtu7Zs5Yl5hvFjfY/CDHlKjEtcZZLlDcush7F8diVW4vvAcTx3nc78ddb31OgaO46LH4+n
Xi93eNEDgV0+0G4174O07lqVhMpTIPbVGgkvW1X3R5ypCEZUjkfKzaiMU2QrT7v/qUrjyYrsUKU2TUyExRFdYAWcRWpR51yV+MlM
F8phyGuu6YhW1PLmHiRWr7g7U5W7ynqoumFXV9/qwUYYb3Tl0iLML1xs4qkGHuH1+2COSf8WsehirobiQCp4grfkSYqL2SjEfNUj
iiQ1Q0BtCyCU9zcoRK0fGtrl2WA5YK3wEAgGmAVRW2BqmdYL6yx2zcDzPZdLr1TKHKzQ0jhzwSjyOCAlPaONEyGxCH+64v/aoWZX
kKuvGCs/Mkzj5JlhBAFABGkEwl0MhIFOCLTB1axR0c9c1P546xaAw0vNp6+D6jNCeqfV6aUazXmvjjU1RL0wxeRBjKaNkwusj4NG
4kClmVdtzm1PjCJ98MJFn6Xi2KVLk+r8UwRQcMGBFEn7nMxU/mhIyvOQGiLjqASzFiLngHOUWvC6lfO6raCWM23GlArgFYY4fPOs
X1gznFNkaiei1qWoq5NmildL2zR3ALzFWbpLIpBVXdVglmjZJypPScsh/qqxTfVgHcnQiNK7azDVoEIaf/16gEO2hwIeuxlJSwsZ
amEkOC/IXqngqYjFiEs01+DFGVADBaYlKc+K4LOZLuiw5TxLaoSsGQAOx/T8Fa9mVJO1cqfA42Ak6OIdGJvKnSDQYLaLvDd9DJm/
pM5+ZOmxxGccgU5QR0m4lF1tIZpG0hTinE0H0dsJUATufTry4NlCtyRIUMjba6Ij93ZuX4KrB8OVWm/NnDSZ41oWZiqwGhUGUa0u
W0/G6qonECzNtC2wkiZ0Peh9kISv4byKD6wmhrUM40Ds0qJUj8NbaCLO0fIGSeaJM9k4cf11L5ZnrCEY814v9LGY6LKzYQAyKrpi
xoLMlbfI8JQM9g2LaXL8djNZE229oEcWtSQ9OR3UQTa8ipu1PTn70jaXKuHcJHJotuUzsAbz9D9URQ88GZr/ucnlEK4w+vGp5mjw
PzZ9txyXTuhcV4hqLwRWR/qBz08h/JyKhvKjOAYAAwfZGq93VMMR4/SpqU4MkXnEYH488q9DUmalAYsTPgSea3YunFgZAdWxVrej
Ac1+KCpcw9o/VfBcEa10VeUHTJs61uxiSsfRmkaFt+2/Mw3SIDHzFiKg24TePLjQajYPtE3GaKeI6cy1H/LIp34yWFeL8lBeMx79
5CyPF6qHdw+qLD+eTUCrOBedTJGEKC8IiME0/X5KJ3gtziQfiLP1uFrUbWUoLYqBTaojrihp9zl5G3fk6qy66Uy5haCR5+PVuvGL
l0bf/HXv5nO7b3wyfuvD8bsvjr++uPflt6ML98ZXb3ILqMPsvnwTYGRSNHr15dErN/fu3dj74kM+4oe7f9659z0CXvps99NL468u
7X1xdfTavdGrV3b/5/b9t74cvfLSzq0vx299zS/z1oKVZr4szD9xeuGM0w94WQMXiru4lU+UvnrJTcjJ5OPkn17yvDQPe56fuw9m
t0UqKvAux1PtqSD01uIEHJyf6feoT8dsWrgxSwMhc4xXq8sDVQqg/hl8HqPav0WnGTwYzPCeIWGu3aNe9zK8y3zgVWrI5dWt6mF2
4EVoXHsm239NGfcZTH5iOXIYEEUS5ElFLNXKEO74qeMnn1o8segu/ttxcrP+MfbI7CPHOv6jx7yOx46yzpGjMzNHjnQePfpw5+HO
Kus8wjqBvzq7OttZnZmdDR56aOZRrzM788jRXqfDOrjuhfnji6dPLZZ3npanRn9+f/fzN+6/e37vo2dB4nevXsarm+M3L4zf/UzV
LyTY6NVP71+4Mrp+BWFGn781vvFVHeb75+9fu8OVY/zBhdGFFwjh1Zuj83frwHevAtjO7RfG7/5t9Peru9dvjz/4pjLzndt7N27k
ySD0d269vPfxH0cX3965dWXn1vm9C19qcOP3ru3c+Xr37u3RzdfGL54HzBzV+I0vxpef1QGpWcx868rec/f4mnSQN2+OPnx//PLr
99++zucXDKI5Rq9frlF5/733OeDuS1+Pzz/L8Y5fvT269dHe9xfGd67zodqIvWuf7H333ejuK8BUAOdk7r7zR2Dr7mcfNQECovtX
v9fJvPTZ6JVvdu5d2b13A1F8/t+7126ML745+ury6K9v4bzf/L0OfuvK6PK395+/AoCCWzXSvn919wZSMXrxygQQ3qwAcVJ9K2+8
s/Pti5OXjDTcuDy+qu/f6PLzwInDRB7+EvsJDDl/l7Nz59ZtsK0602lTZvYu/GP2yM6tv/ERP9y9PLr4KdhhLoKj6x+PXngbZfWL
K6O750fXv+Q8hs//Pf8siiX8vvjmzq1Lo4uv797W2bt75y97N67t3vnT+L2/gCFHo06yPX77nZ3bLyOyjy/x9rp0vfbS3ucfnXh8
knxxybx6k/cDjYCQT8dHcp8kwSs36YSG3QLhem508w3gTw+dOQ2AVOCXJ+ZPPr4oLlRJFOLKNEQzBaNyRFnmtHyW5l4Y50OreloE
EXaEVyNI+8BnoHXFY3y63YAmFpAlAzyuAHsskxZrM4yDZNOlW03yAyYTd8bJ13OP74JXhygNiBl48ZBfVKdLl+Uvmki0xb1wzYV8
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
"""
expected_payload_sha256 = "64c1c37292983e1442491497d3d446e6c418b539b82ec9262fcb24458b771cec"
try:
    folder = publish(source, unpack(payload, expected_payload_sha256))
except Exception as error:
    report = {"status": "preflight_failed", "error_type": type(error).__name__, "model_calls": 0}
    if isinstance(error, ValueError) and str(error).startswith(("原基础工具版本不符：", "基础工具清单", "旧验证工具位置", "旧解析器摘要", "命令内容", "新工具")):
        report["reason"] = str(error)
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(1)
print("本次验证目录：" + str(folder), flush=True)
raise SystemExit(subprocess.call([sys.executable, "-B", str(folder / "entry.py")]))
PY_CHECK
SOC_BACKGROUND_MATERIALS
