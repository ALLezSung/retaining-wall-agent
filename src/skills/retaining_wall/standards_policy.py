"""Small, traceable catalog for the building-slope teaching prototype."""

STANDARDS = {
    "GB 50330-2013": {
        "name": "建筑边坡工程技术规范",
        "url": "https://www.ndls.org.cn/standard/detail/853bfbf5def57ba420536385d7ac33e2",
        "role": "重力式挡墙抗滑、抗倾覆简化对照",
        "coverage": "已做教学模型对照；不是完整规范验算",
    },
    "GB 55003-2021": {
        "name": "建筑与市政地基基础通用规范",
        "url": "https://ebook.chinabuilding.com.cn/zbooklib/book/detail/show?SiteID=1&bookID=147869&isEnterprise=Y",
        "role": "地基基础相关要求",
        "coverage": "未验算：缺少地基承载力、变形等项目数据",
    },
    "GB 50007-2011": {
        "name": "建筑地基基础设计规范",
        "url": "https://ebook.chinabuilding.com.cn/zbooklib/bookpdf/probation?SiteID=1&bookID=61019",
        "role": "地基基础设计参考",
        "coverage": "未验算：缺少地基参数与基础设计数据",
    },
}


def selected_standard_report(selected: list[str], result: dict | None = None) -> list[dict]:
    report = []
    for code in selected:
        if code not in STANDARDS:
            raise ValueError(f"未知规范：{code}")
        item = {"code": code, **STANDARDS[code]}
        item["status"] = "待补充资料与验算"
        item["requirements"] = (["抗滑移稳定性", "抗倾覆稳定性", "地基承载力与墙体强度"]
                                if code == "GB 50330-2013" else ["地基承载力", "地基变形与稳定"])
        if code == "GB 50330-2013" and result is not None:
            item["checks"] = [
                {"item": "抗滑", "value": result["sliding_ratio"], "teaching_reference": 1.3, "passed": result["sliding_pass"]},
                {"item": "抗倾覆", "value": result["overturning_ratio"], "teaching_reference": 1.6, "passed": result["overturning_pass"]},
            ]
            item["status"] = "已有教学校核，其他项目待验算" if all(check["passed"] for check in item["checks"]) else "已有校核未达到教学对照要求"
        report.append(item)
    return report


STANDARD_ENTRIES = {
    "11.2.2": {
        "clause": "GB 50330-2013 第 11.2.2 条",
        "summary": "重力式挡墙设计需检查抗滑移与抗倾覆；特殊地基或坡顶位置还需检查地基稳定。",
    },
    "11.2.3": {
        "clause": "GB 50330-2013 第 11.2.3 条",
        "summary": "给出抗滑移稳定性验算；本教学原型以 1.30 作为一般工况的对照值。",
    },
    "11.2.4": {
        "clause": "GB 50330-2013 第 11.2.4 条",
        "summary": "给出抗倾覆稳定性验算；本教学原型以 1.60 作为一般工况的对照值。",
    },
    "11.2.6": {
        "clause": "GB 50330-2013 第 11.2.6 条",
        "summary": "地基承载力和墙体结构强度还需按相关标准计算，本原型暂未覆盖。",
    },
}


def query_standard(keyword: str = "稳定", selected: list[str] | None = None) -> list[dict]:
    """Return selected standard records and the reviewed GB 50330 clause index."""
    if selected is None:
        selected = ["GB 50330-2013"]
    if any(code not in STANDARDS for code in selected):
        raise ValueError("规范选择无效。")
    words = keyword.strip().lower()
    output = []
    if "GB 50330-2013" in selected:
        matches = [item for item in STANDARD_ENTRIES.values() if not words or words in (item["clause"] + item["summary"]).lower()]
        output.extend(matches or list(STANDARD_ENTRIES.values()))
    for code in selected:
        if code != "GB 50330-2013":
            item = STANDARDS[code]
            output.append({"clause": f"{code} {item['name']}", "summary": item["role"] + "；" + item["coverage"], "url": item["url"]})
    return output
