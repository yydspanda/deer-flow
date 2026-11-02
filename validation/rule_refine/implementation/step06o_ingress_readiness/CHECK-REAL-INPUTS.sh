bash <<'SOC_CHECK_REAL_INPUTS'
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python -B - <<'PY_REAL_INPUTS'
import base64
import hashlib
import json
import os
import sys
import types
import zlib
from pathlib import Path

PAYLOAD = """eNrNPF1v40hyf4WnPJw4J3Pkj5md8a32oLE1u0489sD23iawjAZFtuw+U6SWTdrWDgzk4YDDPeQxL0EegjzkLUHe8o8muX+Rqupu
svkly7OXQ2aAGYmsrq6urq7v1qdecMODW2+56u07vSn9PeN+uJXE0cpZpnzpp34mktiZJ6mTcZmJ+NrJbrjDH4T6cnx6MD524FPK
pfy1E/M7njoyny1E5vgRTzPpKbzTeBqLxTJJ4bnMis83Wbb0gkjwuHz2O5mUwIksPsofI5Hx3eJ7JhZ8Gs/TZOEESZzxhywSM0e/
DKJEAln6/dLPbqyXH+ErEjSNQz53Ulgykws/ivoIN3AiAeS7+9PYgT/3Iruh8V6y5HF/2ktn054Li3BkBiMXGgz/pP69M9KPPcTa
J0zOr5xtV0GJuRMBEgB0nW/UPJXxQnLnt36U80maJilMNhcRZ1mSsMhPrzlMrKBTnuVpjBN6IQ+SkANons233hCEWRgNljzDneqn
SZINnNhfcLOyLF1Zk4d+5gP1yHwvSvxQ9i2+4GDnpTPtAaPn4nragy+IauBsv959s+e6JR5YYgzAArgvMz8OeB8xD5xQBJnroByt
luqZd80zoJvH/iziIVDuwigaPEuSyCLNWvGnaQ+wZrmcgshOeyK+8yMRsiRlCyFxw5mEDQtupr1Hi6+tY3F5097AKShgScxCIW/x
NdJ3adF2pdHxh4AvM+c9cPYkyd4neRzSVu0/NZsmr6BLI+qfntP4gbXt7pPI8hiJR9Jw5ZoJhNps/U2S3Jr10O61bzqIKodNhxPp
wVmXvHXPJU/vRMBfpkme8VS+vE/S23mU3EtQHMjAnVevnRfO9nCnIgdwotMV4I7hXParexmDwFafoH7Bp6BJiCZvloSrKgjIlSVT
CDwgut/ncYA66pDP4VTGIeHxUDid0QioR0pZ5stbpuhHVWRwWuQGsGQJ5F4SGTY9OMm9H932aUVuJyEHgMG9KjHiFiDC5tq9SMQ8
TrpZQMQ8tXxvDitXU5/gqa7CF5xAKE+EihnJDDeTM7OFLOU/5qDXbaZYSwhAHDL+F1rEOMtSMYMtWrsSH6DUWhRxtBRWbHLHQopj
VMFc1yVyyYMMT/ugDkfHacZheVzJEk0OUodDUVvVJLzYfyRecxE/LkTcx+eu8zV9Vq9qC3Yb06d5HKNuW6ZJAHaWocZnYGnFXCC5
+857P5LcGva0khk456s48x/MlyxZHsHpoEVtooD8O19EqIGUCt2MQktB+UvBJLgfC78PNiXmdIbZ3A+yJF2NLLfA++7i4uNBAWJo
+yvnWCC9kRMlyXLmB7dOhiYSbVwCvkvysKJPKQ9FCkMlffOXy0gEtMiXfg6GXTkE4KQQUqQEpL1JEJip7Z2vvCH83cYFvxm+HQ7I
AQGdMtp123QrYvH0+YLx304uFKteoh8By/fIz4FHN6BzQa+OgMfjADdM8dimVYE+uva+yGUSS67JRVtqHvWrYHmUARAgR6aychcN
uKceIWmFWiAZD5MgX8AOVDfQOss1DM4vRs7OcNhhuF+8ULQMasdOCQEDs88smXqse1XFXORX7WmDY/4rPayml1WD/TLyLBfsca3P
BHOW72GntQM9ciyPB71JiTv/6dHVjwqj+lJpMfvdMkEFvdbFKibSftYzF1ndd9wKe+8fn9KjBdp16lUz0tYbdfB14neR5rwxYJYD
C1DdIIvA042zNmWoYPG4A4A+UQyjmefpzrpOmhAA8dxWq4UZ6/bl1nDJUqyWa8eM9D+TaR1Lm4sYbHNDV2HIRMrD6GjCuAADlQpA
VHEjUYTxVBofMfblS5Twl5IH7DYGchZgASJPxWzGKmvBpWhKSIbxSf9pY4N4Z77khYuPAqosZymdVd1LQZsOAWumWUeRnlbyFPKB
UpFJdAfEeL5keSr6LqiUae83uIhRmuB5hKcjEsJC7e9YR5ICwnBWO3fhzOMPPABhgGP88Wz87YexA/YgXTGMsEenJ5VjTSNAu6Fj
BbzFabxFAotNYhEQRbsN9BDdoc2l8JvdgIsBMXc/8hez0N8HJyzrN7B8U8zhQvQ2HA5rFKTg2KO6skhvuDbT3vnkeHJw4RyMzyfO
D99NTlSwz0ghHp07J98fHzunZ/bTkfPLOI+iXzoXCD50JscwdNuZnBw6ttNWTvH+7PSDk8N+gCFkVSnX9gYmPpugTSd3Y/QbZwzI
CEqE8K0d7enZ4eTMefd32i0LkTruHE7ODwYOuMn4wTk++nB0AbascdzwD2zlTzyXypirBfrXpHrqjpvrzTnEohhMuc9TAzpeuQfN
BjtLyMng4t5wONN44sAgJeBjM+kvlhGXLapBvWFwdoTGG/mo99jOkOESmEU9K6ZsQWS921emFT63uKlIMB49OKExw91mpQDgUJkv
cOjl8IoiBfiIgUI7sqW/QpPKKLUTZ5KpeL1LvRtw0pciWz3DPTYaYbNAvtBGFCrPQVnzauhNOTWdbuHxnUjRQqhTD17fqHR8jeaz
fLS4XBCh0ZmKPMLpQG1zplNtNDvZvsp2qawGaZcWozntkVJmKjKD98Pa0BCUcsfLwtSqrB5TOT5tZ6qgAY9wsSg0EhMQoGFhMS2A
loerAoZ8GfodpotWFhXL98lRRt9HsQhNgjHpbcQYK7bJwMfCWPnxqm9nNGgzySu75Ss8/pT+hIPv5UvwwEC7/mJUPi1HorDDCBR2
Snjdoc5G66eUyOTkt+zj2en7o+NJYRDazGKnj3orYPqQTh3phMIqKnhP8dVaChgEAXENWeBRS5qwInMe4dThin1QfXAjVlIb8qfR
GPBOfPKGg8owtCVwglMBUqFV4Ki6G/hH81RvTDMVUfC8xQ6cfX88YWeT90cnE/bh9OTo4vSMTU7G744nh61qvzri4By2bHzx3Qag
J+MPk/OP44NJA9b2H6xX9Vha81QWKm1UPxwyydOAM8wsjJqZP3vHQAEVqqhvvyncvFGb16cBXVuqLkshvHJGZQJFnWa0PKXjV4zA
bCCYb75Ug/r2UZVwVIOMmdoCHcuCEPs4qdBTISwIBYQ6XnrCDA0c2+XR1pTmUlCFqsDYXaSoGGqrV7l3okCpfSCIMUw5MqZyUwyw
iJgxOJ/aX07RGaNAMcwXS9lXVgILEV5wH8JWOIn0tCC7aDhknnLmy0AIvd0gySEegx3MssIqyqWzZhnnSO0FFWuQsNC5Aa4mKajb
yFF+wa9rCRGTC8EcPWl3cLYh5lhfvwnkXVnL8SUWWYrvImkv6SxFcAuqz3y991MUd1mUcqJISZE05ZqDJAcfAGIrMK8+MB1D3PWF
nfPxh4/HE5LKebn/O8Od16heQDJAO8C/2kHaGQ695S2JGOpgOrClcCpc+gRMe5//+Pef//mP//1Pv//Tv/7+f/793z7/439+/of/
YH/6wx8+/8t/KSzrQWswa8grIGG/j07+GvxtUFHn341pWbu7r7fnO2/DV9tfDfnbveEbPtx7uzfb4TuvXwdDvhvszWZ727PdvbfD
nV0+DL7affPVqzd7YfB69iZ49YaE+sP4b9nHo4O/IU69ruVJpvH4+Pj0h8mhBmHfHp++Gx+fl34KZTLi0Neu8CGcrvcpyFqp1GsQ
RyDAD51vz/z4mneBQLiWcm+u0D81mwIWKDNgcqS3AMtzzVM9zzswP7cf1KO2wQzkSZaj1SCWx0ps2QyHV8bFcKBXak74CEcy0GNQ
Qmf5fF6bR8GzZw5QICFWzyovZ7mIQF1qOiWcZt5F3QLOjvDT1NfIQMOBdy2zNA+yTgqfN0jBAB/VAOu1da4VkD7Va/YPRIFLD51t
PXXM79l6CWkO2gw+ReFrl0Pt2geRL6VzBnYC9gvcxUIAv9eSkfbV/17xwPhwqhYbQyyDOPpg5+YDcMFC8I2qFVlt3PqVdyrXFzvt
x7Ge6aMScpUQUK6mnJyDWVmipgRvUcvzdZTM0Lw2I1QARZ/Ws0ivEFaGPDOsg/KQgpK2GjqsidIs6C8AQszViZ/4z6qA/z8vy5e8
EfHvONYQIHSZJ2tKsYrKCieL7BoFtaoSoNCBbyjAmGertyrMalZi19V427oGrAFYbMMUUAQeQ7+ofiIaG0rbYgT89FgNdUwFkBDt
N0qAKM8tLnl3UbUJi7W051cS11UT/TBksJeUPK0njurpdJ0xBa2b1+vYkX8tm/VSmji99u5QhpqvkGfw2rDMg8+yhT+V4inAaA4l
9Chz60wpJhyg7NdYcVVPO9JmtpfrldpsPKIA7F5N0bWyIhC7LxYHMdl9koayHRgWCTiRGbQrmq5pr744M3GNB02kbkt8dpLEvDsU
02RMe1tbAZooTL3FemeRCtW/ogijjhVwFVoExJyPSzR9hAYDHrOgttnCdLUFsZ8133q0AM8AniVUhSgzhTgPZn7+fOVveeODakE4
7eN76glpTO+GP4TiGquczSzegnI/EMOqCPUnsdwAC+697fA2sBomUKeO/twAEjEmudlcPGQYShXs2XeaiYS6/tF6BCKg67hDibQj
qSHKrP4MJTxFK8a78fmEjY8nZxfscHwxbktWm9ND5j/2VH275eC47SolLnTwk/0FQFqyYCKk7N+XsGpDPf0MJd0YZh/4loaXQrlb
wAWz1QLbeGwNKY0BgoumHXg+Y2WQiiWmNKiSskEu+vmNZZjAnCO4DgA6Znx2R8gZIEgxA6DqmOhZ8S6CnlYrqpdC5cobXCK8TEU2
+0q/0iPXM1mVL2RskZKXd5iE5ClHcUKvcWMfTHmz26+rwXHFWcbuDWzDkHfeIYQGZ/SgLxLvHCQ8vj46bfG4tiR2Ubou2WYYRKXF
Zokf9jpLliY+pGQqYpn2Hj0hZT6TPOsrAsBL51GI3KIEzuWV2xoZVLzYHIy90vkMOQQRWr7A6MyiQwbgXqNHo6M1u5Bll3CIhqar
h5ZWl3gccgwz/KjzdHplbvONtdA236tlKaZQTjud3Dfqqmodl1R9Kma+GjjquzXflXvl/GrkbD9PynUvaUOvwp6jq6NLX4oIpZpk
33X/jHa2kTNW9nZtEWygubKBZkDZ3kAPrO8Re/qYA0koMVYHAqaLRdgnllnRJA1WD7UHRoqcnjjfjBy7CUk9pABQDynQWKbNMjet
TjOi33a+HlGgqGeGb6+2d6oweraR+uDh6V723RYYOMCUFEb+9LuLPpZjrZ9UOWSUFWZYGUhLwReNapaHuilOBXU7VUU2wI6t4XAa
l5mKO2BF1hcZX2DCdZndjIY2XXESU/2mirkePW5VjhBpM3z8tTPE005oIdLe3XlaScEJpVIQU/PVG6GsfVMk47Y1FRHuGr7H+F4h
2kyvrJu9ZC8u18zggY3pDK15hCQDFEotKUfTEK+Iw2IVtpNish9YO48Sv9HMVU65u1PHXGdGH1PlA4rk3Se40hasfyFT2ihpa0uz
ynSgYm5EFCIDiIn4j+y3DWhvgCMULVu/wUrQgdK1yPax6kDQDEpwq92GTVBaSjcwlpw6OFFhwv4XztGC/zlLB+ZqEfvaGX7Z+VSD
FKFG5Zb5MHIIGeXy+/SvMUTrGyKeDlbRsGdYuLM6VWgCt9oSQH4O01G5BeZpD6gKjt1scekc7Tea4bEI0eyCxwsIqt6vqjsitDuG
qOxj6pHwkFpwrC6/AlDBNA+YwV8h3Op9ryyhYFfBmlrnh3/PlFcCHrVKD7f3YJildMM9Vjr9KtRhy18eix9z0notnDCZ7sqoZ/RB
4LWYILmOxU/o0ZoN0xRJziFWzSXa24EzWzGSOVC04EeTZURfGv1x9Z9VauzDI3hS84DDXFVOOdMdc0PdXAlrU06AWclly1KvPAFW
9HIfrO/Vfr1d+7K5Y3WntKpiLY+lXckrj6NpmUDv76xtj1auEXLosuWo16lanxytxlp198xqora9tEKpaffU9kprIeo60k18oLTd
5nQ3LY3mdZs9K2ZDvZrMMFHfPldzniZvOh07S9E/myeVrUQq7Vk2pVW32jnGx1UxW7XFok4qvYUReqw9BiPAqjhrIHNnkFo0lNdb
rAM7IUSG98tMiEC4bLxH+kphNW2Ob9Yhnpe4tYvWxULTjWy1aZuhm3KyGoLYKlYrRFEJAOwYwjYra8AqAdSlPeiKmtUKiuOuYkd5
hDQotmFTWnfTVdY0JQzBXpiSzTHpZjvpAF89PwwLGOvdjaCSk73b8Og4uX7ihgSO045w5wL1LgIoi5Jrufl5EGlhQKrpEUCF68PJ
L/e3q7q+k85uZ7lDr2I2Y+BgEsM6D4ColmcBG9Z8iaMOVKalpnGRBYCvBMWW4TP//phYs9blx6Ga2SSFaILwGdqb7cYNoapiAlcE
QVmW5nGgukHbtqF5dApWtCd5caFPZpotwruz/lV4o0xonck1bTv+D9uO2NS2u08XFEmKSOr71oa6zWtNKEztjCyYaCR4HROLuxgw
bbcdY5Qao2Y6vTObngrlaXUdY+QSTo1saiPB+GeX+PKqDQswCQutV6XXVeHbwKg2wSWpGPJWLZExM5hQc4Bh6EhdnVDhl9PfMgHy
5fYVYsRPxWa6uLX2gSalqMIKoEylPucmg2rifbI5JhtpE2xVZOGx5y+XPA5rIv6prddUH+99rQfaQKz85j7N1gqlfHPd4G0Co5KL
busglTjGHwVgutaHIy1WdI7S02lTsl8wzGZjhWsV/oIE1jA/Nu51NzO3LTfVquvG2MYsnaxSBbK0ZIUxNAFV1chV8dNJYK3sVe/c
WhAL+6gOHh0NA2sktgHNtocELrUUVGM/PMyKa4pQjGjUCXc7gkSaRYVUc5g/Y37GFgndGxk2r4joHkZ901XfbVibebYZae5tYK8t
mF8s0HSOtSo6unOTempUiaaaQqj60539R2UH5EY/N6FnLRp/yIVG8rkO7hf+A5utMrUTJfLHDUpMJbRbb+yuZkJ+bqmgoM+0PLWU
LUyTm1pc2nKXxL4JrNt6qa0O6wPLcBrX7uKZJl8voENsvjYyfAWcFMjquYgw5AbhvI6TlBIjh3wJUT41Q/2ggGvGlWJuYNnaVj2R
eO+QD6o457oUefbXepA6VbUMvQJf9z1btXH1BAVdcibfXneyPrYlN8zYFy/W5cp+bt1Yy7JVPq5iLK63fmnltzii/ycF4A1ltHIp
q3arYUB3slko0hEamSL7iF13I2olV+16Ot9DPyOQ0G+aVK69Jqm4xgu16oqeQVk1XYTMvHLNLdyI8k62C2OyekUbus3z5F7d46eE
I6xW/QAP/SaPuSFHKlgpTvMLKKp9/XFdqgELr/qKEF2+kPqiK2mKUb9cufrRH7emR5sZCV1DfHIaUu62V4gsMa4PgBd2XJfuB2U9
ulrSp3sUNwkcElf/ZE2whRZxS1092qKryWQiPbyx0Ot0EDrv3ZXXJ5h15eQZV++M7VgZ02phVMnU+s21PE0xK6z8khSN5YL7eC2k
w6jeYcaTM+xZApzFBek20LX3AJ9x1Y82DB/Th5o7ofpe8W1LD2yNc3ojGe3OvrXjTRZSNd0y/7Z46rsaRkeWfkPv8X8BXBmupA=="""
EXPECTED = "5f692d4e08fa82938ad1e580406db62b45954586727ee01b0d99fc69eeb055e2"

def load_tools():
    unpacker = zlib.decompressobj()
    data = unpacker.decompress(base64.b64decode(PAYLOAD), 256 * 1024 + 1)
    if (len(data) > 256 * 1024 or not unpacker.eof or unpacker.unused_data
            or unpacker.unconsumed_tail or hashlib.sha256(data).hexdigest() != EXPECTED):
        raise ValueError("embedded_tool_check_failed")
    files = json.loads(data)
    if set(files) != {"check.py", "materials_check.py"}:
        raise ValueError("embedded_tool_names_invalid")
    compiled = {name: compile(source, name, "exec") for name, source in files.items()}
    modules = {}
    for name, code in compiled.items():
        module = types.ModuleType("_rule_readonly_" + name[:-3])
        module.__file__ = str(Path.cwd() / name)
        sys.modules[module.__name__] = module
        exec(code, module.__dict__)
        modules[name] = module
    return modules

if any(os.environ.get(key, "LOCAL").upper() != "LOCAL" for key in ("env_profile", "ENV_PROFILE")):
    print(json.dumps({"check": "rule_refine_real_input_preparation", "status": "skipped_non_local",
                      "read_only": True, "business_alerts_submitted": 0, "model_calls": 0}, indent=2))
    raise SystemExit(0)
try:
    tools = load_tools()
except Exception as error:
    print(json.dumps({"status": "tool_preparation_failed", "error_type": type(error).__name__,
                      "model_calls": 0, "business_alerts_submitted": 0}, indent=2))
    raise SystemExit(1)

print("开始只读检查：本机接口、注入脚本结构及200条小样本；不提交告警、不调用模型。", flush=True)
root = Path.cwd()
try:
    ingress = tools["check.py"].check(root, os.environ)
    materials = tools["materials_check.py"].check_materials(root)
except Exception as error:
    print(json.dumps({"status": "inspection_failed", "error_type": type(error).__name__,
                      "model_calls": 0, "business_alerts_submitted": 0}, indent=2))
    raise SystemExit(1)
print(json.dumps({
    "check": "rule_refine_real_input_preparation",
    "status": "inspection_complete",
    "read_only": True,
    "business_alerts_submitted": 0,
    "celery_tasks_dispatched": 0,
    "model_calls": 0,
    "redis_calls": 0,
    "application_code_updated": False,
    "ingress": ingress,
    "historical_materials": materials,
}, ensure_ascii=False, indent=2))
PY_REAL_INPUTS
SOC_CHECK_REAL_INPUTS
