bash <<'SOC_CHECK_INGRESS'
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python - <<'PYCHECK'
import ast
import hashlib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

expected = {
    'service/io_models.py': '35c9e663a03e5e4872f412d877c27994db26edfba882abfd6024c6add08735f3',
    'service/routers/workflows.py': 'a0a3284aae432eb7a83f5503e739b4ec7792bcd7d83ccab4386c40893577747f',
    'model/db/schemas.py': '262349b86f5e7bc292c2b479890bb09ac1a179b93eccc0d382955c92f05158bf',
    'model/db/orm_models.py': 'edae349b1185643725b90944f64bcae98fabbf75f116dd73508c40f4a5e5fc37',
    'model/db/crud/workflow_task.py': '3c0d0c54dbecda7f1d58527155e702dc563353b47fcac08de98251ec5b3e13c5',
    'model/db/session.py': '952530bafc635c4de4823908a0329af5d418592ef1c8a717f7b243f3b4f134ee',
    'util/redis_client.py': '07765e6bfcf2c6b406ad0f59d7d78212d3ac585ee48183a2ab9efd7affd38a0e',
    'my_workflows/zeus/flows/alert_schema.py': '20b437e308c4ad6d28e204b546208adfe212004cf0717eca48b13fdffa072ec7',
}
wanted = {"WorkFlowTaskInput", "WorkflowTaskBase", "WorkflowTaskModel", "HitLog", "Alert", "AlertData", "AlertRoot"}
for name, expected_hash in expected.items():
    path = Path(name)
    if not path.is_file():
        print(name + "：文件不存在")
        continue
    raw = path.read_bytes()
    actual_hash = hashlib.sha256(raw).hexdigest()
    if actual_hash == expected_hash:
        print(name + "：与源码包一致")
    else:
        print(name + "：与源码包不同，SHA256=" + actual_hash)
    try:
        tree = ast.parse(raw.decode("utf-8-sig"))
    except (SyntaxError, UnicodeError):
        print("  无法解析源码，请保留文件供进一步检查")
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name in wanted:
            fields = []
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    fields.append(item.target.id)
                elif isinstance(item, ast.Assign):
                    fields.extend(t.id for t in item.targets if isinstance(t, ast.Name) and not t.id.startswith("_"))
            print("  " + node.name + " 字段：" + ", ".join(fields))
for package in ("redis", "SQLAlchemy", "celery"):
    try:
        print(package + " 版本：" + version(package))
    except PackageNotFoundError:
        print(package + "：未安装")
print("只读检查结束；未加载应用、未连接数据库或Redis。")
PYCHECK
SOC_CHECK_INGRESS
