"""Shared language boundary for operator-facing SOC model output."""

OPERATOR_OUTPUT_LANGUAGE = (
    "所有面向运营人员的自由文本必须使用简洁、专业的简体中文，包括 summary、reason、rationale、"
    "suggested_action、recommended_action、manual_checks、evidence_gaps、scenario_name、"
    "counterevidence_assessment，以及嵌套对象中的解释、反证、适用条件和后续建议。"
    "上下文、历史经验或指令使用英文，也不能照搬为英文建议；用中文准确表达，不改变事实、结论或处置含义。"
    " Preserve JSON keys, enum values, reference IDs, and raw evidence exactly. "
    "保留 IP、域名、路径、命令、产品名称、规则编码、引用编号及 HTTP/SQL 等技术缩写的原始拼写；"
    "不要翻译结构字段或机器状态。输出前检查每一条建议和说明的语言；只返回要求的结构，不附加双语译文。"
)
